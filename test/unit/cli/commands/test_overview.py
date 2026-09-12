"""Unit tests: building and rendering ``mt data overview`` (slice 922).

The overview's value is that it is true at a glance, so these tests are
mostly about the states an operator most needs told apart and a screen most
easily blurs:

- **idle vs running vs abandoned.** A row whose process is gone must not
  read as work in progress; that is the failure mode the pid check exists
  for. It is decided only for rows this host owns — a pid on another machine
  says nothing about a process here.
- **two live runs of one kind.** Summarising them to one would hide exactly
  the condition worth seeing.
- **complete (quota) vs failed.** Quota is a result, not a fault, and the
  screen has to say so or the operator learns to ignore the outcome column.
- **credits unavailable.** Three different reasons, each said plainly, with
  the command still succeeding: the rest of the screen is still true.

``build_overview`` is pure, so all of this runs with no database and no
network.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from manta_trading.api.eodhd_account import CreditUsage
from manta_trading.cli.commands.overview import (
    CREDITS_NO_KEY,
    NEVER_RUN,
    NO_ACCOUNTING,
    OverviewFacts,
    SourceFreshness,
    build_overview,
    credits_unavailable,
)
from manta_trading.cli.rendering.overview import (
    outcome_text,
    overview_payload,
    render_last,
    render_overview,
    render_running,
)
from manta_trading.data.acquisition.pass_runs import (
    PassKind,
    PassRun,
    PassRunOutcome,
)

UTC = UTC
NOW = datetime(2026, 9, 12, 16, 10, tzinfo=UTC)
HOST = "manta9000"


def _facts(**overrides) -> OverviewFacts:
    facts = OverviewFacts(NOW, HOST, minute_firing_days=(5,))
    facts.open_runs = {kind: [] for kind in PassKind}
    facts.latest_ended = {kind: None for kind in PassKind}
    facts.sources = [SourceFreshness("minute bars", None)]
    for key, value in overrides.items():
        setattr(facts, key, value)
    return facts


def _open_run(
    kind: PassKind = PassKind.MINUTE,
    *,
    pid: int = 100,
    hostname: str = HOST,
    phase: str | None = "trailing",
    done: int | None = None,
    total: int | None = None,
    started_at: datetime | None = None,
    progress_at: datetime | None = None,
) -> PassRun:
    return PassRun(
        run_id=uuid.uuid4(),
        pass_kind=kind,
        hostname=hostname,
        pid=pid,
        started_at=started_at or (NOW - timedelta(minutes=30)),
        phase=phase,
        progress_done=done,
        progress_total=total,
        progress_updated_at=progress_at,
    )


def _ended_run(
    kind: PassKind = PassKind.MINUTE,
    *,
    outcome: PassRunOutcome = PassRunOutcome.COMPLETE,
    exit_code: int | None = 0,
    detail: str | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> PassRun:
    start = started_at or (NOW - timedelta(hours=3))
    return PassRun(
        run_id=uuid.uuid4(),
        pass_kind=kind,
        hostname=HOST,
        pid=100,
        started_at=start,
        ended_at=ended_at or (start + timedelta(minutes=36)),
        outcome=outcome,
        exit_code=exit_code,
        detail=detail,
    )


def _line(overview, kind: PassKind):
    return next(line for line in overview.passes if line.kind is kind)


class TestNeverRun:
    def test_a_kind_with_no_rows_has_no_last_run(self) -> None:
        overview = build_overview(_facts())
        assert _line(overview, PassKind.MINUTE).last is None

    def test_it_renders_as_never_run(self) -> None:
        assert render_last(None) == NEVER_RUN

    def test_the_screen_still_names_every_pass(self) -> None:
        text = render_overview(build_overview(_facts()))
        for kind in PassKind:
            assert kind.value in text


class TestIdleAndRunning:
    def test_no_open_row_is_idle(self) -> None:
        overview = build_overview(_facts())
        assert _line(overview, PassKind.MINUTE).running == ()
        assert "idle" in render_overview(overview)

    def test_a_live_run_is_running_not_abandoned(self) -> None:
        facts = _facts()
        facts.open_runs[PassKind.MINUTE] = [_open_run()]
        overview = build_overview(facts, pid_alive=lambda _pid: True)
        row = _line(overview, PassKind.MINUTE).running[0]
        assert row.abandoned is False
        assert "RUNNING" in render_running(row, NOW)

    def test_progress_is_shown_when_known(self) -> None:
        facts = _facts()
        facts.open_runs[PassKind.MINUTE] = [
            _open_run(done=250, total=15215, progress_at=NOW - timedelta(seconds=40))
        ]
        overview = build_overview(facts, pid_alive=lambda _pid: True)
        text = render_running(_line(overview, PassKind.MINUTE).running[0], NOW)
        assert "250/15,215" in text
        assert "40 s ago" in text

    def test_a_run_with_no_progress_yet_still_reports_its_start(self) -> None:
        facts = _facts()
        facts.open_runs[PassKind.MINUTE] = [_open_run(progress_at=None)]
        overview = build_overview(facts, pid_alive=lambda _pid: True)
        text = render_running(_line(overview, PassKind.MINUTE).running[0], NOW)
        assert "since" in text
        assert "progress" not in text


class TestAbandoned:
    """A row whose process is gone must never read as work in progress."""

    def test_a_dead_pid_on_this_host_is_abandoned(self) -> None:
        facts = _facts()
        facts.open_runs[PassKind.MINUTE] = [_open_run(pid=4242)]
        overview = build_overview(facts, pid_alive=lambda _pid: False)
        row = _line(overview, PassKind.MINUTE).running[0]
        assert row.abandoned is True
        text = render_running(row, NOW)
        assert "ABANDONED" in text
        assert "4242" in text
        assert "RUNNING" not in text

    def test_a_dead_pid_on_another_host_is_not_judged(self) -> None:
        """A pid on another machine says nothing about a process here."""
        facts = _facts()
        facts.open_runs[PassKind.MINUTE] = [_open_run(pid=4242, hostname="other-host")]
        overview = build_overview(facts, pid_alive=lambda _pid: False)
        assert _line(overview, PassKind.MINUTE).running[0].abandoned is False

    def test_liveness_is_only_asked_about_this_hosts_pids(self) -> None:
        asked: list[int] = []

        def _alive(pid: int) -> bool:
            asked.append(pid)
            return True

        facts = _facts()
        facts.open_runs[PassKind.MINUTE] = [
            _open_run(pid=1, hostname=HOST),
            _open_run(pid=2, hostname="elsewhere"),
        ]
        build_overview(facts, pid_alive=_alive)
        assert asked == [1]


class TestTwoLiveRuns:
    """Summarising two live runs of one kind would hide the condition."""

    def test_both_are_kept(self) -> None:
        facts = _facts()
        facts.open_runs[PassKind.MINUTE] = [
            _open_run(pid=1, started_at=NOW - timedelta(minutes=5)),
            _open_run(pid=2, started_at=NOW - timedelta(minutes=50)),
        ]
        overview = build_overview(facts, pid_alive=lambda _pid: True)
        assert len(_line(overview, PassKind.MINUTE).running) == 2

    def test_both_appear_on_the_screen(self) -> None:
        facts = _facts()
        facts.open_runs[PassKind.MINUTE] = [
            _open_run(pid=11, phase="trailing"),
            _open_run(pid=22, phase="backfill"),
        ]
        text = render_overview(build_overview(facts, pid_alive=lambda _pid: True))
        assert "trailing" in text
        assert "backfill" in text

    def test_a_live_and_an_abandoned_run_are_told_apart(self) -> None:
        facts = _facts()
        facts.open_runs[PassKind.MINUTE] = [
            _open_run(pid=1),
            _open_run(pid=2),
        ]
        overview = build_overview(facts, pid_alive=lambda pid: pid == 1)
        rows = _line(overview, PassKind.MINUTE).running
        assert [row.abandoned for row in rows] == [False, True]


class TestOutcomeText:
    @pytest.mark.parametrize("outcome", list(PassRunOutcome))
    def test_every_outcome_has_text(self, outcome: PassRunOutcome) -> None:
        assert outcome_text(outcome)

    def test_quota_reads_as_a_result_not_a_fault(self) -> None:
        assert outcome_text(PassRunOutcome.COMPLETE_QUOTA) == "complete (quota)"

    def test_no_outcome_renders_as_its_raw_enum_value(self) -> None:
        for outcome in PassRunOutcome:
            assert outcome_text(outcome) != outcome.value


class TestLastRun:
    def test_a_clean_run_does_not_print_exit_zero(self) -> None:
        """A column of 'exit 0' trains the eye past the interesting number."""
        run = _ended_run(exit_code=0)
        overview = build_overview(_facts(latest_ended={PassKind.MINUTE: run}))
        assert "exit" not in render_last(_line(overview, PassKind.MINUTE).last)

    def test_a_non_zero_exit_is_named(self) -> None:
        run = _ended_run(outcome=PassRunOutcome.INCOMPLETE, exit_code=3)
        overview = build_overview(_facts(latest_ended={PassKind.MINUTE: run}))
        assert "exit 3" in render_last(_line(overview, PassKind.MINUTE).last)

    def test_a_failed_run_shows_its_detail_on_a_second_line(self) -> None:
        run = _ended_run(
            outcome=PassRunOutcome.FAILED, exit_code=1, detail="ValueError"
        )
        facts = _facts(latest_ended={PassKind.MINUTE: run})
        text = render_overview(build_overview(facts))
        assert "failed" in text
        assert "ValueError" in text

    def test_a_successful_run_does_not_show_its_detail(self) -> None:
        """The detail is the reason a failure happened; success needs no room."""
        run = _ended_run(detail="trailing 15,215 · backfill 900 symbols")
        facts = _facts(latest_ended={PassKind.MINUTE: run})
        assert "backfill 900" not in render_overview(build_overview(facts))

    def test_an_open_row_is_not_treated_as_a_last_run(self) -> None:
        facts = _facts(latest_ended={PassKind.MINUTE: _open_run()})
        assert _line(build_overview(facts), PassKind.MINUTE).last is None


class TestCredits:
    def test_a_reading_is_shown_as_used_against_the_limit(self) -> None:
        facts = _facts(credits=CreditUsage(65_210, 100_000, 0))
        overview = build_overview(facts)
        assert overview.credits_text == "65,210 / 100,000 used today"

    def test_purchased_extras_are_named(self) -> None:
        facts = _facts(credits=CreditUsage(65_210, 100_000, 500_000))
        assert "extra 500,000" in build_overview(facts).credits_text

    def test_a_missing_key_says_so_plainly(self) -> None:
        facts = _facts(credits_error=CREDITS_NO_KEY)
        text = build_overview(facts).credits_text
        assert text == CREDITS_NO_KEY
        assert "MT_EODHD_API_KEY" in text

    def test_a_provider_failure_names_the_reason(self) -> None:
        facts = _facts(credits_error=credits_unavailable("timed out"))
        assert build_overview(facts).credits_text == "unavailable (timed out)"

    def test_the_rest_of_the_screen_survives_a_credit_failure(self) -> None:
        facts = _facts(credits_error=credits_unavailable("timed out"))
        facts.latest_ended[PassKind.MINUTE] = _ended_run()
        text = render_overview(build_overview(facts))
        assert "unavailable" in text
        assert "complete" in text


class TestUniverseLine:
    def test_the_accounting_detail_becomes_the_line(self) -> None:
        run = _ended_run(
            PassKind.ACCOUNTING, detail="minute universe: 11,595,172/13,637,498"
        )
        overview = build_overview(_facts(latest_ended={PassKind.ACCOUNTING: run}))
        assert overview.universe is not None
        assert "11,595,172" in overview.universe
        assert overview.universe_at == run.ended_at

    def test_the_screen_stamps_when_it_was_computed(self) -> None:
        run = _ended_run(PassKind.ACCOUNTING, detail="minute universe: 1/2")
        facts = _facts(latest_ended={PassKind.ACCOUNTING: run})
        text = render_overview(build_overview(facts))
        assert "accounting" in text
        assert "minute universe" in text

    def test_no_accounting_row_names_the_command(self) -> None:
        text = render_overview(build_overview(_facts()))
        assert NO_ACCOUNTING in text
        assert "mt data accounting" in text


class TestHealthVerdict:
    def test_the_verdict_and_its_time_are_carried(self) -> None:
        run = _ended_run(PassKind.HEALTH, detail="healthy")
        overview = build_overview(_facts(latest_ended={PassKind.HEALTH: run}))
        assert overview.health_verdict == "healthy"
        assert overview.health_at == run.ended_at

    def test_an_unhealthy_verdict_is_shown_verbatim(self) -> None:
        run = _ended_run(PassKind.HEALTH, detail="UNHEALTHY: 2 of 9 checks failing")
        facts = _facts(latest_ended={PassKind.HEALTH: run})
        text = render_overview(build_overview(facts))
        assert "UNHEALTHY: 2 of 9 checks failing" in text


class TestCadenceAndNextFiring:
    def test_each_kind_gets_its_cadence(self) -> None:
        overview = build_overview(_facts())
        cadences = {line.kind: line.cadence for line in overview.passes}
        assert cadences[PassKind.MINUTE] == "13:05 on Sat"
        assert cadences[PassKind.DAILY] == "00:35, 12:35"
        assert cadences[PassKind.KALSHI] == "hourly :20"
        assert cadences[PassKind.HEALTH] == "hourly :50"

    def test_the_minute_cadence_follows_the_operator_setting(self) -> None:
        facts = _facts(minute_firing_days=None)
        overview = build_overview(facts)
        assert _line(overview, PassKind.MINUTE).cadence == "13:05"

    def test_every_kind_gets_a_next_firing_in_the_future(self) -> None:
        overview = build_overview(_facts())
        for line in overview.passes:
            assert line.next_firing is not None
            assert line.next_firing >= NOW


class TestSources:
    def test_an_empty_source_says_none_rather_than_a_date(self) -> None:
        facts = _facts(sources=[SourceFreshness("kalshi trades", None)])
        assert "none" in render_overview(build_overview(facts))

    def test_a_source_shows_its_newest_row_and_age(self) -> None:
        facts = _facts(
            sources=[SourceFreshness("minute bars", NOW - timedelta(hours=20))]
        )
        text = render_overview(build_overview(facts))
        assert "20 h ago" in text


class TestScreenShape:
    def test_no_line_is_absurdly_wide(self) -> None:
        """It has to paste into an issue without wrapping."""
        facts = _facts(credits=CreditUsage(65_210, 100_000, 0))
        facts.latest_ended[PassKind.MINUTE] = _ended_run(
            outcome=PassRunOutcome.COMPLETE_QUOTA
        )
        facts.latest_ended[PassKind.ACCOUNTING] = _ended_run(
            PassKind.ACCOUNTING,
            detail=(
                "minute universe: 11,595,172/13,637,498 symbol-sessions covered "
                "(85.0%); 438,299 untraded; 1,604,027 fillable (hole 500,716, "
                "unknown 892,063, untracked 211,213, exhausted 35)"
            ),
        )
        text = render_overview(build_overview(facts))
        assert max(len(line) for line in text.splitlines()) <= 110

    def test_the_blocks_are_all_present(self) -> None:
        text = render_overview(build_overview(_facts()))
        for block in ("PASSES", "SOURCES", "EODHD credits", "minute universe"):
            assert block in text


class TestJsonPayload:
    def test_it_carries_the_designed_top_level_keys(self) -> None:
        payload = overview_payload(build_overview(_facts()))
        assert {"now", "passes", "sources", "credits", "universe"} <= set(payload)

    def test_every_pass_kind_appears(self) -> None:
        payload = overview_payload(build_overview(_facts()))
        assert [p["pass"] for p in payload["passes"]] == [k.value for k in PassKind]

    def test_a_running_row_carries_its_pid_and_abandoned_flag(self) -> None:
        facts = _facts()
        facts.open_runs[PassKind.MINUTE] = [_open_run(pid=4242)]
        payload = overview_payload(build_overview(facts, pid_alive=lambda _pid: False))
        running = payload["passes"][0]["running"][0]
        assert running["pid"] == 4242
        assert running["abandoned"] is True

    def test_a_last_run_carries_its_outcome_value(self) -> None:
        facts = _facts(
            latest_ended={
                PassKind.MINUTE: _ended_run(outcome=PassRunOutcome.COMPLETE_QUOTA)
            }
        )
        payload = overview_payload(build_overview(facts))
        assert payload["passes"][0]["last_run"]["outcome"] == "COMPLETE_QUOTA"

    def test_absent_credits_are_null_with_the_reason_in_text(self) -> None:
        facts = _facts(credits_error=CREDITS_NO_KEY)
        payload = overview_payload(build_overview(facts))
        assert payload["credits"] is None
        assert payload["credits_text"] == CREDITS_NO_KEY

    def test_it_is_json_serialisable(self) -> None:
        import json

        facts = _facts(credits=CreditUsage(1, 2, 3))
        facts.open_runs[PassKind.MINUTE] = [_open_run()]
        facts.latest_ended[PassKind.MINUTE] = _ended_run()
        assert json.loads(json.dumps(overview_payload(build_overview(facts))))
