"""Re-ingest the proof database in process, with a patched module constant.

``workers`` and ``batch`` tune constants the way 225 designed them to be
tuned: patch the module attribute, run the shipped ingest. The ingest is the
CLI's own ``_ingest`` (same inputs as ``mt data tick ingest``) on settings
whose tick URLs are the proof database's.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psycopg

from manta_trading.cli.commands.tick_store_cmds import _ingest
from manta_trading.config import Settings
from manta_trading.data.tick import constants
from manta_trading.data.tick.pass_contract import PassResult
from proof_226.common import EXPECTED_ROWS, ProofSetupError, ProofUrls
from proof_226.guard import reset_proof_database


@dataclass(frozen=True)
class IngestRun:
    seconds: float
    units: list[dict[str, Any]]
    ingested: int
    failed: int

    @property
    def decode_seconds(self) -> float:
        return sum(u["decode_seconds"] for u in self.units)

    @property
    def write_seconds(self) -> float:
        return sum(u["write_seconds"] for u in self.units)


@contextmanager
def patched(name: str, value: object) -> Iterator[None]:
    """Set ``constants.<name>`` for the block, then restore it."""
    original = getattr(constants, name)
    setattr(constants, name, value)
    try:
        yield
    finally:
        setattr(constants, name, original)


def reset(urls: ProofUrls) -> int:
    """The guarded reset, as the owner (``tick_app`` has no TRUNCATE)."""
    with psycopg.connect(urls.maintenance_url, autocommit=True) as conn:
        return reset_proof_database(conn)


def ingest(urls: ProofUrls, unit_ids: tuple[int, ...] = ()) -> IngestRun:
    settings = Settings(
        tick_db_url=urls.db_url, tick_maintenance_url=urls.maintenance_url
    )
    result: PassResult = asyncio.run(_ingest(settings, unit_ids))
    [report] = result.reports
    summary = report.summary
    units = [u for u in summary["units"] if "duration_seconds" in u]
    return IngestRun(
        report.duration_ms / 1000, units, summary["ingested"], summary["failed"]
    )


def trade_rows(urls: ProofUrls) -> int:
    with psycopg.connect(urls.db_url) as conn:
        row = conn.execute("SELECT count(*) FROM tick_trade").fetchone()
    assert row is not None
    return int(row[0])


def reload_whole_set(urls: ProofUrls) -> int:
    """Reset and ingest everything with the current constants; check the rows."""
    reset(urls)
    run = ingest(urls)
    rows = trade_rows(urls)
    if run.failed or rows != EXPECTED_ROWS:
        raise ProofSetupError(
            f"reload left {rows:,} rows ({run.failed} units failed); "
            f"expected {EXPECTED_ROWS:,}"
        )
    return rows
