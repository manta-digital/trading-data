"""``mt data tick parity`` on the real 2024-12-03 fixtures (slice 306, D7, D9).

The cut ``tbbo`` day and the bar day are ingested through the real pass (see
``tick_support.parity_seed``). The test buckets the fixture's trades itself, in
Python and independently of the command, and sets them against the provider
minutes; the command's ``--json`` must agree minute for minute.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest
from tick_support.cli import clear_tick_env
from tick_support.parity_seed import (
    SESSION,
    Minute,
    ParityDb,
    bar,
    run_parity,
    seed_parity,
)
from tick_support.roll_seed import seed_tier_days

from manta_trading.data.tick.bar_constants import BAR_1M_TABLE
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.tick_reads import TickWindow

END = "2024-12-04"


def session_body(result: Any) -> dict[str, Any]:
    body = json.loads(result.stdout)
    (session,) = [s for s in body["sessions"] if s["session_date"] == str(SESSION)]
    return session  # type: ignore[no-any-return]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_tick_env(monkeypatch)


def _provider_minutes(url: str, window: TickWindow) -> dict[int, Minute]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            f"SELECT ts_event, open, high, low, close, volume FROM {BAR_1M_TABLE}"
            " WHERE instrument_id = %s AND ts_event BETWEEN %s AND %s",
            (window.instrument_id, window.open_ns, window.close_ns),
        ).fetchall()
    return {int(r[0]): tuple(int(v) for v in r[1:]) for r in rows}  # type: ignore[misc]


def _expected_counts(
    tick: dict[int, Minute], provider: dict[int, Minute]
) -> dict[str, int]:
    counts = {"agree": 0, "differ": 0, "provider_only": 0, "tick_only": 0}
    for bucket in tick.keys() | provider.keys():
        if bucket not in tick:
            counts["provider_only"] += 1
        elif bucket not in provider:
            counts["tick_only"] += 1
        else:
            counts["agree" if tick[bucket] == provider[bucket] else "differ"] += 1
    return counts


async def test_the_commands_minutes_equal_an_independent_bucketing(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    """The derived bar day against the cut ``tbbo`` day (D9): minutes the cut
    kept whole agree; the rest differ or exist only on the provider side."""
    db = await seed_parity(migrated_tick_db, session_migrated_db, tmp_path)
    provider = _provider_minutes(db.url, db.window)
    assert db.tick_minutes and provider

    result = await run_parity(db, "--start", str(SESSION), "--end", END)

    session = session_body(result)
    assert session["counts"] == _expected_counts(db.tick_minutes, provider)
    assert (session["tick_volume"], session["provider_volume"]) == (
        sum(m[4] for m in db.tick_minutes.values()),
        sum(m[4] for m in provider.values()),
    )
    counts = session["counts"]
    assert counts["agree"] > 0 and counts["provider_only"] > 0  # real records
    non_agreeing = sum(n for cls, n in counts.items() if cls != "agree")
    assert result.exit_code == (3 if non_agreeing else 0)
    assert all(
        m["class"] != "agree" or m["at_close"] for m in session["minutes_listed"]
    )


async def test_a_minute_with_trades_and_no_provider_bar_is_tick_only(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    """``tick_only`` needs a minute the provider lacks; the real bar day has
    none, so this test hand-builds a bar day that omits the first trade minute."""
    omitted: list[int] = []

    def bars_for(db: ParityDb) -> list[dict[str, int]]:
        first, *rest = sorted(db.tick_minutes)
        omitted.append(first)
        return [bar(db.window.instrument_id, b, db.tick_minutes[b]) for b in rest]

    db = await seed_parity(migrated_tick_db, session_migrated_db, tmp_path, bars_for)

    result = await run_parity(db, "--start", str(SESSION), "--end", END)

    session = session_body(result)
    assert session["counts"]["tick_only"] == 1
    assert session["counts"]["differ"] == session["counts"]["provider_only"] == 0
    (minute,) = [m for m in session["minutes_listed"] if m["class"] == "tick_only"]
    assert minute["provider"] is None and minute["tick"] is not None
    assert result.exit_code == 3


async def test_the_minute_stamped_at_the_session_close_is_flagged_and_listed(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    """Issue #70: a provider bar stamped at the close, with no ticks there."""

    def bars_for(db: ParityDb) -> list[dict[str, int]]:
        bars = [bar(db.window.instrument_id, b, m) for b, m in db.tick_minutes.items()]
        return [
            *bars,
            bar(db.window.instrument_id, db.window.close_ns, (10, 11, 9, 10, 4)),
        ]

    db = await seed_parity(migrated_tick_db, session_migrated_db, tmp_path, bars_for)

    session = session_body(await run_parity(db, "--start", str(SESSION), "--end", END))

    assert (session["close_stamped"], session["close_stamped_agree"]) == (1, 0)
    (listed,) = [m for m in session["minutes_listed"] if m["at_close"]]
    assert listed["class"] == "provider_only"
    assert listed["time"].endswith(":00.000000000Z")


async def test_a_day_with_ticks_and_no_bars_is_skipped_with_both_statuses(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    """Session 12-05 has a tick unit and no bar unit: skipped, never a
    disagreement, and the exit code is the compared session's alone."""

    def bars_for(db: ParityDb) -> list[dict[str, int]]:
        return [bar(db.window.instrument_id, b, m) for b, m in db.tick_minutes.items()]

    db = await seed_parity(migrated_tick_db, session_migrated_db, tmp_path, bars_for)
    seed_tier_days(db.url, [date(2024, 12, 5)], TickSchema.TBBO)

    result = await run_parity(db, "--start", str(SESSION), "--end", "2024-12-06")

    body = json.loads(result.stdout)
    (skipped,) = [s for s in body["skipped"] if s["session_date"] == "2024-12-05"]
    assert skipped["tick_status"] == "complete"
    assert skipped["bar_status"] != "complete"
    assert session_body(result)["counts"]["agree"] == len(db.tick_minutes)
    assert body["exit_code"] == result.exit_code
