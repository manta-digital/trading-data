"""The committed OpenAPI artifact must match the app (slice 186 D7).

Two assertions, split on purpose: a routine release version bump must not fail
an unrelated test run, but any *shape* drift must fail immediately.

Note: CI (``.github/workflows/ci.yml``) is publish-on-tag only and runs no test
job, so this gates in the local suite like every other test in this repo.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from manta_trading.api_server.app import create_app
from manta_trading.version import package_version


def _load_dump_script() -> Any:
    """Import ``scripts/dump_openapi.py`` by path.

    ``scripts/`` is not an installed package, and making it one to satisfy a
    test would change what this project ships. Importing the real module is
    what keeps the test honest: if the script's serialization changes, this
    test sees the change rather than a copy of it.
    """
    script_path = (
        Path(__file__).resolve().parents[3] / "scripts" / "dump_openapi.py"
    )
    spec = importlib.util.spec_from_file_location("dump_openapi", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_dump = _load_dump_script()
ARTIFACT_PATH = _dump.ARTIFACT_PATH
generate = _dump.generate


def _without_version(schema: dict[str, Any]) -> dict[str, Any]:
    stripped = json.loads(json.dumps(schema))
    stripped["info"].pop("version", None)
    return stripped  # type: ignore[no-any-return]


def test_committed_artifact_matches_the_app_ignoring_version() -> None:
    assert ARTIFACT_PATH.exists(), (
        f"{ARTIFACT_PATH} is missing; run: uv run python scripts/dump_openapi.py"
    )
    committed = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    generated = json.loads(generate())
    assert _without_version(committed) == _without_version(generated), (
        "docs/api/openapi.json is stale; "
        "run: uv run python scripts/dump_openapi.py"
    )


def test_generated_version_is_the_package_version() -> None:
    """D3's guarantee, asserted on the artifact generator rather than only on
    the live app: the schema cannot ship a version the distribution disowns."""
    assert json.loads(generate())["info"]["version"] == package_version()


def test_generation_needs_no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Schema generation does not enter the lifespan, so the artifact can be
    regenerated on a checkout with no DB configured."""
    monkeypatch.delenv("MT_TIMESCALE_DB_URL", raising=False)
    assert json.loads(generate())["paths"]


def test_documented_contract_surfaces_are_present() -> None:
    """The two breaking changes and the new statuses must be discoverable from
    the artifact alone — it is what a client developer reads."""
    paths = create_app().openapi()["paths"]
    bars = paths["/api/v1/bars/{symbol}"]["get"]["responses"]
    assert "504" in bars
    assert "422" in bars
