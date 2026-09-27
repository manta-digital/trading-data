"""Record real Databento metadata responses as unit-test fixtures (slice 220).

Builds a ``DatabentoTickProvider`` (key from ``MT_DATABENTO_API_KEY`` via
``Settings``) over a recording proxy of the SDK client, and drives it
**typed as ``ITickMetadataProvider``** — the free protocol, which has no paid
method. The proxy captures each SDK call's return value (the endpoint's JSON
as the SDK decoded it) and refuses any access to ``batch`` or
``timeseries``, so this script cannot spend.

Recorded request: ``ES.c.0`` (``continuous``), ``GLBX.MDP3``,
``[2025-01-06, 2025-01-11)``, every schema in ``ESTIMATE_SCHEMAS``. Files land
in ``test/fixtures/databento/metadata/<method>[.<schema>].json`` as
``{"method", "request", "response"}``. The key is not a call argument, so it
never reaches a file; the script still scans every file for it before
finishing.

Manual, developer-run only; never in CI. Usage::

    uv run python scripts/record_databento_fixtures.py
    uv run python scripts/record_databento_fixtures.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import databento
import httpx

from manta_trading.config import Settings
from manta_trading.data.tick.constants import CME_DATASET, ESTIMATE_SCHEMAS, SType
from manta_trading.data.tick.databento.adapter import (
    DatabentoTickProvider,
    api_key_env,
)
from manta_trading.data.tick.provider import ITickMetadataProvider, TickRequest

FIXTURE_DIR = (
    Path(__file__).resolve().parents[1] / "test" / "fixtures" / "databento" / "metadata"
)
REQUEST = TickRequest(
    dataset=CME_DATASET,
    symbols=("ES.c.0",),
    stype_in=SType.CONTINUOUS,
    schema=ESTIMATE_SCHEMAS[0],
    start=date(2025, 1, 6),
    end=date(2025, 1, 11),
)
#: SDK namespaces the metadata protocol uses; any other access raises.
RECORDED_NAMESPACES = frozenset({"metadata", "symbology"})


class _RecordingNamespace:
    def __init__(self, name: str, inner: Any, sink: list[dict[str, Any]]):
        self._name, self._inner, self._sink = name, inner, sink

    def __getattr__(self, method: str) -> Any:
        target = getattr(self._inner, method)

        def call(**kwargs: Any) -> Any:
            response = target(**kwargs)
            self._sink.append(
                {
                    "method": f"{self._name}.{method}",
                    "request": kwargs,
                    "response": response,
                }
            )
            return response

        return call


class _RecordingClient:
    """Proxies ``Historical``: records metadata/symbology, refuses the rest."""

    def __init__(self, inner: Any, sink: list[dict[str, Any]]):
        self._inner, self._sink = inner, sink

    def __getattr__(self, name: str) -> Any:
        if name not in RECORDED_NAMESPACES:
            raise RuntimeError(f"recording refused access to Historical.{name}")
        return _RecordingNamespace(name, getattr(self._inner, name), self._sink)


def _file_name(entry: dict[str, Any]) -> str:
    method = entry["method"].split(".")[-1]
    schema = entry["request"].get("schema")
    return f"{method}.{schema}.json" if schema else f"{method}.json"


def record(metadata: ITickMetadataProvider) -> None:
    """Every method of the free protocol, over ``REQUEST`` at each schema."""
    metadata.dataset_range(REQUEST.dataset)
    metadata.dataset_condition(REQUEST.dataset, REQUEST.start, REQUEST.end)
    metadata.resolve_symbols(REQUEST)
    for schema in ESTIMATE_SCHEMAS:
        at_schema = REQUEST.with_schema(schema)
        metadata.record_count(at_schema)
        metadata.billable_size(at_schema)
        metadata.cost(at_schema)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print, don't write")
    args = parser.parse_args(argv)

    key = Settings().databento_api_key
    if not key:
        print(f"{api_key_env()} is not set")
        return 1
    sink: list[dict[str, Any]] = []
    client = _RecordingClient(databento.Historical(key=key), sink)
    with DatabentoTickProvider(client, httpx.Client()) as provider:  # type: ignore[arg-type]
        record(provider)

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for entry in sink:
        text = json.dumps(entry, indent=2, default=str, sort_keys=True) + "\n"
        if key in text:
            print(f"refusing to write {_file_name(entry)}: it contains the key")
            return 1
        if args.dry_run:
            print(f"--- {_file_name(entry)}\n{text[:600]}")
        else:
            (FIXTURE_DIR / _file_name(entry)).write_text(text, encoding="utf-8")
            print(f"wrote {FIXTURE_DIR / _file_name(entry)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
