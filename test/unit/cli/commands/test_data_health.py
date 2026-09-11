"""Unit tests for ``mt data health`` (slice 919)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands.minute_session_mass import (
    SessionMass,
    TradingSessionBounds,
    check_minute_session_mass,
    collecting_firing_finished_at,
    fetch_candidate_sessions,
    fetch_session_mass,
    select_judged_session,
)
from manta_trading.constants import (
    HEALTH_MINUTE_SESSION_CALENDAR,
    HEALTH_MINUTE_SESSION_MIN_BARS_PER_MINUTE,
    HEALTH_MINUTE_SESSION_MIN_SYMBOLS,
    HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT,
)
from manta_trading.cli.commands.health import (
    EXIT_HEALTHY,
    EXIT_UNAVAILABLE,
    EXIT_UNHEALTHY,
    HealthCheck,
    check_cagg,
    check_phase_recency,
    check_raw_freshness,
    gather,
    render,
)

runner = CliRunner()
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


class TestRules:
    def test_raw_freshness_within_threshold_passes(self) -> None:
        c = check_raw_freshness(
            "minute data", NOW - timedelta(days=1), now=NOW, threshold=timedelta(days=4)
        )
        assert c.ok and "minute data" == c.name

    def test_raw_freshness_past_threshold_fails(self) -> None:
        c = check_raw_freshness(
            "minute data",
            NOW - timedelta(days=24),
            now=NOW,
            threshold=timedelta(days=4),
        )
        assert not c.ok and "24.0 d ago" in c.detail

    def test_raw_freshness_no_rows_fails(self) -> None:
        assert not check_raw_freshness(
            "daily data", None, now=NOW, threshold=timedelta(days=5)
        ).ok

    def test_cagg_verdict_is_passed_through(self) -> None:
        assert check_cagg("minute_5min_ohlcv", False, "lag=21 days").ok is False
        assert (
            check_cagg("minute_5min_ohlcv", True, "fresh").name
            == "cagg minute_5min_ohlcv"
        )

    def test_the_quota_check_is_gone(self) -> None:
        """Slice 921 Decision 6 removed the EODHD quota-headroom floor.

        Normal operation is designed to consume the whole daily allowance on
        backfill, so no floor is right — the 2026-08-31 reading of 99,996 of
        100,000 used was HEALTHY, and any threshold that fires nightly is
        noise. What mattered about a starved night was its consequence, which
        the minute-session-mass check now measures directly.
        """
        from manta_trading.cli.commands import health as health_module

        assert not hasattr(health_module, "check_quota")
        assert not hasattr(health_module, "fetch_quota")

    def test_phase_recency_recent_passes_stale_fails_never_fails(self) -> None:
        limit = timedelta(hours=3)
        assert check_phase_recency(
            "kalshi trades", NOW - timedelta(hours=1), now=NOW, threshold=limit
        ).ok
        assert not check_phase_recency(
            "kalshi trades", NOW - timedelta(hours=5), now=NOW, threshold=limit
        ).ok
        assert not check_phase_recency(
            "kalshi trades", None, now=NOW, threshold=limit
        ).ok

    def test_render_names_the_failing_count(self) -> None:
        text = render([HealthCheck("a", True, "x"), HealthCheck("b", False, "y")])
        assert text.splitlines()[-1] == "UNHEALTHY: 1 of 2 checks failing"
        assert render([HealthCheck("a", True, "x")]).splitlines()[-1] == "healthy"


def _settings(**overrides):
    s = MagicMock()
    s.timescale_db_url = "postgresql://ts/db"
    s.eodhd_api_key = "k"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _invoke(settings, checks):
    with (
        patch("manta_trading.cli.app.Settings", return_value=settings),
        patch("manta_trading.cli.app.setup_logging"),
        patch("manta_trading.cli.commands.health.psycopg.connect"),
        patch("manta_trading.cli.commands.health.gather", return_value=checks),
    ):
        return runner.invoke(app, ["data", "health"])


class TestCommand:
    def test_all_passing_exits_zero(self) -> None:
        result = _invoke(_settings(), [HealthCheck("minute data", True, "fresh")])
        assert result.exit_code == EXIT_HEALTHY
        assert "healthy" in result.output

    def test_any_failing_exits_one(self) -> None:
        result = _invoke(
            _settings(),
            [
                HealthCheck("minute data", True, "fresh"),
                HealthCheck("cagg x", False, "lag"),
            ],
        )
        assert result.exit_code == EXIT_UNHEALTHY
        assert "FAIL cagg x" in result.output

    def test_missing_database_url_exits_two(self) -> None:
        result = _invoke(_settings(timescale_db_url=None), [])
        assert result.exit_code == EXIT_UNAVAILABLE

    def test_it_runs_without_an_eodhd_key(self) -> None:
        """Slice 921 Decision 6: with the quota check gone every remaining
        check reads the database, so the provider key is no longer required —
        and demanding it would fail a health run for a credential it does not
        use."""
        checks = [HealthCheck("a", True, "x")]
        with (
            patch(
                "manta_trading.cli.app.Settings",
                return_value=_settings(eodhd_api_key=None),
            ),
            patch("manta_trading.cli.app.setup_logging"),
            patch("manta_trading.cli.commands.health.psycopg.connect"),
            patch("manta_trading.cli.commands.health.gather", return_value=checks),
        ):
            result = runner.invoke(app, ["data", "health"])
        assert result.exit_code == EXIT_HEALTHY

    def test_json_payload_carries_every_check(self) -> None:
        import json

        checks = [HealthCheck("a", True, "x"), HealthCheck("b", False, "y")]
        with (
            patch("manta_trading.cli.app.Settings", return_value=_settings()),
            patch("manta_trading.cli.app.setup_logging"),
            patch("manta_trading.cli.commands.health.psycopg.connect"),
            patch("manta_trading.cli.commands.health.gather", return_value=checks),
        ):
            result = runner.invoke(app, ["data", "health", "--json"])
        payload = json.loads(result.output)
        assert payload["healthy"] is False
        assert [c["name"] for c in payload["checks"]] == ["a", "b"]


# ---------------------------------------------------------------------------
# Slice 921 — minute session mass (Tasks 5.2/5.3, SC7)
# ---------------------------------------------------------------------------


def _session(
    day: str, open_hm: tuple[int, int], close_hm: tuple[int, int]
) -> TradingSessionBounds:
    year, month, date_ = (int(part) for part in day.split("-"))
    return TradingSessionBounds(
        session_open_utc=datetime(year, month, date_, *open_hm, tzinfo=UTC),
        session_close_utc=datetime(year, month, date_, *close_hm, tzinfo=UTC),
    )


class TestJudgedSessionSelection:
    """A session is judged only once the firing that collects it has finished.

    Both a regular 20:00 UTC close and an early 17:00 close are collected by
    the next 13:05 firing, so both become judgeable at 16:05 UTC the next day
    (13:05 + the 3 h collection lag) — the SC7 boundary.
    """

    REGULAR = _session("2026-09-08", (13, 30), (20, 0))
    EARLY = _session("2026-09-08", (13, 30), (17, 0))
    PRIOR = _session("2026-09-07", (13, 30), (20, 0))

    def test_a_regular_close_is_not_judged_at_1604(self) -> None:
        now = datetime(2026, 9, 9, 16, 4, tzinfo=UTC)
        judged = select_judged_session([self.REGULAR, self.PRIOR], now=now)
        assert judged == self.PRIOR, "the day before is still the judged one"

    def test_a_regular_close_is_judged_at_1605(self) -> None:
        now = datetime(2026, 9, 9, 16, 5, tzinfo=UTC)
        judged = select_judged_session([self.REGULAR, self.PRIOR], now=now)
        assert judged == self.REGULAR

    def test_an_early_close_is_not_judged_at_1604(self) -> None:
        """An early close does NOT become judgeable sooner — it is collected by
        the same 13:05 firing, so it waits for the same instant."""
        now = datetime(2026, 9, 9, 16, 4, tzinfo=UTC)
        judged = select_judged_session([self.EARLY, self.PRIOR], now=now)
        assert judged == self.PRIOR

    def test_an_early_close_is_judged_at_1605(self) -> None:
        now = datetime(2026, 9, 9, 16, 5, tzinfo=UTC)
        judged = select_judged_session([self.EARLY, self.PRIOR], now=now)
        assert judged == self.EARLY

    def test_a_weekend_falls_out_because_it_is_not_a_calendar_row(self) -> None:
        """Friday's session stays the judged one all weekend — Saturday and
        Sunday are simply not candidates."""
        friday = _session("2026-09-11", (13, 30), (20, 0))
        now = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)  # Sunday
        assert select_judged_session([friday], now=now) == friday

    def test_a_holiday_gap_keeps_the_last_traded_session(self) -> None:
        before_holiday = _session("2026-11-25", (14, 30), (21, 0))
        now = datetime(2026, 11, 27, 6, 0, tzinfo=UTC)  # Thanksgiving morning
        assert select_judged_session([before_holiday], now=now) == before_holiday

    def test_no_candidates_yields_none(self) -> None:
        assert (
            select_judged_session([], now=datetime(2026, 9, 9, 12, tzinfo=UTC)) is None
        )

    def test_only_unfinished_sessions_yields_none(self) -> None:
        """Today's session has closed but its collecting firing has not run."""
        now = datetime(2026, 9, 8, 21, 0, tzinfo=UTC)
        assert select_judged_session([self.REGULAR], now=now) is None


class TestCollectingFiringFinishedAt:
    def test_a_regular_close_resolves_to_1605_next_day(self) -> None:
        finished = collecting_firing_finished_at(
            _session("2026-09-08", (13, 30), (20, 0))
        )
        assert finished == datetime(2026, 9, 9, 16, 5, tzinfo=UTC)

    def test_an_early_close_resolves_to_the_same_instant(self) -> None:
        finished = collecting_firing_finished_at(
            _session("2026-09-08", (13, 30), (17, 0))
        )
        assert finished == datetime(2026, 9, 9, 16, 5, tzinfo=UTC)

    def test_a_close_before_the_1305_firing_uses_that_firing(self) -> None:
        """A hypothetical session closing at 12:00 UTC is collected by the
        13:05 firing, not the next day's."""
        finished = collecting_firing_finished_at(
            _session("2026-09-08", (9, 0), (12, 0))
        )
        assert finished == datetime(2026, 9, 8, 16, 5, tzinfo=UTC)


class TestMinuteSessionMassRule:
    """Two floors, because either alone can miss the failure."""

    SESSION = _session("2026-09-08", (13, 30), (20, 0))  # 390 minutes

    def test_a_healthy_session_passes(self) -> None:
        # 2026-08-27 shape: ~5,100 bars/min across ~7,259 symbols.
        mass = SessionMass(total_bars=1_989_000, symbols_meeting_min_bars=7_259)
        ok, detail = check_minute_session_mass(self.SESSION, mass)
        assert ok is True
        assert "5,100/min" in detail

    def test_the_20260907_collapse_fails(self) -> None:
        """The measured failure: ~45k bars/day for ~10.8k symbols, nearly all
        of them holding a single opening bar."""
        mass = SessionMass(total_bars=45_000, symbols_meeting_min_bars=0)
        ok, _ = check_minute_session_mass(self.SESSION, mass)
        assert ok is False

    def test_enough_bars_but_too_few_symbols_fails(self) -> None:
        """A handful of fully-collected symbols can clear the rate floor; the
        symbol floor is what catches it."""
        mass = SessionMass(total_bars=1_989_000, symbols_meeting_min_bars=200)
        ok, _ = check_minute_session_mass(self.SESSION, mass)
        assert ok is False

    def test_enough_symbols_but_too_few_bars_fails(self) -> None:
        mass = SessionMass(total_bars=100_000, symbols_meeting_min_bars=7_000)
        ok, _ = check_minute_session_mass(self.SESSION, mass)
        assert ok is False

    def test_no_judged_session_is_an_explicit_fail(self) -> None:
        """Never ok=True — a silent pass here is the silence this slice
        exists to end."""
        ok, detail = check_minute_session_mass(None, None)
        assert ok is False
        assert "no completed session to judge" in detail

    def test_unmeasurable_mass_is_an_explicit_fail(self) -> None:
        ok, detail = check_minute_session_mass(self.SESSION, None)
        assert ok is False
        assert "could not be measured" in detail

    def test_a_zero_length_session_fails_rather_than_dividing_by_zero(self) -> None:
        broken = TradingSessionBounds(
            session_open_utc=datetime(2026, 9, 8, 13, 30, tzinfo=UTC),
            session_close_utc=datetime(2026, 9, 8, 13, 30, tzinfo=UTC),
        )
        ok, detail = check_minute_session_mass(
            broken, SessionMass(total_bars=0, symbols_meeting_min_bars=0)
        )
        assert ok is False
        assert "non-positive" in detail

    def test_the_detail_names_both_measurements_and_both_floors(self) -> None:
        """An operator must be able to act on the line without reading code."""
        mass = SessionMass(total_bars=45_000, symbols_meeting_min_bars=3)
        _, detail = check_minute_session_mass(self.SESSION, mass)
        assert "45,000 bars" in detail
        assert f"{HEALTH_MINUTE_SESSION_MIN_BARS_PER_MINUTE:,}" in detail
        assert f"{HEALTH_MINUTE_SESSION_MIN_SYMBOLS:,}" in detail
        assert "2026-09-08" in detail


class TestSessionMassQueries:
    """Where the mass is read from, and what happens when the read fails."""

    SESSION = TradingSessionBounds(
        session_open_utc=datetime(2026, 9, 8, 13, 30, tzinfo=UTC),
        session_close_utc=datetime(2026, 9, 8, 20, 0, tzinfo=UTC),
    )

    @staticmethod
    def _conn(row: tuple | None = (1_989_000, 7_259)) -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchone = MagicMock(return_value=row)
        cur.fetchall = MagicMock(return_value=[])
        conn.cursor = MagicMock(return_value=cur)
        return conn

    def test_the_mass_read_targets_the_cagg_not_raw_minute_ohlcv(self) -> None:
        """SC7: raw minute_ohlcv is the §166/§167 latency cliff. Reading it
        here would make the health check itself the outage."""
        conn = self._conn()
        fetch_session_mass(conn, self.SESSION)
        executed = " ".join(
            call.args[0] for call in conn.cursor.return_value.execute.call_args_list
        )
        assert "minute_4hour_ohlcv" in executed
        assert "FROM minute_ohlcv" not in executed

    def test_the_mass_read_runs_under_the_statement_timeout(self) -> None:
        conn = self._conn()
        fetch_session_mass(conn, self.SESSION)
        executed = " ".join(
            call.args[0] for call in conn.cursor.return_value.execute.call_args_list
        )
        assert "statement_timeout" in executed
        assert HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT in executed

    def test_the_mass_read_bounds_to_the_session_window(self) -> None:
        conn = self._conn()
        fetch_session_mass(conn, self.SESSION)
        params = conn.cursor.return_value.execute.call_args.args[1]
        assert self.SESSION.session_close_utc in params

    def test_the_mass_read_includes_the_bucket_the_session_opens_inside(
        self,
    ) -> None:
        """Regression, found by the load fixture (990,000 of 1,980,000 seeded
        bars counted).

        The cagg's 4-hour buckets are aligned to the DAY, not to the session,
        so a 13:30-20:00 UTC session opens partway through the 12:00 bucket.
        A ``time_bucket >= session_open`` filter drops that bucket whole — the
        first ~2.5 hours of every regular session, roughly 38% of its bars —
        and reports a mass far below the truth on a perfectly healthy day.
        """
        from manta_trading.constants import GRANULARITY_BAR_MINUTES, Granularity

        conn = self._conn()
        fetch_session_mass(conn, self.SESSION)
        params = conn.cursor.return_value.execute.call_args.args[1]
        width = timedelta(minutes=GRANULARITY_BAR_MINUTES[Granularity.H4])
        expected_start = self.SESSION.session_open_utc - width
        assert expected_start in params, (
            "the window must reach back one bucket width so the bucket the "
            "session opens inside is counted"
        )
        # The bucket BEFORE that one must still be excluded — it holds no
        # session bars, and including it would count pre-market activity.
        assert (self.SESSION.session_open_utc - 2 * width) not in params

    def test_the_mass_read_excludes_the_bucket_starting_at_the_close(
        self,
    ) -> None:
        """The 20:00 bucket begins as the session ends; its bars are
        after-hours, not session mass."""
        conn = self._conn()
        fetch_session_mass(conn, self.SESSION)
        sql_text, params = conn.cursor.return_value.execute.call_args.args
        assert "time_bucket < %s" in sql_text
        assert self.SESSION.session_close_utc in params

    def test_a_statement_timeout_exits_two_rather_than_reporting_a_low_mass(
        self,
    ) -> None:
        """A cancelled query must not become a FAIL verdict — that would read
        as "the data is missing" when the truth is "we could not look"."""
        import psycopg

        with (
            patch("manta_trading.cli.app.Settings", return_value=_settings()),
            patch("manta_trading.cli.app.setup_logging"),
            patch("manta_trading.cli.commands.health.psycopg.connect"),
            patch(
                "manta_trading.cli.commands.health.gather",
                side_effect=psycopg.errors.QueryCanceled("statement timeout"),
            ),
        ):
            result = runner.invoke(app, ["data", "health"])
        assert result.exit_code == EXIT_UNAVAILABLE

    def test_candidate_sessions_come_from_the_judged_calendar(self) -> None:
        conn = self._conn()
        fetch_candidate_sessions(conn, now=NOW)
        sql_text, params = conn.cursor.return_value.execute.call_args.args
        assert "trading_sessions" in sql_text
        assert params[0] == HEALTH_MINUTE_SESSION_CALENDAR
        assert "session_close_utc IS NOT NULL" in sql_text

    def test_candidate_sessions_exclude_ones_that_have_not_closed_yet(self) -> None:
        """Regression, found by the load fixture (whose seeded NYSE calendar
        ran to 2028-12-29).

        ``trading_sessions`` is populated ~2 years ahead
        (TRADING_SESSIONS_EXTENSION_YEARS, kept current by
        maybe_extend_trading_sessions), so an unbounded "newest first" read
        returns only sessions that have not happened yet. None of them can be
        judged, so the check would report "no completed session to judge" on
        every single run in production — a permanent FAIL carrying no
        information about the data.
        """
        conn = self._conn()
        fetch_candidate_sessions(conn, now=NOW)
        sql_text, params = conn.cursor.return_value.execute.call_args.args
        assert "session_close_utc <= %s" in sql_text
        assert NOW in params

    def test_gather_produces_a_session_mass_check_and_no_quota_check(self) -> None:
        conn = self._conn(row=(1_989_000, 7_259))
        with (
            patch(
                "manta_trading.cli.commands.health.assert_cagg_fresh",
                return_value=MagicMock(is_fresh=True, detail="fresh"),
            ),
            patch(
                "manta_trading.cli.commands.health.check_coverage_freshness",
                return_value=MagicMock(verdicts=[]),
            ),
            patch(
                "manta_trading.cli.commands.health.read_catalog_status",
                return_value=None,
            ),
            patch(
                "manta_trading.cli.commands.health.read_candle_status",
                return_value=None,
            ),
            patch(
                "manta_trading.cli.commands.health.read_trade_status",
                return_value=None,
            ),
            patch(
                "manta_trading.cli.commands.health.fetch_candidate_sessions",
                return_value=[self.SESSION],
            ),
            patch(
                "manta_trading.cli.commands.health.select_judged_session",
                return_value=self.SESSION,
            ),
            patch("manta_trading.cli.commands.health._newest", return_value=NOW),
        ):
            checks = gather(conn, _settings(), now=lambda: NOW)
        names = [c.name for c in checks]
        assert "minute session mass" in names
        assert "eodhd quota" not in names


class TestEarlyCloseSessionMass:
    """SC7's early-close case, sized on its own shorter session."""

    def test_a_210_minute_session_holding_105_million_bars_passes(self) -> None:
        early = TradingSessionBounds(
            session_open_utc=datetime(2026, 11, 27, 14, 30, tzinfo=UTC),
            session_close_utc=datetime(2026, 11, 27, 18, 0, tzinfo=UTC),
        )
        assert early.minutes == 210
        mass = SessionMass(total_bars=1_050_000, symbols_meeting_min_bars=7_000)
        ok, detail = check_minute_session_mass(early, mass)
        assert ok is True
        assert "5,000/min" in detail
