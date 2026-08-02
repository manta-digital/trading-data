"""``mt update`` — check PyPI for a newer release and apply it (slice 909).

The helpers below are pure: they take no typer context and print nothing, so
they are unit-testable in isolation. The typer command owns all I/O.

This module never imports the database layer and never opens a connection —
the post-upgrade migration report is a subprocess call to the *new* binary
(slice 909 D6).
"""

from __future__ import annotations

import importlib.metadata
import json
import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from typing import Final

import httpx

from manta_trading.constants import (
    DISTRIBUTION_NAME,
    PYPI_JSON_URL_TEMPLATE,
    REGISTRY_TIMEOUT,
    UPDATE_MIGRATE_PROBE_TIMEOUT,
)


class InstallMethod(StrEnum):
    """How the running ``mt`` was installed (slice 909 D4)."""

    UV_TOOL = "uv-tool"
    PIPX = "pipx"
    PIP = "pip"
    EDITABLE_OR_SOURCE = "editable-or-source"


# Adjacent path-segment pairs identifying managed tool environments (D4).
_UV_TOOL_SEGMENTS: Final[tuple[str, str]] = ("uv", "tools")
_PIPX_SEGMENTS: Final[tuple[str, str]] = ("pipx", "venvs")

# Only the uv-tool path is auto-runnable; every other method gets guidance.
_UPGRADE_COMMANDS: Final[dict[InstallMethod, list[str]]] = {
    InstallMethod.UV_TOOL: [
        "uv",
        "tool",
        "install",
        "--upgrade",
        DISTRIBUTION_NAME,
    ],
}

# Single definition site for the command text shown per install method (D5).
MANUAL_UPGRADE_COMMAND: Final[dict[InstallMethod, str]] = {
    InstallMethod.UV_TOOL: f"uv tool install --upgrade {DISTRIBUTION_NAME}",
    InstallMethod.PIPX: f"pipx upgrade {DISTRIBUTION_NAME}",
    InstallMethod.PIP: f"pip install --upgrade {DISTRIBUTION_NAME}",
    InstallMethod.EDITABLE_OR_SOURCE: "git pull && uv sync",
}

MIGRATION_POINTER: Final[str] = (
    "Run 'mt data migrate status' to check for pending migrations."
)


# -- Install-method detection (D4) --------------------------------------------


def _is_editable_or_source() -> bool:
    """True when running from a checkout or a PEP 660 editable install."""
    try:
        distribution = importlib.metadata.distribution(DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError:
        # No installed distribution metadata: a bare source checkout.
        return True

    try:
        direct_url = distribution.read_text("direct_url.json")
    except OSError:
        # Metadata present but unreadable — not evidence of an editable
        # install; fall through to path-based detection.
        return False
    if not direct_url:
        return False

    try:
        payload = json.loads(direct_url)
    except ValueError:
        # Malformed direct_url.json is not evidence of an editable install.
        return False
    if not isinstance(payload, dict):
        return False
    dir_info = payload.get("dir_info")
    return isinstance(dir_info, dict) and dir_info.get("editable") is True


def _contains_segments(parts: tuple[str, ...], pair: tuple[str, str]) -> bool:
    """True when *pair* appears as adjacent segments in *parts*."""
    return any(parts[i : i + 2] == pair for i in range(len(parts) - 1))


def detect_install_method() -> InstallMethod:
    """Classify how this copy of ``mt`` was installed. Never raises, no I/O."""
    if _is_editable_or_source():
        return InstallMethod.EDITABLE_OR_SOURCE

    parts = Path(sys.executable).resolve().parts
    if _contains_segments(parts, _UV_TOOL_SEGMENTS):
        return InstallMethod.UV_TOOL
    if _contains_segments(parts, _PIPX_SEGMENTS):
        return InstallMethod.PIPX
    return InstallMethod.PIP


# -- Registry query (D2) ------------------------------------------------------


def fetch_latest_version() -> str | None:
    """Return the latest non-yanked version on PyPI, or ``None`` on failure.

    D2 mandates returning nothing rather than raising on *any* failure — a
    registry outage or a malformed payload must never surface a traceback.
    The except clauses stay enumerated rather than becoming a blind except.
    """
    url = PYPI_JSON_URL_TEMPLATE.format(name=DISTRIBUTION_NAME)
    try:
        response = httpx.get(url, timeout=REGISTRY_TIMEOUT)
        response.raise_for_status()
        version = response.json()["info"]["version"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None

    if not isinstance(version, str) or not version:
        return None
    return version


# -- Upgrade dispatch (D5) ----------------------------------------------------


def upgrade_command(method: InstallMethod) -> list[str] | None:
    """Return the argv to run for *method*, or ``None`` if it is not auto-run.

    Fixed argv, no shell, no interpolation of registry data: the upgrade
    always installs latest-unpinned.
    """
    command = _UPGRADE_COMMANDS.get(method)
    return list(command) if command is not None else None


# -- Post-upgrade migration probe (D6) ----------------------------------------


def _resolve_mt_binary() -> str:
    """Prefer the ``mt`` entry point beside this interpreter; else bare ``mt``."""
    candidate = Path(sys.executable).resolve().parent / "mt"
    if candidate.exists():
        return str(candidate)
    return "mt"


def report_pending_migrations() -> int | None:
    """Return the pending-migration count from the new binary, or ``None``.

    Best-effort by contract (D6): a non-zero exit, timeout, unparseable
    output, or a database that is unreachable all degrade to ``None`` so the
    caller prints the generic pointer instead of a count. No failure here may
    change the update's exit code, and no DB import happens in this module.
    """
    try:
        completed = subprocess.run(
            [_resolve_mt_binary(), "data", "migrate", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=UPDATE_MIGRATE_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # Missing binary or probe timeout — informational only, degrade.
        return None

    if completed.returncode != 0:
        return None

    try:
        payload = json.loads(completed.stdout)
    except ValueError:
        return None

    if not isinstance(payload, dict) or payload.get("connected") is not True:
        return None
    pending = payload.get("pending")
    if not isinstance(pending, list):
        return None
    return len(pending)
