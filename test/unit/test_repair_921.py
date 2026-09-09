"""Unit tests for the slice-921 minute-session repair.

``scripts/`` is not a package, so the CLI module is loaded by path with the
directory on ``sys.path`` — the pattern ``test_cutover_267.py`` established.
Nothing here touches a database, a host, or the provider: the writer
(``update_data_gaps``) and the systemd probe are faked.

What a fake writer CANNOT reach is deliberately left to
``test/integration/test_repair_921_live.py``: every SC2 property — that
``force_reset_terminal=True`` resets the in-window terminal rows and only
those, that carry-forward preserves ``attempt_count`` so a second ``--apply``
is genuinely zero net change, that pre-window rows survive — is a property of
``update_data_gaps`` itself, and a fake stands in for exactly the code under
test.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from manta_trading.constants import REPAIR_921_WINDOW_START
from manta_trading.data.gaps.repair_921 import (
    SymbolFindings,
    repair_window_for,
    window_start_utc,
)

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture(scope="module")
def repair() -> Any:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "repair_921_minute_sessions", SCRIPTS / "repair_921_minute_sessions.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _findings(
    *,
    symbol: str = "AAPL",
    truncated: frozenset[date] = frozenset(),
    zero_width: int = 0,
    terminal: int = 0,
    midnight: int = 0,
    straddling: datetime | None = None,
) -> SymbolFindings:
    return SymbolFindings(
        symbol=symbol,
        truncated_days=truncated,
        zero_width_rows=zero_width,
        session_open_ended_terminal_rows=terminal,
        midnight_ended_rows=midnight,
        straddling_gap_start=straddling,
    )


class TestWritesGoThroughTheSingleWriter:
    """SC2: the repair issues no ``data_gaps`` SQL of its own."""

    @staticmethod
    def _run_repair(repair: Any, findings: SymbolFindings) -> tuple[Any, Any, Any]:
        """Repair one symbol; return (update mock, lock mock, conn)."""
        update_mock = MagicMock(return_value=MagicMock(gaps_inserted=3))
        lock_cm = MagicMock()
        lock_cm.__enter__ = MagicMock(return_value=None)
        lock_cm.__exit__ = MagicMock(return_value=False)
        lock_mock = MagicMock(return_value=lock_cm)

        conn = MagicMock()
        txn = MagicMock()
        txn.__enter__ = MagicMock(return_value=txn)
        txn.__exit__ = MagicMock(return_value=False)
        conn.transaction.return_value = txn

        with (
            patch.object(repair, "inspect_symbol", return_value=findings),
            patch.object(
                repair, "build_symbol_minute_coverage", return_value={"AAPL": set()}
            ),
            patch.object(repair, "compute_missing_minute_sessions", return_value=[]),
            patch.object(repair, "update_data_gaps", update_mock),
            patch.object(repair, "advisory_lock", lock_mock),
        ):
            repair.repair_symbol(
                conn, "AAPL", now_midnight=datetime(2026, 9, 9, tzinfo=UTC)
            )
        return update_mock, lock_mock, conn

    def test_update_data_gaps_is_called_with_force_reset_terminal(
        self, repair: Any
    ) -> None:
        update_mock, _, _ = self._run_repair(
            repair, _findings(truncated=frozenset({date(2026, 7, 20)}))
        )
        update_mock.assert_called_once()
        assert update_mock.call_args.kwargs["force_reset_terminal"] is True

    def test_the_write_happens_inside_the_advisory_lock(self, repair: Any) -> None:
        _, lock_mock, _ = self._run_repair(
            repair, _findings(truncated=frozenset({date(2026, 7, 20)}))
        )
        lock_mock.assert_called_once()
        args = lock_mock.call_args.args
        assert args[1] == "AAPL"
        assert args[2] == "minute"

    def test_the_script_issues_no_other_write_against_data_gaps(
        self, repair: Any
    ) -> None:
        """Every mutation goes through update_data_gaps — there is one writer
        and one set of semantics."""
        _, _, conn = self._run_repair(
            repair, _findings(truncated=frozenset({date(2026, 7, 20)}))
        )
        executed = " ".join(
            str(call.args[0])
            for call in conn.cursor.return_value.__enter__.return_value.execute.call_args_list
            if call.args
        ).upper()
        for statement in (
            "INSERT INTO DATA_GAPS",
            "UPDATE DATA_GAPS",
            "DELETE FROM DATA_GAPS",
        ):
            assert statement not in executed

    def test_the_truncated_days_are_passed_as_uncovered(self, repair: Any) -> None:
        """Without this the coverage index reports the truncated day as
        covered and the session is never re-seeded."""
        days = frozenset({date(2026, 7, 20), date(2026, 7, 21)})
        compute_mock = MagicMock(return_value=[])
        conn = MagicMock()
        txn = MagicMock()
        txn.__enter__ = MagicMock(return_value=txn)
        txn.__exit__ = MagicMock(return_value=False)
        conn.transaction.return_value = txn
        lock_cm = MagicMock()
        lock_cm.__enter__ = MagicMock(return_value=None)
        lock_cm.__exit__ = MagicMock(return_value=False)

        with (
            patch.object(
                repair, "inspect_symbol", return_value=_findings(truncated=days)
            ),
            patch.object(
                repair, "build_symbol_minute_coverage", return_value={"AAPL": set()}
            ),
            patch.object(repair, "compute_missing_minute_sessions", compute_mock),
            patch.object(repair, "update_data_gaps"),
            patch.object(repair, "advisory_lock", return_value=lock_cm),
        ):
            repair.repair_symbol(
                conn, "AAPL", now_midnight=datetime(2026, 9, 9, tzinfo=UTC)
            )
        assert compute_mock.call_args.kwargs["uncovered_days"] == set(days)

    def test_a_symbol_needing_nothing_is_not_written(self, repair: Any) -> None:
        update_mock, lock_mock, _ = self._run_repair(repair, _findings())
        update_mock.assert_not_called()
        lock_mock.assert_not_called()

    def test_unavailable_coverage_skips_rather_than_seeding(self, repair: Any) -> None:
        """build_symbol_minute_coverage fails safe to None on a stale cagg.
        Seeding from an untrusted read is what slice 162 exists to prevent."""
        update_mock = MagicMock()
        conn = MagicMock()
        with (
            patch.object(
                repair,
                "inspect_symbol",
                return_value=_findings(truncated=frozenset({date(2026, 7, 20)})),
            ),
            patch.object(repair, "build_symbol_minute_coverage", return_value=None),
            patch.object(repair, "update_data_gaps", update_mock),
        ):
            inserted = repair.repair_symbol(
                conn, "AAPL", now_midnight=datetime(2026, 9, 9, tzinfo=UTC)
            )
        assert inserted == 0
        update_mock.assert_not_called()


class TestRefusesWhileAPassIsRunning:
    def test_it_exits_non_zero_and_writes_nothing(self, repair: Any) -> None:
        apply_mock = MagicMock()
        with (
            patch.object(repair, "_unit_active", return_value=True),
            patch.object(repair, "_database_url", return_value="postgresql://x/y"),
            patch.object(repair, "psycopg") as pg,
            patch.object(repair, "_active_symbols", return_value=["AAPL"]),
            patch.object(repair, "run_apply", apply_mock),
        ):
            pg.connect.return_value.__enter__ = MagicMock(return_value=MagicMock())
            pg.connect.return_value.__exit__ = MagicMock(return_value=False)
            with pytest.raises(SystemExit) as exc:
                repair.main(["--apply"])
        assert exc.value.code == repair.EXIT_REFUSED
        apply_mock.assert_not_called()

    def test_check_is_allowed_while_a_pass_runs(self, repair: Any) -> None:
        """--check is read-only, so it never has to wait for a pass."""
        with (
            patch.object(repair, "_unit_active", return_value=True),
            patch.object(repair, "_database_url", return_value="postgresql://x/y"),
            patch.object(repair, "psycopg") as pg,
            patch.object(repair, "_active_symbols", return_value=["AAPL"]),
            patch.object(
                repair, "run_check", return_value=repair.CheckReport()
            ) as check_mock,
        ):
            pg.connect.return_value.__enter__ = MagicMock(return_value=MagicMock())
            pg.connect.return_value.__exit__ = MagicMock(return_value=False)
            assert repair.main(["--check"]) == repair.EXIT_OK
        check_mock.assert_called_once()

    def test_both_pass_units_are_checked(self, repair: Any) -> None:
        assert "mt-minute-pass.service" in repair.PASS_UNITS
        assert "mt-daily-pass.service" in repair.PASS_UNITS


class TestStraddlingRowWindow:
    """Task 6.3's decision: widen back to the straddling row's gap_start."""

    def test_no_straddling_row_uses_the_window_start(self) -> None:
        assert repair_window_for(_findings()) == window_start_utc()

    def test_a_straddling_row_widens_the_window_back_to_its_start(self) -> None:
        earlier = datetime(2026, 6, 1, 13, 30, tzinfo=UTC)
        assert repair_window_for(_findings(straddling=earlier)) == earlier

    def test_the_widening_is_bounded_by_the_row_not_by_history(self) -> None:
        """The window moves to that row's own gap_start and no further, so
        provider holes behind it are never pulled into the reset."""
        earlier = datetime(2026, 6, 1, 13, 30, tzinfo=UTC)
        widened = repair_window_for(_findings(straddling=earlier))
        assert widened == earlier
        assert widened > datetime(2004, 1, 1, tzinfo=UTC)

    def test_a_row_starting_after_the_window_never_narrows_it(self) -> None:
        """min() guards against a later value narrowing the repair scope."""
        later = datetime(2026, 8, 1, tzinfo=UTC)
        assert repair_window_for(_findings(straddling=later)) == window_start_utc()


class TestNeedsRepairPredicate:
    def test_truncated_days_alone_need_repair(self) -> None:
        assert _findings(truncated=frozenset({date(2026, 7, 20)})).needs_repair

    def test_row_shapes_alone_need_repair(self) -> None:
        assert _findings(zero_width=1).needs_repair
        assert _findings(terminal=1).needs_repair
        assert _findings(midnight=1).needs_repair

    def test_a_clean_symbol_does_not(self) -> None:
        assert not _findings().needs_repair

    def test_a_straddling_row_alone_is_not_a_reason_to_rewrite(self) -> None:
        """A straddling row changes the WINDOW when a repair happens; it is
        not itself evidence that this symbol was truncated."""
        assert not _findings(straddling=datetime(2026, 6, 1, tzinfo=UTC)).needs_repair


class TestWindowConstant:
    def test_the_window_starts_the_day_the_seeder_shipped(self) -> None:
        assert REPAIR_921_WINDOW_START == date(2026, 7, 16)

    def test_window_start_utc_is_midnight_utc_on_that_date(self) -> None:
        assert window_start_utc() == datetime(2026, 7, 16, tzinfo=UTC)


class TestTruncatedDayPredicate:
    """The truncation signature, defined once in ``find_truncated_days``.

    A symbol-day is truncated when the newest bar stored for that session is
    at or before the session's open — exactly what a range ending at the open
    produces, because EODHD honors ``to`` precisely and returns the opening
    minute and nothing after it.
    """

    @staticmethod
    def _conn(rows: list[tuple]) -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchall = MagicMock(return_value=rows)
        conn.cursor = MagicMock(return_value=cur)
        return conn

    def test_the_predicate_compares_the_newest_bar_to_the_session_open(
        self,
    ) -> None:
        from manta_trading.data.gaps.repair_921 import find_truncated_days

        conn = self._conn([])
        find_truncated_days(conn, "AAPL")
        sql_text, params = conn.cursor.return_value.execute.call_args.args
        # The signature itself: max(time) at or before the open.
        assert "max(m.time)" in sql_text
        assert "bars.newest <= ts.session_open_utc" in sql_text
        assert params[0] == "AAPL"

    def test_it_is_bounded_to_the_repair_window(self) -> None:
        from manta_trading.data.gaps.repair_921 import find_truncated_days

        conn = self._conn([])
        find_truncated_days(conn, "AAPL")
        sql_text, params = conn.cursor.return_value.execute.call_args.args
        assert "ts.session_open_utc >= %s" in sql_text
        assert params[1] == window_start_utc()

    def test_it_ignores_sessions_that_have_not_closed(self) -> None:
        """A session still in progress legitimately has few bars; judging it
        truncated would re-seed the day the pass is currently collecting."""
        from manta_trading.data.gaps.repair_921 import find_truncated_days

        conn = self._conn([])
        find_truncated_days(conn, "AAPL")
        sql_text, _ = conn.cursor.return_value.execute.call_args.args
        assert "ts.session_close_utc <= now()" in sql_text

    def test_a_session_with_no_bars_at_all_is_not_truncated(self) -> None:
        """No bars is a missing session, which the coverage diff already
        finds. Truncation is specifically 'present but cut at the open', and
        conflating them would widen the repair beyond its window."""
        from manta_trading.data.gaps.repair_921 import find_truncated_days

        conn = self._conn([])
        find_truncated_days(conn, "AAPL")
        sql_text, _ = conn.cursor.return_value.execute.call_args.args
        assert "bars.newest IS NOT NULL" in sql_text

    def test_returned_dates_become_the_uncovered_set(self) -> None:
        from manta_trading.data.gaps.repair_921 import find_truncated_days

        conn = self._conn([(date(2026, 7, 20),), (date(2026, 7, 21),)])
        assert find_truncated_days(conn, "AAPL") == frozenset(
            {date(2026, 7, 20), date(2026, 7, 21)}
        )

    def test_the_straddling_query_is_a_true_intersection(self) -> None:
        """It must find rows that START before the window and END inside it —
        precisely the rows update_data_gaps' containment delete misses."""
        from manta_trading.data.gaps.repair_921 import find_straddling_gap_start

        conn = self._conn([])
        conn.cursor.return_value.fetchone = MagicMock(return_value=(None,))
        find_straddling_gap_start(conn, "AAPL")
        sql_text, _ = conn.cursor.return_value.execute.call_args.args
        assert "gap_start < %s" in sql_text
        assert "gap_end   >= %s" in sql_text


class TestTruncationPredicateHasOneDefinition:
    """The signature is spelled by two functions — the per-symbol probe and
    the universe-wide index — because the per-symbol form costs 0.5-0.9 s
    against production and 13k of them is ~2.5 hours. They must agree, or the
    repair measures one thing and fixes another.
    """

    @staticmethod
    def _sql_of(fn, *args) -> str:
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchall = MagicMock(return_value=[])
        cur.fetchone = MagicMock(return_value=(None,))
        conn.cursor = MagicMock(return_value=cur)
        fn(conn, *args)
        return cur.execute.call_args.args[0]

    def test_both_compare_the_newest_bar_to_the_session_open(self) -> None:
        from manta_trading.data.gaps.repair_921 import (
            build_truncated_day_index,
            find_truncated_days,
        )

        per_symbol = self._sql_of(find_truncated_days, "AAPL")
        universe = self._sql_of(build_truncated_day_index)
        assert "<= ts.session_open_utc" in per_symbol
        assert "<= ts.session_open_utc" in universe

    def test_both_ignore_sessions_that_have_not_closed(self) -> None:
        from manta_trading.data.gaps.repair_921 import (
            build_truncated_day_index,
            find_truncated_days,
        )

        for sql in (
            self._sql_of(find_truncated_days, "AAPL"),
            self._sql_of(build_truncated_day_index),
        ):
            assert "ts.session_close_utc <= now()" in sql

    def test_both_bound_to_the_repair_window(self) -> None:
        from manta_trading.data.gaps.repair_921 import (
            build_truncated_day_index,
            find_truncated_days,
        )

        for sql in (
            self._sql_of(find_truncated_days, "AAPL"),
            self._sql_of(build_truncated_day_index),
        ):
            assert "ts.session_open_utc >= %s" in sql

    def test_the_index_groups_by_symbol_and_session(self) -> None:
        from manta_trading.data.gaps.repair_921 import build_truncated_day_index

        sql = self._sql_of(build_truncated_day_index)
        assert "GROUP BY" in sql
        assert "HAVING" in sql

    def test_inspect_symbol_prefers_the_index_over_the_per_symbol_probe(
        self,
    ) -> None:
        """Passing the index must avoid the expensive probe entirely."""
        from manta_trading.data.gaps import repair_921

        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchone = MagicMock(return_value=(0, 0, 0))
        cur.fetchall = MagicMock(return_value=[])
        conn.cursor = MagicMock(return_value=cur)

        with patch.object(repair_921, "find_truncated_days") as probe:
            findings = repair_921.inspect_symbol(
                conn,
                "AAPL",
                truncated_index={"AAPL": frozenset({date(2026, 7, 20)})},
            )
        probe.assert_not_called()
        assert findings.truncated_days == frozenset({date(2026, 7, 20)})

    def test_a_symbol_absent_from_the_index_has_no_truncated_days(self) -> None:
        from manta_trading.data.gaps import repair_921

        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchone = MagicMock(return_value=(0, 0, 0))
        cur.fetchall = MagicMock(return_value=[])
        conn.cursor = MagicMock(return_value=cur)

        findings = repair_921.inspect_symbol(conn, "NOSUCH", truncated_index={})
        assert findings.truncated_days == frozenset()
