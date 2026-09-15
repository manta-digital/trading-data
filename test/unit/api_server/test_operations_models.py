"""Unit tests: the operations response models (slice 189, Section 2).

Pure translation, so all of this runs with no database and no network — 922
made ``build_overview`` pure precisely so the interesting shapes can be built
by hand.

What these tests are really guarding is the two places the API deliberately
differs from the screen, because both look like bugs to a reader who does not
know why:

- ``abandoned`` is absent (D4/SC5). The API cannot judge a pid recorded on
  another host, so it publishes nothing rather than a field that would be
  false for every row.
- ``credits`` is absent from the overview (D2/SC7). It lives on its own route
  so ``/api/v1/overview`` is pure database and safe to poll.

The enum tests **iterate** ``PassKind`` and ``PassRunOutcome`` rather than
listing their members, so adding one cannot pass silently (SC8).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from manta_trading.api.eodhd_account import CreditUsage
from manta_trading.api_server.models.operations import (
    CreditsRecord,
    CreditsResponse,
    OverviewResponse,
)
from manta_trading.cli.overview_types import (
    CREDITS_NO_KEY,
    LastRun,
    Overview,
    PassLine,
    RunningRow,
    SourceFreshness,
    credits_unavailable,
)
from manta_trading.data.acquisition.pass_runs import PassKind, PassRunOutcome

NOW = datetime(2026, 9, 13, 14, 22, 3, tzinfo=UTC)
HOST = "manta9000"


def _running(
    *,
    phase: str | None = "trailing",
    done: int | None = 412,
    total: int | None = 1100,
    pid: int = 38214,
    abandoned: bool = False,
) -> RunningRow:
    return RunningRow(
        phase=phase,
        done=done,
        total=total,
        since=NOW - timedelta(minutes=17),
        progress_at=NOW - timedelta(seconds=5),
        hostname=HOST,
        pid=pid,
        abandoned=abandoned,
    )


def _last_run(
    *, outcome: PassRunOutcome = PassRunOutcome.COMPLETE_QUOTA
) -> LastRun:
    return LastRun(
        started_at=NOW - timedelta(days=4),
        ended_at=NOW - timedelta(days=4) + timedelta(minutes=43),
        outcome=outcome,
        exit_code=0,
        detail="1,100 symbols; stopped at provider quota",
    )


_DEFAULT_PASSES = (
    PassLine(
        kind=PassKind.MINUTE,
        cadence="Mon,Thu 09:05 UTC",
        running=(_running(),),
        last=_last_run(),
        next_firing=NOW + timedelta(days=3),
    ),
)
_DEFAULT_SOURCES = (SourceFreshness("minute bars", NOW - timedelta(hours=18)),)
_UNIVERSE_LINE = "1,100 active / 43 exhausted / 12 still asking"


def _overview(
    *,
    passes: tuple[PassLine, ...] = _DEFAULT_PASSES,
    sources: tuple[SourceFreshness, ...] = _DEFAULT_SOURCES,
    universe: str | None = _UNIVERSE_LINE,
    universe_at: datetime | None = NOW - timedelta(hours=11),
) -> Overview:
    return Overview(
        now=NOW,
        passes=passes,
        sources=sources,
        health_verdict="OK — 1,100 symbols current",
        health_at=NOW - timedelta(minutes=22),
        credits=CreditUsage(98412, 100000, 0),
        credits_text="98,412 / 100,000 used",
        universe=universe,
        universe_at=universe_at,
    )


def _body(overview: Overview) -> dict:
    return json.loads(OverviewResponse.from_overview(overview).model_dump_json())


class TestEveryFieldTranslates:
    """Nothing of the screen's database half is lost on the way to the wire."""

    def test_the_top_level_blocks(self) -> None:
        body = _body(_overview())

        assert body["now"] == "2026-09-13T14:22:03Z"
        assert body["health"] == {
            "verdict": "OK — 1,100 symbols current",
            "at": "2026-09-13T14:00:03Z",
        }
        assert body["universe"] == {
            "summary": "1,100 active / 43 exhausted / 12 still asking",
            "at": "2026-09-13T03:22:03Z",
        }
        assert body["sources"] == [
            {"name": "minute bars", "newest": "2026-09-12T20:22:03Z"}
        ]

    def test_the_pass_line(self) -> None:
        line = _body(_overview())["passes"][0]

        assert line["pass"] == "minute"
        assert line["cadence"] == "Mon,Thu 09:05 UTC"
        assert line["next_firing"] == "2026-09-16T14:22:03Z"
        assert line["last_run"] == {
            "started_at": "2026-09-09T14:22:03Z",
            "ended_at": "2026-09-09T15:05:03Z",
            "outcome": "COMPLETE_QUOTA",
            "exit_code": 0,
            "detail": "1,100 symbols; stopped at provider quota",
        }
        assert line["running"] == [
            {
                "phase": "trailing",
                "done": 412,
                "total": 1100,
                "since": "2026-09-13T14:05:03Z",
                "progress_at": "2026-09-13T14:21:58Z",
                "hostname": HOST,
                "pid": 38214,
            }
        ]

    def test_the_wire_key_is_pass_not_the_python_attribute(self) -> None:
        """``pass`` is a keyword, so the attribute is ``pass_`` and the key
        comes from the alias. A body carrying ``pass_`` would be a contract
        break invisible to a test that only read the model."""
        line = _body(_overview())["passes"][0]

        assert "pass" in line
        assert "pass_" not in line


class TestTheStatesAScreenBlurs:
    """The shapes 922 renders with sentinel prose; the API serves null."""

    def test_two_live_runs_stay_two(self) -> None:
        overview = _overview(
            passes=(
                PassLine(
                    kind=PassKind.MINUTE,
                    cadence="daily 09:05 UTC",
                    running=(_running(pid=1), _running(pid=2, phase="backfill")),
                    last=None,
                    next_firing=None,
                ),
            )
        )

        running = _body(overview)["passes"][0]["running"]

        assert [row["pid"] for row in running] == [1, 2]

    def test_a_pass_that_never_completed_serves_null_not_a_sentinel(self) -> None:
        overview = _overview(
            passes=(
                PassLine(
                    kind=PassKind.DAILY,
                    cadence="daily 09:05 UTC",
                    running=(),
                    last=None,
                    next_firing=None,
                ),
            )
        )

        line = _body(overview)["passes"][0]

        assert line["last_run"] is None
        assert "NEVER" not in json.dumps(line).upper()

    def test_no_accounting_run_serves_a_null_summary(self) -> None:
        body = _body(_overview(universe=None, universe_at=None))

        assert body["universe"] == {"summary": None, "at": None}
        assert "NO_ACCOUNTING" not in json.dumps(body)

    def test_a_pass_reporting_no_progress_serves_nulls(self) -> None:
        overview = _overview(
            passes=(
                PassLine(
                    kind=PassKind.HEALTH,
                    cadence="daily 09:05 UTC",
                    running=(_running(phase=None, done=None, total=None),),
                    last=None,
                    next_firing=None,
                ),
            )
        )

        row = _body(overview)["passes"][0]["running"][0]

        assert row["phase"] is None
        assert row["done"] is None
        assert row["total"] is None

    def test_a_source_that_holds_nothing_serves_a_null_newest(self) -> None:
        body = _body(_overview(sources=(SourceFreshness("kalshi trades", None),)))

        assert body["sources"] == [{"name": "kalshi trades", "newest": None}]


class TestTheDeliberateOmissions:
    """The two fields whose absence is the design, not an oversight."""

    def test_abandoned_appears_nowhere(self) -> None:
        """SC5. Asserted over the whole body, including an abandoned row:
        a running row the CLI would mark abandoned must still not carry the
        field here."""
        overview = _overview(
            passes=(
                PassLine(
                    kind=PassKind.MINUTE,
                    cadence="daily 09:05 UTC",
                    running=(_running(abandoned=True),),
                    last=None,
                    next_firing=None,
                ),
            )
        )

        assert "abandoned" not in _body_text(overview)

    def test_credits_appear_nowhere_in_the_overview(self) -> None:
        """SC7. The overview carries credits on its ``Overview`` input and
        must drop both of them: the route is pure database (D2)."""
        text = _body_text(_overview())

        assert "credits" not in text
        assert "98412" not in text
        assert "98,412" not in text


def _body_text(overview: Overview) -> str:
    return OverviewResponse.from_overview(overview).model_dump_json()


class TestEnumParity:
    """SC8: tokens come from the enums, so a new member cannot pass silently.

    These iterate rather than list. A test spelling out five outcomes would
    keep passing on the day a sixth is added — which is the moment the wire
    contract actually changes.
    """

    def test_every_pass_kind_round_trips(self) -> None:
        for kind in PassKind:
            overview = _overview(
                passes=(
                    PassLine(
                        kind=kind,
                        cadence="daily 09:05 UTC",
                        running=(),
                        last=None,
                        next_firing=None,
                    ),
                )
            )

            assert _body(overview)["passes"][0]["pass"] == kind.value

    def test_every_outcome_round_trips(self) -> None:
        for outcome in PassRunOutcome:
            overview = _overview(
                passes=(
                    PassLine(
                        kind=PassKind.MINUTE,
                        cadence="daily 09:05 UTC",
                        running=(),
                        last=_last_run(outcome=outcome),
                        next_firing=None,
                    ),
                )
            )

            assert _body(overview)["passes"][0]["last_run"]["outcome"] == outcome.value


class TestCreditsResponse:
    """All three shapes the credits route serves, each a 200 (D6/SC6)."""

    def test_usage_present(self) -> None:
        body = json.loads(
            CreditsResponse(
                credits=CreditsRecord.from_usage(CreditUsage(98412, 100000, 0)),
                error=None,
            ).model_dump_json()
        )

        assert body == {
            "credits": {
                "used": 98412,
                "daily_limit": 100000,
                "extra": 0,
                "remaining": 1588,
            },
            "error": None,
        }

    def test_remaining_is_read_from_the_property_not_stored(self) -> None:
        """``CreditUsage.remaining`` is computed, and extras count toward it.
        A record that copied a stored field would get this wrong."""
        record = CreditsRecord.from_usage(CreditUsage(98412, 100000, 5000))

        assert record.remaining == 6588

    def test_no_key_configured(self) -> None:
        body = json.loads(
            CreditsResponse(credits=None, error=CREDITS_NO_KEY).model_dump_json()
        )

        assert body["credits"] is None
        assert body["error"] == CREDITS_NO_KEY

    def test_fetch_failure(self) -> None:
        message = credits_unavailable("timed out")
        body = json.loads(
            CreditsResponse(credits=None, error=message).model_dump_json()
        )

        assert body["credits"] is None
        assert body["error"] == message
