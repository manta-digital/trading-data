"""The open-interest rule ``n`` (slice 303, D9) on synthetic inputs: no database.

Session S ranks by the open interest of S - 2 (``RANK_LAG_SESSIONS``), never S's
own.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from tick_support.roll_synthetic import (
    contract,
    inputs,
    interest_unit,
    sessions,
    symbols,
    weekdays,
)

from manta_trading.data.tick.roll_resolve import active_line, resolve_sessions
from manta_trading.data.tick.roll_rules import RollRule
from manta_trading.data.tick.roll_types import ActiveContract, Unresolved
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.tick_status import TickSessionStatus

FIRST = date(2024, 9, 2)
DAYS = weekdays(FIRST, 15)
RUN = sessions(FIRST, 15)
U = contract(118, "ESU4", FIRST, date(2024, 9, 20))
Z = contract(183748, "ESZ4", FIRST, date(2024, 12, 20))
H = contract(5002, "ESH5", FIRST, date(2025, 3, 21))
CROSS = date(2024, 9, 11)  # Z's open interest passes U's from this session on
N0, N1 = RollRule.parse("n.0"), RollRule.parse("n.1")


def _interest(cross: date = CROSS) -> dict[tuple[date, int], int]:
    rows: dict[tuple[date, int], int] = {}
    for day in DAYS:
        rows[day, U.instrument_id] = 900 if day < cross else 100
        rows[day, Z.instrument_id] = 500 if day < cross else 1_000
        rows[day, H.instrument_id] = 10
    return rows


def _resolve(
    rule: RollRule, interest: dict[tuple[date, int], int], **kwargs: object
) -> list[str]:
    run = inputs(RUN, [U, Z, H], interest=interest, **kwargs)  # type: ignore[arg-type]
    return symbols(resolve_sessions(rule, run, FIRST, date(2024, 9, 21)))


def test_the_switch_shows_two_sessions_after_the_cross() -> None:
    rolled = dict(zip(DAYS, _resolve(N0, _interest()), strict=True))
    assert rolled[date(2024, 9, 12)] == "ESU4"  # ranked by 09-10
    assert rolled[date(2024, 9, 13)] == "ESZ4"  # ranked by 09-11, the cross


def test_n1_is_the_second_ranked() -> None:
    rolled = dict(zip(DAYS, _resolve(N1, _interest()), strict=True))
    assert rolled[date(2024, 9, 12)] == "ESZ4"
    assert rolled[date(2024, 9, 13)] == "ESU4"


def test_a_tie_goes_to_the_earlier_expiration() -> None:
    tied = {key: 5 for key in _interest()}
    assert set(_resolve(N0, tied)[2:]) == {"ESU4"}


def test_a_candidate_not_listed_at_the_lag_session_is_excluded() -> None:
    late = contract(7001, "ESM5", DAYS[4], date(2025, 6, 20))  # lists on DAYS[4]
    interest = _interest() | {(day, late.instrument_id): 10_000 for day in DAYS}
    run = inputs(RUN, [U, Z, H, late], interest=interest)
    answers = resolve_sessions(N0, run, DAYS[4], DAYS[7])
    # S = DAYS[4] and DAYS[5] rank on DAYS[2], DAYS[3]: ESM5 was not listed then.
    assert symbols(answers[:2]) == ["ESU4", "ESU4"]
    assert symbols(answers[2:]) == ["ESM5"]  # DAYS[6] ranks on DAYS[4]


def test_a_candidate_without_open_interest_is_not_ranked_and_never_read_as_zero() -> (
    None
):
    # ESZ4 has no record at DAYS[2]: it is skipped, not counted as zero.
    interest = {k: v for k, v in _interest().items() if k != (DAYS[2], Z.instrument_id)}
    run = inputs(RUN, [U, Z, H], interest=interest)
    (answer,) = resolve_sessions(N0, run, DAYS[4], DAYS[5])
    assert not isinstance(answer, Unresolved)
    assert answer.raw_symbol != "ESZ4"


def test_no_candidate_with_open_interest_is_unresolved_naming_the_lag_session() -> None:
    interest = {k: v for k, v in _interest().items() if k[0] != DAYS[2]}
    run = inputs(RUN, [U, Z, H], interest=interest)
    (answer,) = resolve_sessions(N0, run, DAYS[4], DAYS[5])
    assert isinstance(answer, Unresolved)
    assert answer.reason == (
        f"open interest inputs: no candidate has open interest in {DAYS[2]}"
    )


def test_an_incomplete_lag_session_is_unresolved_naming_it_and_its_status() -> None:
    statuses = {DAYS[2]: TickSessionStatus.AWAITING_INGEST}
    run = inputs(RUN, [U, Z, H], interest=_interest(), statistics_statuses=statuses)
    (answer,) = resolve_sessions(N0, run, DAYS[4], DAYS[5])
    assert isinstance(answer, Unresolved)
    assert answer.reason == (
        f"open interest inputs: session {DAYS[2]} is awaiting_ingest"
    )


def test_a_session_not_judged_for_statistics_says_so() -> None:
    run = inputs(RUN, [U, Z, H], interest=_interest())
    run = replace(run, statistics_statuses={})
    (answer,) = resolve_sessions(N0, run, DAYS[4], DAYS[5])
    assert isinstance(answer, Unresolved)
    assert answer.reason.endswith("is not judged")


def test_the_first_two_sessions_have_no_lag_session() -> None:
    answers = _resolve(N0, _interest())
    assert answers[:2] == ["-", "-"]


def test_the_rule_never_reads_the_sessions_own_open_interest() -> None:
    base = _interest()
    # A huge value for H at S = DAYS[4] itself must not change S's answer...
    skewed = base | {(DAYS[4], H.instrument_id): 10**9}
    assert _resolve(N0, base)[4] == _resolve(N0, skewed)[4] == "ESU4"
    # ...it is the lag session's (DAYS[2]) value that counts.
    at_lag = base | {(DAYS[2], H.instrument_id): 10**9}
    assert _resolve(N0, at_lag)[4] == "ESH5"


def test_every_answer_names_the_statistics_basis_and_the_records_units() -> None:
    run = inputs(RUN, [U, Z, H], interest=_interest())
    answers = resolve_sessions(N0, run, DAYS[2], DAYS[6])
    contracts = [a for a in answers if isinstance(a, ActiveContract)]
    assert contracts
    for answer in contracts:
        assert answer.basis_family is SchemaFamily.STATISTICS
    first = contracts[0]
    assert first.basis_session == DAYS[0]
    assert first.basis_unit_ids == (interest_unit(DAYS[0]),)


def test_the_forecast_reads_like_the_volume_rule() -> None:
    longer = sessions(FIRST, 25)  # past ESU4's expiration session
    run = inputs(longer, [U, Z, H], interest=_interest())
    line = active_line("ES", N0, run, DAYS[6])
    assert isinstance(line.answer, ActiveContract)
    assert line.known_through is not None
    assert line.roll_no_later_than is not None
