"""Record slice 229's provider-equivalence fixtures from production (read-only).

Reads, through the shipped roll and status reads (never copies of them):

- ES outright definitions and ``definitions_as_of`` (``roll_inputs``);
- outright ledger volume per (session, instrument, unit) over current units,
  and 225's verdict per session (``roll_inputs.session_volumes``,
  ``status_reads.session_verdicts``);
- CME_EQUITY sessions over the fixture span (``status_reads.calendar_sessions``);
- Databento's ``symbology.resolve`` intervals for ``ES.c.0``, ``ES.c.1``,
  ``ES.v.0`` and ``ES.v.1`` (free; the provider is typed as
  ``ITickMetadataProvider``, which has no paid method).

The tick connection is read-only; every statement is a ``SELECT``. URLs and
the key come from ``Settings`` (``MT_TICK_DB_URL``, ``MT_TIMESCALE_DB_URL``,
the Databento key); an unset one stops the script naming it. Output:
``test/fixtures/tick/roll/es_{definitions,volumes,calendar,databento}.json``,
each with its source and the UTC time it was read.

Manual, developer-run only; never in CI. Usage::

    uv run python scripts/record_229_roll_fixture.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg

from manta_trading.config import Settings
from manta_trading.data.tick.constants import (
    CME_DATASET,
    RollMethod,
    SType,
    TickSchema,
)
from manta_trading.data.tick.databento.adapter import DatabentoTickProvider
from manta_trading.data.tick.provider import ITickMetadataProvider, TickRequest
from manta_trading.data.tick.roll_inputs import (
    definitions_as_of,
    outright_definitions,
    session_volumes,
)
from manta_trading.data.tick.roll_rules import RollRule
from manta_trading.data.tick.session_days import utc_midnight
from manta_trading.data.tick.status_reads import (
    calendar_sessions,
    session_verdicts,
    shape_of,
)
from manta_trading.data.tick.universe import TICK_UNIVERSE

OUT = Path(__file__).resolve().parents[1] / "test" / "fixtures" / "tick" / "roll"
PRODUCT = "ES"
#: Covers both held ranges, the lag sessions before them, and ESH5's expiry.
CALENDAR_SPAN = (date(2024, 8, 28), date(2025, 4, 1))
#: Databento's intervals over the held span; ``end`` exclusive.
RESOLVE_SPAN = (date(2024, 8, 30), date(2025, 1, 3))
#: ``ES.c.0``, ``ES.c.1``, ``ES.v.0``, ``ES.v.1``: Databento's continuous symbols.
CONTINUOUS = tuple(
    RollRule(method, rank).symbol(PRODUCT) for method in RollMethod for rank in (0, 1)
)


def _required(value: str | None, env: str) -> str:
    if not value:
        raise SystemExit(f"{env} is not set")
    return value


def _envelope(source: str, rows: Any, **extra: Any) -> dict[str, Any]:
    read_at = datetime.now(UTC).isoformat()
    return {"source": source, "read_at": read_at, **extra, "rows": rows}


def _calendar(calendar_url: str) -> dict[str, Any]:
    sessions = calendar_sessions(
        calendar_url,
        "CME_EQUITY",
        utc_midnight(CALENDAR_SPAN[0]),
        utc_midnight(CALENDAR_SPAN[1]),
    )
    rows = [
        {
            "session_date": s.session_date.isoformat(),
            "open_utc": s.open_utc.isoformat(),
            "close_utc": s.close_utc.isoformat(),
        }
        for s in sessions
    ]
    return _envelope("production calendar CME_EQUITY (trading_sessions)", rows)


async def _tick(tick_url: str, calendar_url: str) -> dict[str, dict[str, Any]]:
    entry = next(e for e in TICK_UNIVERSE if e.product == PRODUCT)
    shape = shape_of(entry)
    sessions = await asyncio.to_thread(
        calendar_sessions,
        calendar_url,
        "CME_EQUITY",
        utc_midnight(CALENDAR_SPAN[0]),
        utc_midnight(CALENDAR_SPAN[1]),
    )
    async with await psycopg.AsyncConnection.connect(tick_url) as conn:
        await conn.set_read_only(True)
        async with conn.transaction():
            defs = await outright_definitions(conn, PRODUCT)
            as_of = await definitions_as_of(conn, shape)
            volumes = await session_volumes(conn, shape, *CALENDAR_SPAN)
            verdicts = await session_verdicts(conn, entry, sessions)
    source = "trading_tick (read-only)"
    return {
        "es_definitions": _envelope(
            f"{source}: roll_inputs.outright_definitions",
            [asdict(d) for d in defs],
            definitions_as_of=as_of.isoformat(),
        ),
        "es_volumes": _envelope(
            f"{source}: roll_inputs.session_volumes, status_reads.session_verdicts",
            [asdict(v) | {"session_date": v.session_date.isoformat()} for v in volumes],
            statuses={v.session_date.isoformat(): v.status.value for v in verdicts},
        ),
    }


def _databento(metadata: ITickMetadataProvider) -> dict[str, Any]:
    request = TickRequest(
        dataset=CME_DATASET,
        symbols=CONTINUOUS,
        stype_in=SType.CONTINUOUS,
        schema=TickSchema.TRADES,
        start=RESOLVE_SPAN[0],
        end=RESOLVE_SPAN[1],
    )
    resolution = metadata.resolve_symbols(request)
    rows = {
        symbol: [
            {
                "start_date": i.start_date.isoformat(),
                "end_date": i.end_date.isoformat(),
                "instrument_id": i.instrument_id,
            }
            for i in resolution.mappings.get(symbol, ())
        ]
        for symbol in CONTINUOUS
    }
    missing = [s for s in CONTINUOUS if not rows[s]]
    if missing:
        raise SystemExit(f"symbology.resolve returned no intervals for {missing}")
    return _envelope(
        "Databento symbology.resolve (GLBX.MDP3, continuous → instrument_id)",
        rows,
        start=RESOLVE_SPAN[0].isoformat(),
        end_exclusive=RESOLVE_SPAN[1].isoformat(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    settings = Settings()
    tick_url = _required(settings.tick_db_url, "MT_TICK_DB_URL")
    calendar_url = _required(settings.timescale_db_url, "MT_TIMESCALE_DB_URL")
    files = asyncio.run(_tick(tick_url, calendar_url))
    files["es_calendar"] = _calendar(calendar_url)
    with DatabentoTickProvider.from_settings(settings) as provider:
        metadata: ITickMetadataProvider = provider
        files["es_databento"] = _databento(metadata)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {path} ({len(body['rows'])} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
