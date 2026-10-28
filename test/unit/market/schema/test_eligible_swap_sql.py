"""Rendering of migration 064's swap SQL (slice 173)."""

from __future__ import annotations

import re

from manta_trading.market.schema.eligible_swap_sql import (
    SWAP_NAMES,
    SwapNames,
    swap_drop_statements,
    swap_rename_statements,
)

_RENAME = re.compile(r"ALTER MATERIALIZED VIEW (\w+) RENAME TO (\w+)")
_DROP = re.compile(r"DROP MATERIALIZED VIEW (\w+)")


def _match(pattern: re.Pattern[str], sql: str) -> re.Match[str]:
    match = pattern.search(sql)
    assert match is not None, sql
    return match


def test_swap_names_are_the_five_triples() -> None:
    assert SWAP_NAMES == (
        SwapNames(
            "minute_5min_ohlcv", "minute_5min_ohlcv_next", "minute_5min_ohlcv_prev"
        ),
        SwapNames(
            "minute_15min_ohlcv", "minute_15min_ohlcv_next", "minute_15min_ohlcv_prev"
        ),
        SwapNames(
            "minute_hourly_ohlcv",
            "minute_hourly_ohlcv_next",
            "minute_hourly_ohlcv_prev",
        ),
        SwapNames(
            "minute_4hour_ohlcv", "minute_4hour_ohlcv_next", "minute_4hour_ohlcv_prev"
        ),
        SwapNames("minute_coverage", "minute_coverage_next", "minute_coverage_prev"),
    )


def test_renames_move_every_current_away_before_any_next_moves_in() -> None:
    pairs = [_match(_RENAME, s).groups() for s in swap_rename_statements()]
    assert pairs == [(n.current, n.prev) for n in SWAP_NAMES] + [
        (n.next, n.current) for n in SWAP_NAMES
    ]


def test_current_is_renamed_away_only_while_its_next_twin_exists() -> None:
    """Otherwise a rerun on a finished swap would rename the live cagg to
    ``_prev`` and then drop it."""
    for names, sql in zip(SWAP_NAMES, swap_rename_statements(), strict=False):
        assert f"to_regclass('public.{names.next}') IS NOT NULL" in sql
        assert f"to_regclass('public.{names.prev}') IS NULL" in sql


def test_drops_run_coverage_first_and_only_prev_names() -> None:
    dropped = [_match(_DROP, s).group(1) for s in swap_drop_statements()]
    assert dropped == [n.prev for n in reversed(SWAP_NAMES)]
    assert dropped[0] == "minute_coverage_prev"


def test_every_statement_is_guarded() -> None:
    for sql in swap_rename_statements() + swap_drop_statements():
        assert sql.startswith("DO $$ BEGIN IF to_regclass(")
        assert sql.endswith("END IF; END $$;")
