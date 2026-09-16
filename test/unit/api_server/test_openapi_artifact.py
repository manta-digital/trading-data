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
from pydantic import BaseModel

from manta_trading.api_server.app import create_app
from manta_trading.api_server.models.kalshi_timeseries import (
    CandlesResponse,
    TradesResponse,
)
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


# --- Slice 190, section 1: the three published 200 schemas (D3, commit 3141815)


PUBLISHED_SCHEMA_TYPES = (
    "BarsResponse",
    "BarRecord",
    "CandlesResponse",
    "CandleRecord",
    "CandleBidAsk",
    "CandlePrice",
    "TradesResponse",
    "TradeRecord",
)
"""The eight types D3 made reachable from a published ``200``.

Before D3 the three list routes declared ``response_class=Response``, which
suppressed the ``200`` schema entirely and took every type reachable from it
out of ``components/schemas`` with it.
"""

PUBLISHED_SCHEMA_PATHS = (
    "/api/v1/bars/{symbol}",
    "/api/v1/kalshi/markets/{ticker}/candlesticks",
    "/api/v1/kalshi/markets/{ticker}/trades",
)
"""The three routes that offer ``format=msgpack`` and so declare two media
types on one status."""

PUBLISHED_MEDIA_TYPES = ("application/json", "application/x-msgpack")

EXPECTED_ARTIFACT_PATH_COUNT = 17


def _committed_artifact() -> dict[str, Any]:
    assert ARTIFACT_PATH.exists(), (
        f"{ARTIFACT_PATH} is missing; run: uv run python scripts/dump_openapi.py"
    )
    loaded: dict[str, Any] = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    return loaded


@pytest.mark.parametrize("type_name", PUBLISHED_SCHEMA_TYPES)
def test_published_response_types_are_in_components(type_name: str) -> None:
    """Read from the committed artifact, not the live app: the artifact is what
    a client generator consumes, and only its contents are a published fact."""
    schemas = _committed_artifact()["components"]["schemas"]
    assert type_name in schemas, (
        f"{type_name} is absent from components/schemas in {ARTIFACT_PATH}; "
        "a route that suppresses its 200 schema takes every type reachable "
        "from it out of the artifact. Run: uv run python scripts/dump_openapi.py"
    )


@pytest.mark.parametrize("path", PUBLISHED_SCHEMA_PATHS)
def test_published_paths_declare_both_media_types(path: str) -> None:
    """Each list route's ``200`` names both encodings it can actually serve."""
    content = _committed_artifact()["paths"][path]["get"]["responses"]["200"]["content"]
    for media_type in PUBLISHED_MEDIA_TYPES:
        assert media_type in content, (
            f"{path} does not declare {media_type} on its 200 in "
            f"{ARTIFACT_PATH}; it declares {sorted(content)}"
        )


def _described_fields(model: type[BaseModel]) -> dict[str, str]:
    """Field name to description, read from the model rather than retyped.

    A test that hard-codes the prose passes when the model's description is
    deleted — it would assert only that the literal in the test still matches
    the literal in the artifact, with the model no longer in the loop.
    """
    return {
        name: field.description
        for name, field in model.model_fields.items()
        if field.description is not None
    }


DESCRIBED_MODELS = (CandlesResponse, TradesResponse)


@pytest.mark.parametrize("model", DESCRIBED_MODELS, ids=lambda m: m.__name__)
def test_field_descriptions_reach_the_artifact(model: type[BaseModel]) -> None:
    """The ``Field(description=…)`` prose is the field table's source (190 D3).

    Descriptions live in the artifact only because the route publishes its
    ``200`` schema; before D3 none of these reached a client.
    """
    described = _described_fields(model)
    assert described, (
        f"{model.__name__} declares no Field(description=…); the seven "
        "descriptions D3 published are the reason this test exists"
    )
    properties = _committed_artifact()["components"]["schemas"][model.__name__][
        "properties"
    ]
    for name, description in described.items():
        assert properties[name].get("description") == description, (
            f"{model.__name__}.{name}'s description in {ARTIFACT_PATH} does "
            f"not match the model. Run: uv run python scripts/dump_openapi.py"
        )


def test_artifact_declares_the_expected_path_count() -> None:
    """A gate on the assumption the rest of slice 190 rests on: the documented
    surface is these seventeen paths and no others."""
    paths = _committed_artifact()["paths"]
    assert len(paths) == EXPECTED_ARTIFACT_PATH_COUNT, (
        f"{ARTIFACT_PATH} declares {len(paths)} paths, expected "
        f"{EXPECTED_ARTIFACT_PATH_COUNT}: {sorted(paths)}"
    )
