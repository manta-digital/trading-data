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
    CaggFreshness,
    cagg_freshness_for_session,
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
        assert "m.time >  ts.session_open_utc" in sql_text
        assert "m.time <= ts.session_close_utc" in sql_text
        assert "bars.newest_in_session IS NULL" in sql_text
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
        assert "bars.day_bars > 0" in sql_text

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
        for sql_text in (per_symbol, universe):
            assert "m.time >  ts.session_open_utc" in sql_text
            assert "m.time <= ts.session_close_utc" in sql_text
            assert "IS NULL" in sql_text

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


class TestVerifyMode:
    """``--verify`` turns acceptance from a wait into an action.

    Its whole value is that the numbers it certifies are the numbers
    ``mt data health`` reports. Two implementations of one measurement drift —
    an inclusive vs. exclusive close, a stale calendar literal — and the
    cutover would sign off on a figure the health check does not reproduce.
    """

    @staticmethod
    def _run_verify(repair: Any, **overrides: Any) -> tuple[bool, Any]:
        """Run --verify with the health-check seams patched; return (result, mocks)."""
        from manta_trading.cli.commands.minute_session_mass import (
            SessionMass,
            TradingSessionBounds,
        )

        session = TradingSessionBounds(
            session_open_utc=datetime(2026, 9, 8, 13, 30, tzinfo=UTC),
            session_close_utc=datetime(2026, 9, 8, 20, 0, tzinfo=UTC),
        )
        mass = overrides.get(
            "mass", SessionMass(total_bars=1_989_000, symbols_meeting_min_bars=7_259)
        )
        mocks = {
            "select": MagicMock(return_value=overrides.get("session", session)),
            "fetch_candidates": MagicMock(return_value=[session]),
            "fetch_mass": MagicMock(return_value=mass),
            "rule": MagicMock(
                return_value=(overrides.get("health_ok", True), "detail")
            ),
            "truncated": MagicMock(return_value=overrides.get("truncated", {})),
            "freshness": MagicMock(
                return_value=overrides.get(
                    "freshness",
                    CaggFreshness(
                        last_refresh=datetime(2026, 9, 9, 5, 0, tzinfo=UTC),
                        newest_bar_written=datetime(2026, 9, 9, 4, 30, tzinfo=UTC),
                    ),
                )
            ),
        }
        conn = MagicMock()

        with (
            patch.object(repair, "cagg_freshness_for_session", mocks["freshness"]),
            patch.object(repair, "select_judged_session", mocks["select"]),
            patch.object(repair, "fetch_candidate_sessions", mocks["fetch_candidates"]),
            patch.object(repair, "fetch_session_mass", mocks["fetch_mass"]),
            patch.object(repair, "check_minute_session_mass", mocks["rule"]),
            patch.object(repair, "build_truncated_day_index", mocks["truncated"]),
            patch.object(repair, "run_check", return_value=repair.CheckReport()),
        ):
            result = repair.run_verify(conn, ["AAPL"])
        return result, mocks

    def test_it_uses_the_health_checks_judged_session_selector(
        self, repair: Any
    ) -> None:
        _, mocks = self._run_verify(repair)
        mocks["select"].assert_called_once()

    def test_it_uses_the_health_checks_mass_query(self, repair: Any) -> None:
        _, mocks = self._run_verify(repair)
        mocks["fetch_mass"].assert_called_once()

    def test_it_uses_the_health_checks_rule_function(self, repair: Any) -> None:
        """The `minute session mass` line must be the health check's own
        verdict, not a restatement of it."""
        _, mocks = self._run_verify(repair)
        mocks["rule"].assert_called_once()

    def test_it_writes_nothing(self, repair: Any) -> None:
        conn = MagicMock()
        from manta_trading.cli.commands.minute_session_mass import (
            SessionMass,
            TradingSessionBounds,
        )

        session = TradingSessionBounds(
            session_open_utc=datetime(2026, 9, 8, 13, 30, tzinfo=UTC),
            session_close_utc=datetime(2026, 9, 8, 20, 0, tzinfo=UTC),
        )
        with (
            patch.object(repair, "select_judged_session", return_value=session),
            patch.object(repair, "fetch_candidate_sessions", return_value=[session]),
            patch.object(
                repair,
                "fetch_session_mass",
                return_value=SessionMass(1_989_000, 7_259),
            ),
            patch.object(repair, "check_minute_session_mass", return_value=(True, "d")),
            patch.object(repair, "build_truncated_day_index", return_value={}),
            patch.object(
                repair,
                "cagg_freshness_for_session",
                return_value=CaggFreshness(last_refresh=None, newest_bar_written=None),
            ),
            patch.object(repair, "run_check", return_value=repair.CheckReport()),
        ):
            repair.run_verify(conn, ["AAPL"])
        executed = " ".join(
            str(call.args[0])
            for call in conn.cursor.return_value.__enter__.return_value.execute.call_args_list
            if call.args
        ).upper()
        for statement in ("INSERT", "UPDATE ", "DELETE"):
            assert statement not in executed

    def test_the_bar_count_is_judged_against_the_acceptance_bar(
        self, repair: Any, capsys: Any
    ) -> None:
        """Not the health floor. 975,000 clears the alarm threshold but does
        not demonstrate the fix, so the cutover bar is stricter."""
        from manta_trading.cli.commands.minute_session_mass import SessionMass

        # Between the health floor (975,000) and the acceptance bar.
        result, _ = self._run_verify(
            repair, mass=SessionMass(total_bars=980_000, symbols_meeting_min_bars=7_259)
        )
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert f"{repair.VERIFY_MIN_SESSION_BARS:,}" in out
        assert result is False

    def test_the_acceptance_bar_is_stricter_than_the_health_floor(
        self, repair: Any
    ) -> None:
        from manta_trading.constants import (
            HEALTH_MINUTE_SESSION_MIN_BARS_PER_MINUTE,
        )

        regular_session_minutes = 390
        health_floor = (
            HEALTH_MINUTE_SESSION_MIN_BARS_PER_MINUTE * regular_session_minutes
        )
        assert health_floor == 975_000
        assert repair.VERIFY_MIN_SESSION_BARS > health_floor

    def test_residual_truncation_is_judged_against_zero(
        self, repair: Any, capsys: Any
    ) -> None:
        result, _ = self._run_verify(
            repair, truncated={"AAPL": frozenset({date(2026, 9, 8)})}
        )
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "(bar: 0)" in out
        assert result is False

    def test_a_clean_run_passes_every_bar(self, repair: Any, capsys: Any) -> None:
        result, _ = self._run_verify(repair)
        out = capsys.readouterr().out
        assert "FAIL" not in out
        assert result is True

    def test_truncation_outside_the_active_universe_is_not_counted(
        self, repair: Any
    ) -> None:
        """A delisted symbol's old truncation is not the cutover's business."""
        result, _ = self._run_verify(
            repair, truncated={"DELISTED": frozenset({date(2026, 9, 8)})}
        )
        assert result is True

    def test_no_judgeable_session_fails_rather_than_passing(
        self, repair: Any, capsys: Any
    ) -> None:
        result, _ = self._run_verify(repair, session=None)
        assert result is False
        assert "no completed session to judge" in capsys.readouterr().out


class TestScanScopeAndTimeout:
    """Two defects found by running --verify against production.

    (1) ``--verify --symbol AAPL`` ran the UNIVERSE-wide truncation scan, so a
    one-symbol question paid a 13,083-symbol answer and exceeded the session's
    statement timeout. (2) The resulting QueryCanceled escaped as a traceback
    rather than a message an operator can act on.
    """

    @staticmethod
    def _conn() -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchall = MagicMock(return_value=[])
        conn.cursor = MagicMock(return_value=cur)
        return conn

    def test_the_scan_bounds_the_bar_time_directly(self) -> None:
        """The redundant-looking `m.time >= %s` is what makes TimescaleDB
        exclude chunks.

        Bounding the time only through the trading_sessions join leaves the
        planner scanning EVERY chunk of 22 years of minute_ohlcv — measured
        2026-09-09, a single-symbol scan hit the statement timeout that way,
        and 0.10 s with this predicate. Delete it and the repair becomes
        unrunnable, silently and only against production-sized history.
        """
        from manta_trading.data.gaps.repair_921 import (
            build_truncated_day_index,
            window_start_utc,
        )

        conn = self._conn()
        build_truncated_day_index(conn)
        sql_text, params = conn.cursor.return_value.execute.call_args.args
        assert "AND m.time  >= %s" in sql_text
        # The window start bounds the session; the bar time is bound a day
        # earlier so a session's 00:00 UTC spillover bar stays in view (#22).
        from datetime import timedelta

        assert params[0] == window_start_utc()
        assert params[1] == window_start_utc() - timedelta(days=1)

    def test_a_symbol_list_narrows_the_scan(self) -> None:
        from manta_trading.data.gaps.repair_921 import build_truncated_day_index

        conn = self._conn()
        build_truncated_day_index(conn, symbols=["AAPL", "MSFT"])
        sql_text, params = conn.cursor.return_value.execute.call_args.args
        assert "m.symbol = ANY(%s)" in sql_text
        assert ["AAPL", "MSFT"] in params

    def test_no_symbol_list_scans_the_universe(self) -> None:
        from manta_trading.data.gaps.repair_921 import build_truncated_day_index

        conn = self._conn()
        build_truncated_day_index(conn)
        sql_text, _ = conn.cursor.return_value.execute.call_args.args
        assert "m.symbol = ANY(%s)" not in sql_text

    def test_the_scan_sets_its_own_statement_timeout(self) -> None:
        """An analytical scan run by an operator needs its own budget, not the
        serving session's."""
        from manta_trading.data.gaps.repair_921 import (
            TRUNCATION_SCAN_TIMEOUT,
            build_truncated_day_index,
        )

        conn = self._conn()
        build_truncated_day_index(conn)
        executed = " ".join(
            str(call.args[0])
            for call in conn.cursor.return_value.execute.call_args_list
            if call.args
        )
        assert "statement_timeout" in executed
        assert TRUNCATION_SCAN_TIMEOUT in executed

    def test_a_cancelled_scan_raises_rather_than_returning_empty(self) -> None:
        """An unmeasured universe reported as a clean one is exactly the
        silent pass this slice removes."""
        import psycopg

        from manta_trading.data.gaps.repair_921 import (
            RepairScanTimeout,
            build_truncated_day_index,
        )

        conn = self._conn()
        cur = conn.cursor.return_value
        cur.execute = MagicMock(
            side_effect=[None, psycopg.errors.QueryCanceled("timeout")]
        )
        with pytest.raises(RepairScanTimeout, match="truncation scan exceeded"):
            build_truncated_day_index(conn)

    def test_the_timeout_message_names_the_way_out(self) -> None:
        import psycopg

        from manta_trading.data.gaps.repair_921 import (
            RepairScanTimeout,
            build_truncated_day_index,
        )

        conn = self._conn()
        cur = conn.cursor.return_value
        cur.execute = MagicMock(
            side_effect=[None, psycopg.errors.QueryCanceled("timeout")]
        )
        with pytest.raises(RepairScanTimeout) as exc:
            build_truncated_day_index(conn)
        assert "--symbol" in str(exc.value)

    def test_a_scan_timeout_exits_refused_not_a_traceback(
        self, repair: Any, capsys: Any
    ) -> None:
        from manta_trading.data.gaps.repair_921 import RepairScanTimeout

        with (
            patch.object(repair, "_database_url", return_value="postgresql://x/y"),
            patch.object(repair, "_run", side_effect=RepairScanTimeout("too slow")),
        ):
            code = repair.main(["--check"])
        assert code == repair.EXIT_REFUSED
        assert "scan refused" in capsys.readouterr().err


class TestVerifyWaitsForTheCagg:
    """#22: --verify read "1 bar" minutes after a pass had written 50,806,
    because the health cagg is materialized-only and refreshed hourly. The
    probe compares the cagg's last refresh with the session's newest
    ``created_at``; a stale cagg is a WAIT, never a verdict."""

    def test_a_bar_written_after_the_last_refresh_is_stale(self) -> None:
        assert CaggFreshness(
            last_refresh=datetime(2026, 9, 10, 11, 12, tzinfo=UTC),
            newest_bar_written=datetime(2026, 9, 10, 11, 30, tzinfo=UTC),
        ).stale

    def test_a_refresh_after_the_newest_bar_is_fresh(self) -> None:
        assert not CaggFreshness(
            last_refresh=datetime(2026, 9, 10, 12, 12, tzinfo=UTC),
            newest_bar_written=datetime(2026, 9, 10, 11, 30, tzinfo=UTC),
        ).stale

    def test_no_refresh_on_record_with_bars_is_stale(self) -> None:
        assert CaggFreshness(
            last_refresh=None,
            newest_bar_written=datetime(2026, 9, 10, 11, 30, tzinfo=UTC),
        ).stale

    def test_no_bars_at_all_is_not_stale(self) -> None:
        """Nothing to materialize — the mass read (zero) is the truth."""
        assert not CaggFreshness(last_refresh=None, newest_bar_written=None).stale

    def test_the_probe_reads_the_job_stats_and_the_session_chunk(self) -> None:
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cur
        refresh = datetime(2026, 9, 10, 11, 12, tzinfo=UTC)
        newest = datetime(2026, 9, 10, 11, 30, tzinfo=UTC)
        cur.fetchone.side_effect = [(refresh,), (newest,)]
        open_ = datetime(2026, 9, 9, 13, 30, tzinfo=UTC)
        close = datetime(2026, 9, 9, 20, 0, tzinfo=UTC)
        result = cagg_freshness_for_session(conn, "minute_4hour_ohlcv", open_, close)
        assert result == CaggFreshness(last_refresh=refresh, newest_bar_written=newest)
        jobs_sql, jobs_params = cur.execute.call_args_list[0].args
        assert "policy_refresh_continuous_aggregate" in jobs_sql
        assert jobs_params == ("minute_4hour_ohlcv",)
        bars_sql, bars_params = cur.execute.call_args_list[1].args
        assert "max(created_at)" in bars_sql
        assert bars_params == (open_, close)

    def test_verify_reports_wait_and_does_not_judge(
        self, repair: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        stale = CaggFreshness(
            last_refresh=datetime(2026, 9, 10, 11, 12, tzinfo=UTC),
            newest_bar_written=datetime(2026, 9, 10, 11, 30, tzinfo=UTC),
        )
        result, mocks = TestVerifyMode._run_verify(repair, freshness=stale)
        assert result is False
        mocks["fetch_mass"].assert_not_called()
        assert "[WAIT]" in capsys.readouterr().out

    def test_verify_judges_when_fresh(
        self, repair: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        result, mocks = TestVerifyMode._run_verify(repair)
        assert result is True
        mocks["fetch_mass"].assert_called_once()
        assert "[WAIT]" not in capsys.readouterr().out
