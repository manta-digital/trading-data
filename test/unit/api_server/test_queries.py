"""Unit tests for ``api_server.queries`` — the shared symbol-existence seek."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import psycopg
import pytest

from manta_trading.api_server.queries import (
    _DAILY_HEAD_ONLY_SQL,
    _MINUTE_HEAD_ONLY_SQL,
    _SYMBOL_EXISTS_SQL,
    _SYMBOL_HEAD_SQL,
    UniverseEdgeCache,
    _as_bound,
    fetch_symbol_coverage,
    fetch_symbol_head,
    fetch_universe_edges,
    merge_available_ranges,
    symbol_exists,
)
from manta_trading.constants import CAGG_FRESHNESS_CACHE_TTL, CycleGranularity

_T0 = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
"""Fixed clock origin for the cache tests — TTL expiry without sleeping."""


def _conn(fetchone_result: object) -> MagicMock:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = fetchone_result
    return conn


def test_returns_true_when_a_row_is_found() -> None:
    assert symbol_exists(_conn((1,)), "AAPL") is True


def test_returns_false_when_no_row_is_found() -> None:
    assert symbol_exists(_conn(None), "ZZZZ") is False


def test_symbol_is_passed_as_a_bound_parameter() -> None:
    """Never interpolated: ``symbol`` is caller-supplied path input."""
    conn = _conn((1,))
    symbol_exists(conn, "AAPL")
    sql, params = conn.execute.call_args.args
    assert params == ("AAPL",)
    assert "AAPL" not in sql


def test_query_is_a_bare_existence_seek() -> None:
    """No projection and no join — the caller needs one bit, on the empty path
    of a request that already did its real work."""
    assert "FROM instruments" in _SYMBOL_EXISTS_SQL
    assert "WHERE symbol = %s" in _SYMBOL_EXISTS_SQL
    assert "SELECT 1" in _SYMBOL_EXISTS_SQL


@pytest.mark.parametrize(
    "error",
    [
        psycopg.errors.QueryCanceled("cancelled"),
        psycopg.OperationalError("connection lost"),
    ],
)
def test_failures_propagate_rather_than_defaulting(error: Exception) -> None:
    """The function must not decide 404-vs-200 on a failed lookup; the
    app-level handlers turn these into 504 and 500 respectively (D5 addendum)."""
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.side_effect = error
    with pytest.raises(type(error)):
        symbol_exists(conn, "AAPL")


# --- available ranges (slice 187 D2, D3, D8) ---------------------------------


def _rows_conn(rows: list[tuple[object, ...]]) -> MagicMock:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchall.return_value = rows
    return conn


_MINUTE = CycleGranularity.MINUTE
_DAILY = CycleGranularity.DAILY

_EDGE_MINUTE = date(2026, 7, 24)
_EDGE_DAILY = date(2026, 6, 12)
_EDGES: dict[CycleGranularity, date | None] = {
    _MINUTE: _EDGE_MINUTE,
    _DAILY: _EDGE_DAILY,
}


class TestMergeAvailableRanges:
    """The four D2 cases, each its own case so a regression names itself.

    Pure function: no database, no connection. The COALESCE order is the whole
    design, and each direction is load bearing in a different scenario.
    """

    @pytest.mark.parametrize(
        ("case", "coverage", "head", "expected"),
        [
            (
                # Data spanning the edge — the common case. Coverage holds the
                # true floor, the head probe the true leading edge.
                "spanning",
                {_DAILY: (date(1993, 1, 29), date(2026, 6, 12))},
                {_DAILY: (date(2026, 6, 15), date(2026, 8, 3))},
                {_DAILY: (date(1993, 1, 29), date(2026, 8, 3))},
            ),
            (
                # Delisted, or ingest stopped: nothing past the horizon, so the
                # head probe is empty and coverage must supply both endpoints.
                "entirely-before-edge",
                {_DAILY: (date(1998, 2, 2), date(2015, 7, 9))},
                {_DAILY: (None, None)},
                {_DAILY: (date(1998, 2, 2), date(2015, 7, 9))},
            ),
            (
                # A symbol whose data begins after the coverage horizon has no
                # coverage row at all. This is why `start` coalesces from
                # coverage *first* rather than taking it unconditionally.
                "entirely-after-edge",
                {_DAILY: (None, None)},
                {_DAILY: (date(2026, 6, 20), date(2026, 8, 3))},
                {_DAILY: (date(2026, 6, 20), date(2026, 8, 3))},
            ),
            (
                # No data in the family: omitted, matching the pre-187 contract.
                "no-data",
                {_DAILY: (None, None)},
                {_DAILY: (None, None)},
                {},
            ),
        ],
    )
    def test_d2_cases(
        self,
        case: str,
        coverage: dict[CycleGranularity, tuple[date | None, date | None]],
        head: dict[CycleGranularity, tuple[date | None, date | None]],
        expected: dict[CycleGranularity, tuple[date, date]],
    ) -> None:
        assert merge_available_ranges(coverage, head) == expected, case

    def test_families_are_merged_independently(self) -> None:
        # A symbol can be daily-only or minute-only; one family's absence must
        # not affect the other's range.
        merged = merge_available_ranges(
            {_DAILY: (date(1962, 1, 2), date(2026, 6, 12))},
            {
                _DAILY: (date(2026, 6, 15), date(2026, 8, 3)),
                _MINUTE: (None, None),
            },
        )
        assert merged == {_DAILY: (date(1962, 1, 2), date(2026, 8, 3))}
        assert _MINUTE not in merged

    def test_missing_family_keys_are_treated_as_absent_not_an_error(self) -> None:
        # A symbol in neither cagg yields mappings without the key at all.
        assert merge_available_ranges({}, {}) == {}

    def test_head_end_wins_over_coverage_end(self) -> None:
        # The direction that makes the leading edge exact despite a stale cagg
        # (D2/D5) — the single most important assertion in this class.
        merged = merge_available_ranges(
            {_DAILY: (date(2000, 1, 3), date(2026, 6, 12))},
            {_DAILY: (date(2026, 6, 15), date(2026, 8, 3))},
        )
        assert merged[_DAILY][1] == date(2026, 8, 3)


class TestFetchUniverseEdges:
    def test_returns_both_families_even_when_a_cagg_is_empty(self) -> None:
        # A missing edge must be None ("no bound available"), never absent —
        # fetch_symbol_head skips on None rather than running unbounded.
        conn = _rows_conn([(_MINUTE.value, _EDGE_MINUTE)])
        edges = fetch_universe_edges(conn)
        assert edges == {_MINUTE: _EDGE_MINUTE, _DAILY: None}

    def test_single_round_trip_with_no_per_symbol_predicate(self) -> None:
        conn = _rows_conn([])
        fetch_universe_edges(conn)
        assert conn.execute.call_count == 1
        sql = conn.execute.call_args.args[0]
        assert "WHERE" not in sql.upper()


class TestFetchSymbolCoverage:
    def test_one_round_trip_for_both_families(self) -> None:
        conn = _rows_conn(
            [
                (_MINUTE.value, date(2004, 1, 2), date(2026, 7, 24)),
                (_DAILY.value, date(1993, 1, 29), date(2026, 6, 12)),
            ]
        )
        result = fetch_symbol_coverage(conn, "SPY")
        assert conn.execute.call_count == 1
        assert result[_MINUTE] == (date(2004, 1, 2), date(2026, 7, 24))
        assert result[_DAILY] == (date(1993, 1, 29), date(2026, 6, 12))

    def test_symbol_absent_from_both_caggs_yields_null_ranges(self) -> None:
        # An aggregate always produces a row; the merge omits the family.
        conn = _rows_conn([(_MINUTE.value, None, None), (_DAILY.value, None, None)])
        result = fetch_symbol_coverage(conn, "ZZZZ")
        assert result == {_MINUTE: (None, None), _DAILY: (None, None)}
        assert merge_available_ranges(result, {}) == {}


class TestFetchSymbolHead:
    def test_bound_is_timestamptz_typed_not_a_date(self) -> None:
        """Regression guard for a 3,100 ms defect found on prod 2026-08-04.

        Binding the coverage edge as a ``datetime.date`` makes PostgreSQL
        resolve ``timestamptz > date`` through a timezone-dependent conversion
        the planner cannot use for chunk exclusion, so it plans across all 3,371
        daily chunks: 3,100 ms against 7 ms for the identical instant bound as
        an aware ``datetime``. Same rows either way — only the plan differs,
        which is exactly why a value-equality test would not have caught it.
        """
        conn = _rows_conn([])
        fetch_symbol_head(conn, "SPY", _EDGES)
        params = conn.execute.call_args.args[1]
        bounds = [p for p in params if isinstance(p, (date, datetime))]
        assert bounds, "the head probe must carry a time bound"
        for bound in bounds:
            assert isinstance(bound, datetime), (
                f"bound {bound!r} is a bare date; bind an aware datetime or the "
                "planner cannot prune chunks (187 D8)"
            )
            assert bound.tzinfo is not None, "bound must be timezone-aware"

    def test_bound_preserves_the_edge_instant(self) -> None:
        assert _as_bound(_EDGE_DAILY) == datetime(2026, 6, 12, tzinfo=UTC)

    def test_one_round_trip_when_both_families_have_an_edge(self) -> None:
        conn = _rows_conn([])
        fetch_symbol_head(conn, "SPY", _EDGES)
        assert conn.execute.call_count == 1

    def test_family_without_an_edge_is_skipped_never_run_unbounded(self) -> None:
        """No unbounded fallback (D3): the unbounded form is the 2.5-4.0 s
        statement this slice exists to remove."""
        conn = _rows_conn([(_DAILY.value, date(2026, 6, 15), date(2026, 8, 3))])
        result = fetch_symbol_head(conn, "SPY", {_MINUTE: None, _DAILY: _EDGE_DAILY})
        assert _MINUTE not in result
        for call in conn.execute.call_args_list:
            sql = call.args[0]
            if "minute_5min_ohlcv" in sql:
                raise AssertionError("minute branch ran without a bound")
            assert "time" in sql and ">" in sql

    def test_no_edges_at_all_issues_no_query(self) -> None:
        conn = _rows_conn([])
        assert fetch_symbol_head(conn, "SPY", {_MINUTE: None, _DAILY: None}) == {}
        assert conn.execute.call_count == 0

    def test_every_branch_carries_a_time_predicate(self) -> None:
        # Success criterion 1, asserted on the SQL itself: no branch of the
        # union may lack a bound the planner can prune on.
        for sql in (_SYMBOL_HEAD_SQL, _MINUTE_HEAD_ONLY_SQL, _DAILY_HEAD_ONLY_SQL):
            for branch in sql.split("UNION ALL"):
                assert "WHERE" in branch.upper()
                assert "time_bucket > %s" in branch or "time > %s" in branch


class TestUniverseEdgeCache:
    """Slice 187 D3 — the edges are identical per symbol and cost ~32 ms, so
    they are read once per TTL window rather than once per request."""

    @staticmethod
    def _clock(moment: datetime) -> Callable[[], datetime]:
        return lambda: moment

    def test_cold_call_queries_and_returns_the_value(self) -> None:
        conn = _rows_conn([(_MINUTE.value, _EDGE_MINUTE), (_DAILY.value, _EDGE_DAILY)])
        cache = UniverseEdgeCache()
        edges = cache.get(conn, now=self._clock(_T0))
        assert edges == {_MINUTE: _EDGE_MINUTE, _DAILY: _EDGE_DAILY}
        assert conn.execute.call_count == 1

    def test_warm_call_inside_the_ttl_issues_no_query(self) -> None:
        conn = _rows_conn([(_MINUTE.value, _EDGE_MINUTE), (_DAILY.value, _EDGE_DAILY)])
        cache = UniverseEdgeCache()
        cache.get(conn, now=self._clock(_T0))
        edges = cache.get(
            conn, now=self._clock(_T0 + CAGG_FRESHNESS_CACHE_TTL - timedelta(seconds=1))
        )
        assert conn.execute.call_count == 1, "a warm read must not re-query"
        assert edges == {_MINUTE: _EDGE_MINUTE, _DAILY: _EDGE_DAILY}

    def test_call_after_expiry_requeries(self) -> None:
        conn = _rows_conn([(_MINUTE.value, _EDGE_MINUTE), (_DAILY.value, _EDGE_DAILY)])
        cache = UniverseEdgeCache()
        cache.get(conn, now=self._clock(_T0))
        cache.get(
            conn, now=self._clock(_T0 + CAGG_FRESHNESS_CACHE_TTL + timedelta(seconds=1))
        )
        assert conn.execute.call_count == 2

    def test_one_query_populates_both_families(self) -> None:
        # A cache miss must cost one fetch, not one per family.
        conn = _rows_conn([(_MINUTE.value, _EDGE_MINUTE), (_DAILY.value, _EDGE_DAILY)])
        cache = UniverseEdgeCache()
        edges = cache.get(conn, now=self._clock(_T0))
        assert set(edges) == {_MINUTE, _DAILY}
        assert conn.execute.call_count == 1

    def test_clear_forces_a_refetch(self) -> None:
        conn = _rows_conn([(_MINUTE.value, _EDGE_MINUTE), (_DAILY.value, _EDGE_DAILY)])
        cache = UniverseEdgeCache()
        cache.get(conn, now=self._clock(_T0))
        cache.clear()
        cache.get(conn, now=self._clock(_T0))
        assert conn.execute.call_count == 2

    def test_concurrent_cold_reads_issue_exactly_one_query(self) -> None:
        """The route reads this from executor threads, so a cold cache under
        concurrency must not become a thundering herd against a pool of 8."""
        conn = _rows_conn([(_MINUTE.value, _EDGE_MINUTE), (_DAILY.value, _EDGE_DAILY)])
        # Make the fetch slow enough that every thread is inside get() at once
        # if the lock is not held across it.
        original = conn.execute

        def _slow(*args: object, **kwargs: object) -> object:
            time.sleep(0.05)
            return original(*args, **kwargs)

        conn.execute = _slow  # type: ignore[method-assign]

        cache = UniverseEdgeCache()
        results: list[dict[CycleGranularity, date | None]] = []
        threads = [
            threading.Thread(
                target=lambda: results.append(cache.get(conn, now=self._clock(_T0)))
            )
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(results) == 8
        assert all(r == {_MINUTE: _EDGE_MINUTE, _DAILY: _EDGE_DAILY} for r in results)
        assert original.call_count == 1, (
            "a cold cache under concurrency must issue one query, not one per "
            f"waiting thread; got {original.call_count}"
        )
