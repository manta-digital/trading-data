"""The off-tape history sweep on compressed chunks (slice 171).

Throwaway migrated database holding real fixture days inserted raw (marks
``NULL``, as history is before the sweep) across six weekly chunks, all but
the newest compressed. SPY 2026-01-07 is a Wednesday whose after-hours tail
runs past 00:00 UTC Thursday, the chunk boundary, so that ET day spans two
chunks and the widening to whole ET days is exercised.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
import pytest
from psycopg.rows import dict_row
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.constants import HEALTH_MINUTE_SESSION_CALENDAR
from manta_trading.data.acquisition.pass_runs import (
    PassKind,
    PassRun,
    PassRunRepository,
)
from manta_trading.data.gaps.minute_accounting import compute_minute_accounting
from manta_trading.data.quality.off_tape import mark_day
from manta_trading.market.maintenance import off_tape_sweep
from manta_trading.market.maintenance.off_tape_sweep import (
    ChecksumMismatch,
    list_chunks,
    run_sweep,
    unevaluated_chunks,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "off_tape"
DAYS = (
    "SPY_2026-01-07",  # Wednesday, winter: tail to 01:00 UTC Thursday
    "AVGO_2026-06-03",
    "SPY_2026-09-08",
    "SPY_2026-09-11",
    "SPY_2026-09-22",
)
_RAW = (
    "SELECT md5(string_agg(concat_ws(',', symbol, time, open, high, low, close,"
    " volume), '|' ORDER BY symbol, time)) FROM minute_ohlcv"
)


def _load(name: str) -> pd.DataFrame:
    bars = pd.read_csv(FIXTURES / f"{name}.csv", dtype=str)
    bars["time"] = pd.to_datetime(bars["time"], utc=True)
    return bars


def _expected() -> dict[tuple[str, datetime], int]:
    out: dict[tuple[str, datetime], int] = {}
    for name in DAYS:
        bars = _load(name)
        masks = mark_day(bars)
        for row, mask in zip(bars.itertuples(), masks, strict=True):
            out[(row.symbol, row.time.to_pydatetime())] = int(mask)
    return out


@pytest.fixture()
def history(migrated_db: str) -> Iterator[str]:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        with conn.cursor() as cur:
            with cur.copy(
                "COPY minute_ohlcv (time, symbol, open, high, low, close, volume)"
                " FROM STDIN"
            ) as copy:
                for name in DAYS:
                    for row in _load(name).itertuples(index=False):
                        copy.write_row(
                            (
                                row.time.to_pydatetime(),
                                row.symbol,
                                row.open,
                                row.high,
                                row.low,
                                row.close,
                                row.volume,
                            )
                        )
        for chunk in list_chunks(conn)[:-1]:
            conn.execute("SELECT compress_chunk(%s::regclass)", (chunk.name,))
    yield migrated_db


def _cli(url: str, *extra: str) -> Any:
    args = ["data", "off-tape", "sweep", "--maintenance-url", url, *extra]
    return CliRunner().invoke(app, args)


def _conn(url: str) -> psycopg.Connection:
    return psycopg.connect(url, autocommit=True)


def _marks(conn: psycopg.Connection) -> dict[tuple[str, datetime], int | None]:
    rows = conn.execute("SELECT symbol, time, off_tape FROM minute_ohlcv").fetchall()
    return {(symbol, when.astimezone(UTC)): mask for symbol, when, mask in rows}


def _nulls_by_chunk(conn: psycopg.Connection) -> dict[str, int]:
    out = {}
    for chunk in list_chunks(conn):
        row = conn.execute(
            "SELECT count(*) FROM minute_ohlcv WHERE time >= %s AND time < %s"
            " AND off_tape IS NULL",
            (chunk.start, chunk.end),
        ).fetchone()
        assert row is not None
        out[chunk.name] = int(row[0])
    return out


def _raw(conn: psycopg.Connection) -> str:
    row = conn.execute(_RAW).fetchone()
    assert row is not None
    return str(row[0])


def test_unevaluated_chunks_on_a_dict_row_connection(history: str) -> None:
    """The 172 cutover opens its connection with dict rows; unpacking one
    yields column names, which reached the database as timestamps."""
    with psycopg.connect(history, autocommit=True, row_factory=dict_row) as conn:
        assert len(unevaluated_chunks(conn)) == 6
    with _conn(history) as conn:
        run_sweep(conn)
    with psycopg.connect(history, autocommit=True, row_factory=dict_row) as conn:
        assert unevaluated_chunks(conn) == []


def test_sweep_marks_everything_and_keeps_raw(history: str) -> None:
    with _conn(history) as conn:
        chunks = list_chunks(conn)
        assert len(chunks) == 6
        compressed_before = {c.name for c in chunks if c.compressed}
        assert len(compressed_before) == 5
        raw = _raw(conn)
        report = run_sweep(conn)
        assert len(report.done) == 6 and not report.unlocked
        assert set(_nulls_by_chunk(conn).values()) == {0}
        assert _raw(conn) == raw
        assert {c.name for c in list_chunks(conn) if c.compressed} == compressed_before
        assert _marks(conn) == _expected()


def test_resume_after_a_failed_chunk(
    history: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = off_tape_sweep.write_masks
    calls = {"n": 0}

    def fail_second(conn: psycopg.Connection, masks: pd.DataFrame) -> int:
        calls["n"] += 1
        updated = real(conn, masks)
        if calls["n"] == 2:
            raise RuntimeError("killed after the UPDATE")
        return updated

    monkeypatch.setattr(off_tape_sweep, "write_masks", fail_second)
    with _conn(history) as conn:
        names = [c.name for c in list_chunks(conn)]
        with pytest.raises(RuntimeError, match="killed"):
            run_sweep(conn)
        nulls = _nulls_by_chunk(conn)
        assert nulls[names[0]] == 0
        assert nulls[names[1]] > 0  # rolled back
        assert list_chunks(conn)[1].compressed  # decompress rolled back too
        monkeypatch.setattr(off_tape_sweep, "write_masks", real)
        report = run_sweep(conn)
        assert names[0] not in report.done
        assert report.done == names[1:]
        assert _marks(conn) == _expected()


def test_locked_chunk_is_skipped_retried_and_reported(
    history: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(off_tape_sweep, "OFF_TAPE_SWEEP_LOCK_TIMEOUT", "200ms")
    with _conn(history) as conn, psycopg.connect(history) as holder:
        target = list_chunks(conn)[2].name
        holder.execute(f"LOCK TABLE {target} IN ROW EXCLUSIVE MODE")
        report = run_sweep(conn)
        assert report.unlocked == [target]
        assert target not in report.done and len(report.done) == 5
        holder.rollback()
        result = _cli(history)
        assert result.exit_code == 0, result.output


def test_cli_exits_non_zero_naming_the_locked_chunk(
    history: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(off_tape_sweep, "OFF_TAPE_SWEEP_LOCK_TIMEOUT", "200ms")
    with _conn(history) as conn, psycopg.connect(history) as holder:
        target = list_chunks(conn)[0].name
        holder.execute(f"LOCK TABLE {target} IN ROW EXCLUSIVE MODE")
        result = _cli(history)
        holder.rollback()
    assert result.exit_code == 1
    assert target in result.output


def _open_minute_pass(url: str) -> None:
    repo = PassRunRepository(lambda: psycopg.connect(url))
    repo.insert(
        PassRun(
            run_id=uuid.uuid4(),
            pass_kind=PassKind.MINUTE,
            hostname="test-host",
            pid=os.getpid(),
            started_at=datetime.now(UTC),
        )
    )


def test_newest_chunk_deferred_while_a_minute_pass_runs(history: str) -> None:
    _open_minute_pass(history)
    with _conn(history) as conn:
        names = [c.name for c in list_chunks(conn)]
        report = run_sweep(conn)
        assert report.deferred == names[-1]
        assert report.done == names[:-1]
        nulls = _nulls_by_chunk(conn)
        assert nulls[names[-1]] > 0
        assert all(nulls[name] == 0 for name in names[:-1])


def test_checksum_mismatch_rolls_back_and_exits_non_zero(
    history: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = off_tape_sweep._checksum
    seen: list[str] = []

    def drift(conn: psycopg.Connection, chunk: Any) -> tuple[object, ...]:
        seen.append(chunk.name)
        value = real(conn, chunk)
        return value if len(seen) % 2 else (*value, "changed")

    monkeypatch.setattr(off_tape_sweep, "_checksum", drift)
    with _conn(history) as conn:
        first = list_chunks(conn)[0]
        with pytest.raises(ChecksumMismatch, match=first.name):
            run_sweep(conn)
        assert _nulls_by_chunk(conn)[first.name] > 0
        assert list_chunks(conn)[0].compressed
    seen.clear()
    result = _cli(history)
    assert result.exit_code == 1
    assert first.name in result.output


def _seed_instruments(conn: psycopg.Connection) -> None:
    for symbol, kind in (("SPY", "ETF"), ("AVGO", "Common Stock")):
        conn.execute(
            "INSERT INTO instruments (canonical_id, symbol, asset_class, venue,"
            " trading_calendar_id, delisted_at_eodhd, eodhd_type, eodhd_exchange,"
            " first_listing_date) VALUES (%s, %s, 'equity', 'US', %s, FALSE,"
            " %s, 'US', '2026-01-02') ON CONFLICT DO NOTHING",
            (f"EQ:{symbol}", symbol, HEALTH_MINUTE_SESSION_CALENDAR, kind),
        )


def _refresh(conn: psycopg.Connection) -> None:
    for view in ("minute_4hour_ohlcv", "minute_coverage"):
        conn.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")


def _derived(conn: psycopg.Connection) -> tuple[Any, ...]:
    now = datetime(2026, 9, 23, tzinfo=UTC)
    with conn.transaction():
        accounting = compute_minute_accounting(conn, now=now)
    status = conn.execute("SELECT * FROM data_status ORDER BY 1, 2").fetchall()
    coverage = conn.execute("SELECT * FROM minute_coverage ORDER BY 1, 2").fetchall()
    return accounting, status, coverage


def test_accounting_status_and_coverage_unchanged(history: str) -> None:
    with _conn(history) as conn:
        _seed_instruments(conn)
        _refresh(conn)
        before = _derived(conn)
        assert before[0] and before[2]  # not vacuous
        run_sweep(conn)
        _refresh(conn)  # the sweep's UPDATEs invalidated every chunk's range
        assert _derived(conn) == before


def test_dry_run_writes_nothing(history: str) -> None:
    with _conn(history) as conn:
        before = _nulls_by_chunk(conn)
    result = _cli(history, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "chunks: 6 total, 6 to sweep (5 compressed, 1 uncompressed)" in result.output
    with _conn(history) as conn:
        assert _nulls_by_chunk(conn) == before
