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
import shutil
import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from typing import Final

import httpx
import typer
from packaging.version import Version

from manta_trading.cli.output import print_error, print_result
from manta_trading.constants import (
    DISTRIBUTION_NAME,
    PYPI_JSON_URL_TEMPLATE,
    PYPI_REGISTRY_TIMEOUT,
    UPDATE_MIGRATE_PROBE_TIMEOUT,
    UPDATE_VERSION_PROBE_TIMEOUT,
    UPGRADE_TIMEOUT,
)
from manta_trading.logging import get_logger

_logger = get_logger(__name__)


class InstallMethod(StrEnum):
    """How the running ``mt`` was installed (slice 909 D4)."""

    UV_TOOL = "uv-tool"
    PIPX = "pipx"
    PIP = "pip"
    EDITABLE_OR_SOURCE = "editable-or-source"


# Adjacent path-segment pairs identifying managed tool environments (D4).
# Matched against UNRESOLVED paths: in every venv layout `bin/python` is a
# symlink to the base interpreter, so resolving first discards exactly the
# segments being matched (review 909 F001).
_UV_TOOL_SEGMENTS: Final[tuple[str, str]] = ("uv", "tools")
_PIPX_SEGMENTS: Final[tuple[str, str]] = ("pipx", "venvs")

# Marker files each installer writes at the root of its managed environment
# (``sys.prefix``). Location-independent, unlike the path segments above:
# they survive a relocated UV_TOOL_DIR / PIPX_HOME. Verified against
# uv 0.11.2 and pipx 1.16.5 (2026-08-02).
_UV_TOOL_RECEIPT: Final[str] = "uv-receipt.toml"
_PIPX_RECEIPT: Final[str] = "pipx_metadata.json"

# ``@latest`` asks for the newest release explicitly, ignoring any version
# constraint recorded by the original install.
_UPGRADE_TARGET: Final[str] = f"{DISTRIBUTION_NAME}@latest"

# Only the uv-tool path is auto-runnable; every other method gets guidance.
#
# ``--refresh-package`` is load-bearing, not defensive. uv resolves against
# cached index metadata, while this command reads the PyPI JSON API directly:
# a cache populated shortly before a release makes uv see no new version at
# all, so the upgrade exits 0 having done nothing right after we told the user
# an update was available. Observed 2026-08-02 (uv 0.11.2) during the 909
# release; forcing a metadata refresh for this one package fixes it (D5).
_UPGRADE_COMMANDS: Final[dict[InstallMethod, list[str]]] = {
    InstallMethod.UV_TOOL: [
        "uv",
        "tool",
        "install",
        "--upgrade",
        "--refresh-package",
        DISTRIBUTION_NAME,
        _UPGRADE_TARGET,
    ],
}

# Single definition site for the command text shown per install method (D5).
MANUAL_UPGRADE_COMMAND: Final[dict[InstallMethod, str]] = {
    InstallMethod.UV_TOOL: (
        f"uv tool install --upgrade --refresh-package {DISTRIBUTION_NAME} "
        f"{_UPGRADE_TARGET}"
    ),
    InstallMethod.PIPX: f"pipx upgrade {DISTRIBUTION_NAME}",
    InstallMethod.PIP: f"pip install --upgrade {DISTRIBUTION_NAME}",
    InstallMethod.EDITABLE_OR_SOURCE: "git pull && uv sync",
}

# Extra guidance shown alongside the manual command, where it is needed.
_UPGRADE_NOTE: Final[dict[InstallMethod, str]] = {
    InstallMethod.PIP: "Run it in the environment that owns mt.",
}

MIGRATION_POINTER: Final[str] = (
    "Run 'mt data migrate status' to check for pending migrations."
)

REGISTRY_UNREACHABLE_ERROR: Final[str] = "registry unreachable"
REGISTRY_UNREACHABLE_MESSAGE: Final[str] = (
    "Could not reach PyPI — check your network connection."
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


def _has_marker(filename: str) -> bool:
    """True when *filename* exists at the root of this environment."""
    return (Path(sys.prefix) / filename).is_file()


def _environment_parts() -> tuple[str, ...]:
    """Unresolved path segments identifying this environment.

    Both the environment root and the interpreter path are inspected, and
    neither is resolved: a venv's ``bin/python`` is a symlink to the base
    interpreter, so ``.resolve()`` returns the *interpreter's* location and
    discards the installer-specific segments entirely (review 909 F001).
    """
    return Path(sys.prefix).parts + Path(sys.executable).parts


def detect_install_method() -> InstallMethod:
    """Classify how this copy of ``mt`` was installed. Never raises, no I/O.

    Marker files first (location-independent), path segments as a fallback
    for layouts that predate them.
    """
    if _is_editable_or_source():
        return InstallMethod.EDITABLE_OR_SOURCE
    if _has_marker(_UV_TOOL_RECEIPT):
        return InstallMethod.UV_TOOL
    if _has_marker(_PIPX_RECEIPT):
        return InstallMethod.PIPX

    parts = _environment_parts()
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
        response = httpx.get(url, timeout=PYPI_REGISTRY_TIMEOUT)
        response.raise_for_status()
        version = response.json()["info"]["version"]
        if not isinstance(version, str) or not version:
            raise TypeError("info.version is not a non-empty string")
        # Parse here so the caller can compare without a guard: InvalidVersion
        # subclasses ValueError, so a non-PEP-440 string is caught below rather
        # than escaping as a traceback at comparison time (review 909 F002).
        # This also keeps registry-supplied text out of Rich markup — a valid
        # PEP 440 version cannot contain markup characters.
        Version(version)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        _logger.debug("PyPI version lookup failed: %s: %s", type(exc).__name__, exc)
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
    """Prefer the ``mt`` entry point beside this interpreter; else bare ``mt``.

    Unresolved for the same reason detection is: resolving lands in the *base*
    interpreter's bin directory, where no ``mt`` exists, so the preference
    never fired (review 909 F003).
    """
    candidate = Path(sys.executable).parent / "mt"
    if candidate.exists():
        return str(candidate)
    return "mt"


def installed_version() -> str | None:
    """Version reported by the ``mt`` binary on disk, or ``None`` if unknown.

    Run after an upgrade to confirm the new code is actually in place — the
    upgrade subprocess exiting 0 is not by itself proof that the version
    moved (D5). Best-effort: an unparseable or failing probe leaves the
    outcome unverified rather than asserting a failure.
    """
    try:
        completed = subprocess.run(
            [_resolve_mt_binary(), "--version"],
            capture_output=True,
            text=True,
            timeout=UPDATE_VERSION_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # Missing binary or probe timeout — leave the version unverified.
        _logger.debug("version probe failed: %s: %s", type(exc).__name__, exc)
        return None

    if completed.returncode != 0:
        _logger.debug("version probe exited %d", completed.returncode)
        return None
    tokens = completed.stdout.split()
    if not tokens:
        _logger.debug("version probe produced no output")
        return None
    return tokens[-1]


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
    except (OSError, subprocess.SubprocessError) as exc:
        # Missing binary or probe timeout — informational only, degrade.
        _logger.debug("migration probe failed: %s: %s", type(exc).__name__, exc)
        return None

    if completed.returncode != 0:
        _logger.debug("migration probe exited %d", completed.returncode)
        return None

    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        _logger.debug("migration probe output was not JSON: %s", exc)
        return None

    if not isinstance(payload, dict) or payload.get("connected") is not True:
        _logger.debug("migration probe reported no database connection")
        return None
    pending = payload.get("pending")
    if not isinstance(pending, list):
        _logger.debug("migration probe payload had no 'pending' list")
        return None
    return len(pending)


# -- The command (D1, D7, D8) -------------------------------------------------


def _current_version() -> str:
    """Installed version of this distribution, or ``"dev"`` without metadata.

    Missing metadata is classified as ``EDITABLE_OR_SOURCE`` by
    :func:`detect_install_method`, so every path that *compares* versions has
    a real version string by the time it runs (D3).
    """
    try:
        return importlib.metadata.version(DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def _is_interactive() -> bool:
    """True when stdin is a TTY, i.e. a human can answer a prompt."""
    return sys.stdin.isatty()


def _print_manual_command(method: InstallMethod) -> None:
    print_result(f"To update, run: {MANUAL_UPGRADE_COMMAND[method]}", json_mode=False)
    note = _UPGRADE_NOTE.get(method)
    if note:
        print_result(f"  {note}", json_mode=False)


def update(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Install the update without prompting.",
    ),
) -> None:
    """Check PyPI for a newer release of mt and install it."""
    method = detect_install_method()
    current = _current_version()

    # 1. Developer installs refuse before any network call (D4).
    if method is InstallMethod.EDITABLE_OR_SOURCE:
        if json_output:
            print_result(
                {
                    "current": current,
                    "latest": None,
                    "update_available": False,
                    "install_method": method.value,
                },
                json_mode=True,
            )
            return
        print_result(
            "Developer install detected (editable/source checkout) — "
            "self-update disabled.",
            json_mode=False,
        )
        print_result(f"  To update: {MANUAL_UPGRADE_COMMAND[method]}", json_mode=False)
        return

    # 2. Registry query — never raises, returns None on any failure (D2).
    latest = fetch_latest_version()
    if latest is None:
        if json_output:
            print_result(
                {
                    "current": current,
                    "latest": None,
                    "update_available": False,
                    "error": REGISTRY_UNREACHABLE_ERROR,
                },
                json_mode=True,
            )
        else:
            print_error(REGISTRY_UNREACHABLE_MESSAGE, json_mode=False)
        raise typer.Exit(1)

    # 3. PEP 440 comparison (D3).
    update_available = Version(latest) > Version(current)

    # 4. --json is a pure query: no prompt, no subprocess, no probe (D7).
    if json_output:
        print_result(
            {
                "current": current,
                "latest": latest,
                "update_available": update_available,
                "install_method": method.value,
            },
            json_mode=True,
        )
        return

    # 5. Already current.
    if not update_available:
        print_result(f"mt is up to date ({current}).", json_mode=False)
        return

    print_result(f"Update available: {current} → {latest}", json_mode=False)

    # 6. Confirmation gate.
    if not yes:
        if not _is_interactive():
            print_result(
                "Run with --yes to install non-interactively.", json_mode=False
            )
            return
        if not typer.confirm("Install now?"):
            return

    # 7. Only the uv-tool path is auto-run; everything else prints (D5).
    command = upgrade_command(method)
    if command is None or shutil.which(command[0]) is None:
        _print_manual_command(method)
        return

    try:
        completed = subprocess.run(command, timeout=UPGRADE_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        print_error(
            f"Upgrade timed out after {UPGRADE_TIMEOUT:.0f}s and was killed. "
            f"Run manually: {MANUAL_UPGRADE_COMMAND[method]}",
            json_mode=False,
        )
        raise typer.Exit(1) from None

    if completed.returncode != 0:
        print_error(
            f"Upgrade failed (exit {completed.returncode}). "
            f"Run manually: {MANUAL_UPGRADE_COMMAND[method]}",
            json_mode=False,
        )
        raise typer.Exit(1)

    # The subprocess exiting 0 does not prove the version moved (D5).
    verified = installed_version()
    if verified is not None and verified != latest:
        print_error(
            f"Upgrade reported success but {DISTRIBUTION_NAME} is still "
            f"{verified}. Run manually: {MANUAL_UPGRADE_COMMAND[method]}",
            json_mode=False,
        )
        raise typer.Exit(1)

    print_result(f"Updated {DISTRIBUTION_NAME} to {latest}", json_mode=False)
    if verified is None:
        print_result("Run 'mt --version' to confirm.", json_mode=False)

    # 8. Best-effort migration report — cannot change the exit code (D6).
    pending = report_pending_migrations()
    if pending is None:
        print_result(MIGRATION_POINTER, json_mode=False)
    elif pending > 0:
        print_result(
            f"{pending} migration(s) pending — run 'mt data migrate status'.",
            json_mode=False,
        )
    else:
        print_result("No pending migrations.", json_mode=False)
