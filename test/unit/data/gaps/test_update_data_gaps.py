"""Unit tests for update_data_gaps algorithm correctness.

Patches compute_missing_ranges and the advisory_lock so no live DB is needed.
Verifies that the function's DB-mutation behavior matches the spec for all
required fixture cases.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from manta_trading.constants import MAX_RETRY_COUNT
from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.data.gaps.compute_missing_ranges import GapRange
from manta_trading.data.gaps.update_data_gaps import UpdateResult, update_data_gaps
from manta_trading.data.quality.fetch_status import FetchStatus

UTC = timezone.utc


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 14, 30, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers for building mock connection
# ---------------------------------------------------------------------------


class _CapturingCursor:
    """Cursor that records execute() calls and returns configurable results."""

    def __init__(self) -> None:
        self.executes: list[tuple] = []
        self._fetchone_val: tuple | None = None
        self._fetchall_val: list[tuple] = []
        self.rowcount: int = 0
        self.description: list = [
            MagicMock(name="gap_start"),
            MagicMock(name="gap_end"),
            MagicMock(name="fetch_status"),
            MagicMock(name="attempt_count"),
        ]
        # Make description items have .name attr
        self.description[0].name = "gap_start"
        self.description[1].name = "gap_end"
        self.description[2].name = "fetch_status"
        self.description[3].name = "attempt_count"

    def __enter__(self) -> "_CapturingCursor":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executes.append((sql, params))

    def fetchone(self) -> tuple | None:
        return self._fetchone_val

    def fetchall(self) -> list[tuple]:
        return self._fetchall_val


def _make_conn(
    prior_rows: list[dict] | None = None,
) -> tuple[MagicMock, list[_CapturingCursor]]:
    """Return (conn, cursor_list) where cursors track execute calls in order."""
    cursors: list[_CapturingCursor] = [_CapturingCursor() for _ in range(20)]
    cursor_iter = iter(cursors)

    # Configure first cursor to return prior rows for _fetch_prior_rows
    if prior_rows:
        # _fetch_prior_rows is called first; its cursor returns prior_rows
        # The cursor's fetchall is called inside _fetch_prior_rows
        # We need fetchall to return the prior_rows as tuples in order
        first_cur = cursors[0]
        first_cur._fetchall_val = [
            (r["gap_start"], r["gap_end"], r["fetch_status"], r["attempt_count"])
            for r in prior_rows
        ]
        # Also second cursor (for _reset_terminal_rows if needed or _delete_intersecting)
        # subsequent cursors return empty results by default

    conn = MagicMock()
    conn.cursor = MagicMock(side_effect=lambda: next(cursor_iter))
    return conn, cursors


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUpdateDataGaps:
    def _call(
        self,
        *,
        prior_rows: list[dict] | None = None,
        gap_ranges: list[GapRange] | None = None,
        fetch_status: FetchStatus | None = FetchStatus.UNKNOWN,
        outcome: LastAttemptOutcome = LastAttemptOutcome.SUCCESS,
        force_reset_terminal: bool = False,
    ) -> tuple[UpdateResult, list[_CapturingCursor]]:
        conn, cursors = _make_conn(prior_rows or [])
        gap_ranges = gap_ranges or []

        with patch(
            "manta_trading.data.gaps.update_data_gaps.compute_missing_ranges",
            return_value=gap_ranges,
        ):
            result = update_data_gaps(
                conn,
                "AAPL",
                "daily",
                _dt(2024, 1, 1),
                _dt(2024, 12, 31),
                fetch_status,
                force_reset_terminal=force_reset_terminal,
                outcome=outcome,
            )
        return result, cursors

    def test_first_attempt_inserts_with_count_1(self) -> None:
        gap = GapRange("AAPL", "daily", _dt(2024, 1, 2), _dt(2024, 1, 5))
        result, cursors = self._call(gap_ranges=[gap], fetch_status=FetchStatus.UNKNOWN)

        assert result.gaps_inserted == 1
        assert result.gaps_promoted_exhausted == 0
        # Find the INSERT execute call
        insert_calls = [
            c
            for cur in cursors
            for sql, params in cur.executes
            if "INSERT INTO data_gaps" in sql
            for c in [(sql, params)]
        ]
        assert len(insert_calls) == 1
        _, params = insert_calls[0]
        assert params[6] == 1  # attempt_count = 1

    def test_success_outcome_inserts_no_gap_rows(self) -> None:
        result, _ = self._call(
            gap_ranges=[], fetch_status=None, outcome=LastAttemptOutcome.SUCCESS
        )
        assert result.gaps_inserted == 0

    def test_retry_exhausted_promoted_at_max_count(self) -> None:
        # Prior rows have attempt_count = MAX_RETRY_COUNT - 1 so next insert → MAX
        prior = [
            {
                "gap_start": _dt(2024, 1, 2),
                "gap_end": _dt(2024, 1, 5),
                "fetch_status": str(FetchStatus.UNKNOWN),
                "attempt_count": MAX_RETRY_COUNT - 1,
            }
        ]
        gap = GapRange("AAPL", "daily", _dt(2024, 1, 2), _dt(2024, 1, 5))
        result, cursors = self._call(
            prior_rows=prior,
            gap_ranges=[gap],
            fetch_status=FetchStatus.UNKNOWN,
        )
        assert result.gaps_promoted_exhausted == 1
        insert_calls = [
            (sql, params)
            for cur in cursors
            for sql, params in cur.executes
            if "INSERT INTO data_gaps" in sql
        ]
        assert len(insert_calls) == 1
        assert insert_calls[0][1][4] == str(FetchStatus.RETRY_EXHAUSTED)

    def test_force_reset_terminal_clears_terminal_rows(self) -> None:
        result, cursors = self._call(
            gap_ranges=[],
            fetch_status=None,
            force_reset_terminal=True,
        )
        # Should have a DELETE with IN (...PROVIDER_HOLE, RETRY_EXHAUSTED...)
        delete_calls = [
            (sql, params)
            for cur in cursors
            for sql, params in cur.executes
            if "DELETE FROM data_gaps" in sql and "PROVIDER_HOLE" in str(params)
        ]
        assert len(delete_calls) >= 1

    def test_acquisition_state_upserted(self) -> None:
        result, cursors = self._call(outcome=LastAttemptOutcome.SUCCESS)
        state_calls = [
            (sql, params)
            for cur in cursors
            for sql, params in cur.executes
            if "acquisition_state" in sql and "INSERT" in sql
        ]
        assert len(state_calls) == 1
        assert "success" in str(state_calls[0][1])

    def test_different_fetch_status_resets_carry_forward(self) -> None:
        """A repeat with different fetch_status treats it as first attempt."""
        prior = [
            {
                "gap_start": _dt(2024, 1, 2),
                "gap_end": _dt(2024, 1, 5),
                "fetch_status": str(FetchStatus.FAILED_RETRYABLE),  # different
                "attempt_count": 3,
            }
        ]
        gap = GapRange("AAPL", "daily", _dt(2024, 1, 2), _dt(2024, 1, 5))
        result, cursors = self._call(
            prior_rows=prior,
            gap_ranges=[gap],
            fetch_status=FetchStatus.UNKNOWN,  # different from prior
        )
        insert_calls = [
            params
            for cur in cursors
            for sql, params in cur.executes
            if "INSERT INTO data_gaps" in sql
        ]
        assert len(insert_calls) == 1
        # attempt_count should be 1 (no carry-forward since status differs)
        assert insert_calls[0][6] == 1

    def test_delete_intersecting_called_before_insert(self) -> None:
        gap = GapRange("AAPL", "daily", _dt(2024, 1, 2), _dt(2024, 1, 5))
        _, cursors = self._call(gap_ranges=[gap])
        all_sqls = [sql for cur in cursors for sql, _ in cur.executes]
        # DELETE should appear before INSERT
        delete_idx = next(
            (i for i, s in enumerate(all_sqls) if "DELETE FROM data_gaps" in s), -1
        )
        insert_idx = next(
            (i for i, s in enumerate(all_sqls) if "INSERT INTO data_gaps" in s), -1
        )
        assert delete_idx != -1, "Expected a DELETE"
        assert insert_idx != -1, "Expected an INSERT"
        assert delete_idx < insert_idx


class TestUpdateDataGapsPrecomputedRanges:
    """Tests for the minute-path precomputed_ranges parameter (slice 162)."""

    def _call_minute(
        self,
        *,
        prior_rows: list[dict] | None = None,
        precomputed_ranges: list[GapRange] | None,
        fetch_status: FetchStatus | None = FetchStatus.UNKNOWN,
        outcome: LastAttemptOutcome = LastAttemptOutcome.SUCCESS,
    ) -> tuple[UpdateResult, list[_CapturingCursor]]:
        conn, cursors = _make_conn(prior_rows or [])
        result = update_data_gaps(
            conn,
            "AAPL",
            "minute",
            _dt(2024, 1, 1),
            _dt(2024, 12, 31),
            fetch_status,
            outcome=outcome,
            precomputed_ranges=precomputed_ranges,
        )
        return result, cursors

    def test_precomputed_ranges_inserts_exactly_those_ranges_not_one_span(self) -> None:
        ranges = [
            GapRange("AAPL", "minute", _dt(2024, 1, 3), _dt(2024, 1, 3)),
            GapRange("AAPL", "minute", _dt(2024, 6, 10), _dt(2024, 6, 12)),
        ]
        result, cursors = self._call_minute(precomputed_ranges=ranges)

        assert result.gaps_inserted == 2
        insert_calls = [
            params
            for cur in cursors
            for sql, params in cur.executes
            if "INSERT INTO data_gaps" in sql
        ]
        assert len(insert_calls) == 2
        inserted_spans = {(p[2], p[3]) for p in insert_calls}
        assert inserted_spans == {
            (_dt(2024, 1, 3), _dt(2024, 1, 3)),
            (_dt(2024, 6, 10), _dt(2024, 6, 12)),
        }
        # Not the legacy single [from_ts, to_ts] span
        assert (_dt(2024, 1, 1), _dt(2024, 12, 31)) not in inserted_spans

    def test_precomputed_ranges_empty_list_inserts_nothing(self) -> None:
        result, _ = self._call_minute(precomputed_ranges=[])
        assert result.gaps_inserted == 0

    def test_carry_forward_preserved_for_precomputed_range(self) -> None:
        """A re-seed over a prior status carries forward attempt_count.

        Slice 921 Decision 4: the minute seed carries the prior count forward
        UNCHANGED. It ran before any provider call, so incrementing here would
        count a retry the provider was never asked to supply.
        """
        prior = [
            {
                "gap_start": _dt(2024, 6, 10),
                "gap_end": _dt(2024, 6, 12),
                "fetch_status": str(FetchStatus.UNKNOWN),
                "attempt_count": 2,
            }
        ]
        ranges = [GapRange("AAPL", "minute", _dt(2024, 6, 10), _dt(2024, 6, 12))]
        result, cursors = self._call_minute(
            prior_rows=prior,
            precomputed_ranges=ranges,
            fetch_status=FetchStatus.UNKNOWN,
        )
        insert_calls = [
            params
            for cur in cursors
            for sql, params in cur.executes
            if "INSERT INTO data_gaps" in sql
        ]
        assert len(insert_calls) == 1
        assert insert_calls[0][6] == 2  # carried forward 2 -> 2, not 2 -> 3

    def test_omitting_precomputed_ranges_keeps_legacy_single_span_behavior(
        self,
    ) -> None:
        """Daily-style legacy minute behavior is byte-for-byte unchanged."""
        result, cursors = self._call_minute(
            precomputed_ranges=None, fetch_status=FetchStatus.UNKNOWN
        )
        assert result.gaps_inserted == 1
        insert_calls = [
            params
            for cur in cursors
            for sql, params in cur.executes
            if "INSERT INTO data_gaps" in sql
        ]
        assert len(insert_calls) == 1
        # Legacy behavior: single span covering the full [from_ts, to_ts] window
        assert insert_calls[0][2] == _dt(2024, 1, 1)
        assert insert_calls[0][3] == _dt(2024, 12, 31)

    def test_omitting_precomputed_ranges_with_no_fetch_status_inserts_nothing(
        self,
    ) -> None:
        result, _ = self._call_minute(precomputed_ranges=None, fetch_status=None)
        assert result.gaps_inserted == 0

    def test_precomputed_ranges_with_no_fetch_status_raises(self) -> None:
        """Ranges with a null status would be silently discarded — reject it."""
        ranges = [GapRange("AAPL", "minute", _dt(2024, 6, 10), _dt(2024, 6, 12))]
        with pytest.raises(ValueError, match="fetch_status_for_unfilled"):
            self._call_minute(precomputed_ranges=ranges, fetch_status=None)

    def test_empty_precomputed_ranges_with_no_fetch_status_raises(self) -> None:
        """The guard keys on the parameter being supplied, not on it being non-empty."""
        with pytest.raises(ValueError, match="fetch_status_for_unfilled"):
            self._call_minute(precomputed_ranges=[], fetch_status=None)


# ---------------------------------------------------------------------------
# Slice 921 Section 2 — the seed path consumes retries with no provider answer
# ---------------------------------------------------------------------------


class _FakeDataGaps:
    """A minimal in-memory ``data_gaps`` honoring the three statements the
    seed path issues: the prior-row SELECT, the containment DELETE, and the
    INSERT ... ON CONFLICT DO UPDATE. Enough to run ``update_data_gaps``
    repeatedly and watch a row's ``attempt_count`` evolve across seeds.
    """

    def __init__(self) -> None:
        # (gap_start, gap_end) -> {"fetch_status", "attempt_count"}
        self.rows: dict[tuple[datetime, datetime], dict] = {}

    def cursor(self) -> "_FakeCursor":
        return _FakeCursor(self)

    def execute(self, sql: str, params: tuple) -> list[tuple]:
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT gap_start, gap_end, fetch_status"):
            _symbol, _gran, from_ts, to_ts = params
            return [
                (start, end, row["fetch_status"], row["attempt_count"])
                for (start, end), row in sorted(self.rows.items())
                if start >= from_ts and end <= to_ts
            ]
        if normalized.startswith("DELETE FROM data_gaps"):
            _symbol, _gran, from_ts, to_ts = params
            for key in [k for k in self.rows if k[0] >= from_ts and k[1] <= to_ts]:
                del self.rows[key]
            return []
        if normalized.startswith("INSERT INTO data_gaps"):
            (
                _symbol,
                _gran,
                gap_start,
                gap_end,
                fetch_status,
                _last_attempt_ts,
                attempt_count,
            ) = params
            self.rows[(gap_start, gap_end)] = {
                "fetch_status": fetch_status,
                "attempt_count": attempt_count,
            }
            return []
        # acquisition_state and any other statement: no stored state needed.
        return []


class _FakeCursor:
    def __init__(self, store: _FakeDataGaps) -> None:
        self._store = store
        self._result: list[tuple] = []
        self.rowcount = 0

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._result = self._store.execute(sql, params)
        self.rowcount = len(self._result)

    def fetchall(self) -> list[tuple]:
        return self._result

    def fetchone(self) -> tuple | None:
        return self._result[0] if self._result else None


def _seed_once(
    store: _FakeDataGaps,
    gap_range: GapRange,
    from_ts: datetime,
    to_ts: datetime,
) -> UpdateResult:
    """One coverage-aware minute seed — exactly what the daemon issues when
    the seed gate fires, with NO provider call anywhere in the transaction."""
    conn = MagicMock()
    conn.cursor = MagicMock(side_effect=store.cursor)
    return update_data_gaps(
        conn,
        "AAPL",
        "minute",
        from_ts,
        to_ts,
        FetchStatus.UNKNOWN,
        outcome=LastAttemptOutcome.PARTIAL,
        precomputed_ranges=[gap_range],
    )


class TestSeedingDoesNotConsumeRetries:
    """Slice 921 Tasks 2.1/2.2 — seeding no longer burns retries.

    The defect: ``update_data_gaps`` Step 5 computed
    ``attempt_count = prior_count + 1`` and promoted to ``RETRY_EXHAUSTED`` at
    ``MAX_RETRY_COUNT`` on the SEED, with no provider response anywhere in the
    transaction. The minute daemon re-seeds every cycle, so a symbol the
    provider was never asked about burned a retry per cycle and reached a
    terminal status on its own. That is the path that promoted 7,755 rows to
    RETRY_EXHAUSTED on production 2026-09-07 — no fetch had failed for them;
    the seeder counted them out.

    These tests are the Task 2.1 reproduction with its assertions inverted:
    the same four-plus seeds with no fetch between them must now leave the row
    UNKNOWN at its original count.
    """

    _FROM = datetime(2026, 9, 1, tzinfo=UTC)
    _TO = datetime(2026, 9, 30, tzinfo=UTC)
    _RANGE = GapRange(
        "AAPL",
        "minute",
        datetime(2026, 9, 3, 13, 30, tzinfo=UTC),
        datetime(2026, 9, 3, 20, 0, tzinfo=UTC),
    )
    _KEY = (_RANGE.gap_start_utc, _RANGE.gap_end_utc)

    def test_reseeding_never_increments_attempt_count(self) -> None:
        store = _FakeDataGaps()
        counts: list[int] = []
        for _ in range(MAX_RETRY_COUNT + 2):
            _seed_once(store, self._RANGE, self._FROM, self._TO)
            counts.append(store.rows[self._KEY]["attempt_count"])
        # A never-answered range stays at zero however often it is re-seeded.
        assert counts == [0] * (MAX_RETRY_COUNT + 2)

    def test_reseeding_alone_cannot_promote_to_retry_exhausted(self) -> None:
        store = _FakeDataGaps()
        results = [
            _seed_once(store, self._RANGE, self._FROM, self._TO)
            for _ in range(MAX_RETRY_COUNT + 2)
        ]
        row = store.rows[self._KEY]
        assert row["fetch_status"] == str(FetchStatus.UNKNOWN)
        assert all(r.gaps_promoted_exhausted == 0 for r in results)

    def test_a_prior_fetch_count_is_carried_forward_unchanged(self) -> None:
        """A row the provider HAS answered keeps the count that fetch earned;
        re-seeding neither resets it nor advances it toward exhaustion."""
        store = _FakeDataGaps()
        # Stand in for a real fetch attempt recorded by _record_minute_attempt.
        store.rows[self._KEY] = {
            "fetch_status": str(FetchStatus.UNKNOWN),
            "attempt_count": 3,
        }
        for _ in range(MAX_RETRY_COUNT):
            _seed_once(store, self._RANGE, self._FROM, self._TO)
        row = store.rows[self._KEY]
        assert row["attempt_count"] == 3
        assert row["fetch_status"] == str(FetchStatus.UNKNOWN)

    def test_the_daily_path_still_increments_on_its_post_fetch_call(self) -> None:
        """The daily callers invoke update_data_gaps AFTER their fetch, so
        their increment IS backed by a provider answer and must not move
        (daily.py:619 and :757). Only the pre-fetch minute seed changed."""
        conn = MagicMock()
        cursors = [_CapturingCursor() for _ in range(20)]
        cursors[0]._fetchall_val = [
            (
                self._RANGE.gap_start_utc,
                self._RANGE.gap_end_utc,
                str(FetchStatus.UNKNOWN),
                2,
            )
        ]
        cursor_iter = iter(cursors)
        conn.cursor = MagicMock(side_effect=lambda: next(cursor_iter))
        with patch(
            "manta_trading.data.gaps.update_data_gaps.compute_missing_ranges",
            return_value=[
                GapRange(
                    "AAPL",
                    "daily",
                    self._RANGE.gap_start_utc,
                    self._RANGE.gap_end_utc,
                )
            ],
        ):
            update_data_gaps(
                conn,
                "AAPL",
                "daily",
                self._FROM,
                self._TO,
                FetchStatus.UNKNOWN,
                outcome=LastAttemptOutcome.PARTIAL,
            )
        inserts = [
            params
            for cur in cursors
            for sql, params in cur.executes
            if "INSERT INTO data_gaps" in sql
        ]
        assert len(inserts) == 1
        assert inserts[0][6] == 3, "daily still carries 2 -> 3"
