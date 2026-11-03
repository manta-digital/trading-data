"""The pure purchase planner (slice 224, LLD Technical Decisions 4 and 5; FR3)."""

from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path

import pytest

from manta_trading.data.tick import planner
from manta_trading.data.tick.constants import (
    CME_DATASET,
    DatasetCondition,
    SType,
    TickSchema,
)
from manta_trading.data.tick.planner import (
    DayKey,
    OwnedTierDay,
    PlanInputs,
    plan_purchases,
)
from manta_trading.data.tick.universe import HistoryWant, TickUniverseEntry

SYMBOLS = ("ES.FUT",)
AVAILABLE = DatasetCondition.AVAILABLE
EDGE = date(2030, 1, 1)


def _sessions(start: date, end: date) -> list[date]:
    """Sunday–Friday days in ``[start, end)``: what a CME calendar touches."""
    days = (start + timedelta(days=n) for n in range((end - start).days))
    return [d for d in days if d.weekday() != 5]


ALL_SESSIONS = {"ES": _sessions(date(2024, 1, 1), date(2025, 2, 1))}
NO_TIER = TickUniverseEntry("ES", SYMBOLS, SType.PARENT, None, None, None)


def _owned(days: list[date], schema: TickSchema = TickSchema.TRADES) -> list:
    return [
        OwnedTierDay("ES", CME_DATASET, schema, SYMBOLS, SType.PARENT, day)
        for day in days
    ]


def _key(schema: TickSchema, day: date) -> DayKey:
    return DayKey(CME_DATASET, schema, SYMBOLS, SType.PARENT, day)


def _inputs(**overrides: object) -> PlanInputs:
    sessions = ALL_SESSIONS["ES"]
    fields: dict[str, object] = {
        "universe": [NO_TIER],
        "owned": [],
        "covered": frozenset(),
        "sessions": ALL_SESSIONS,
        "conditions": {(CME_DATASET, day): AVAILABLE for day in sessions},
        "edge_end": {CME_DATASET: EDGE},
    }
    return PlanInputs(**(fields | overrides))  # type: ignore[arg-type]


def _shape(plan: planner.Plan) -> list[tuple[str, date, date]]:
    return [(p.schema.value, p.request.start, p.request.end) for p in plan.requests]


def test_month_boundary_splits_a_run() -> None:
    owned = _owned(_sessions(date(2024, 8, 26), date(2024, 9, 4)))
    plan = plan_purchases(_inputs(owned=owned))
    assert _shape(plan) == [
        ("definition", date(2024, 8, 26), date(2024, 8, 31)),
        ("definition", date(2024, 9, 1), date(2024, 9, 4)),
    ]


def test_a_covered_day_splits_a_run() -> None:
    days = _sessions(date(2024, 9, 2), date(2024, 9, 7))  # Mon–Fri
    covered = frozenset({_key(TickSchema.DEFINITION, date(2024, 9, 4))})
    plan = plan_purchases(_inputs(owned=_owned(days), covered=covered))
    assert _shape(plan) == [
        ("definition", date(2024, 9, 2), date(2024, 9, 4)),
        ("definition", date(2024, 9, 5), date(2024, 9, 7)),
    ]
    assert plan.wanted[("ES", TickSchema.DEFINITION)] == 4


def test_a_saturday_inside_a_run_does_not_split_it() -> None:
    days = _sessions(date(2024, 9, 6), date(2024, 9, 10))  # Fri, Sun, Mon, Tue...
    plan = plan_purchases(_inputs(owned=_owned(days)))
    (only,) = plan.requests
    assert only.request.start == date(2024, 9, 6)
    assert only.request.end == date(2024, 9, 10)
    assert date(2024, 9, 7) not in only.days
    assert only.days == (date(2024, 9, 6), date(2024, 9, 8), date(2024, 9, 9))


def test_pending_and_missing_are_tallied_and_not_planned() -> None:
    days = _sessions(date(2024, 9, 2), date(2024, 9, 7))
    conditions = {(CME_DATASET, d): AVAILABLE for d in days}
    conditions[(CME_DATASET, date(2024, 9, 3))] = DatasetCondition.PENDING
    conditions[(CME_DATASET, date(2024, 9, 5))] = DatasetCondition.MISSING
    plan = plan_purchases(_inputs(owned=_owned(days), conditions=conditions))
    assert _shape(plan) == [
        ("definition", date(2024, 9, 2), date(2024, 9, 3)),
        ("definition", date(2024, 9, 4), date(2024, 9, 5)),
        ("definition", date(2024, 9, 6), date(2024, 9, 7)),
    ]
    key = ("ES", TickSchema.DEFINITION)
    assert (plan.wanted[key], plan.pending[key], plan.missing[key]) == (5, 1, 1)


def test_degraded_is_purchasable() -> None:
    day = date(2024, 9, 3)
    conditions = {(CME_DATASET, day): DatasetCondition.DEGRADED}
    plan = plan_purchases(_inputs(owned=_owned([day]), conditions=conditions))
    assert len(plan.requests) == 1


def test_a_day_with_no_condition_or_past_the_edge_is_pending() -> None:
    days = [date(2024, 9, 3), date(2024, 9, 4)]
    conditions = {(CME_DATASET, days[0]): AVAILABLE, (CME_DATASET, days[1]): AVAILABLE}
    edge = {CME_DATASET: days[1]}  # exclusive: the 4th is past it
    plan = plan_purchases(
        _inputs(owned=_owned(days), conditions=conditions, edge_end=edge)
    )
    assert [p.days for p in plan.requests] == [(days[0],)]
    assert plan.pending[("ES", TickSchema.DEFINITION)] == 1
    unknown = plan_purchases(_inputs(owned=_owned(days), conditions={}))
    assert unknown.requests == ()
    assert unknown.pending[("ES", TickSchema.DEFINITION)] == 2


def test_no_tier_yields_companion_definitions_only() -> None:
    plan = plan_purchases(_inputs(owned=_owned([date(2024, 9, 3)], TickSchema.TBBO)))
    assert {p.schema for p in plan.requests} == {TickSchema.DEFINITION}
    request = plan.requests[0].request
    assert (request.symbols, request.stype_in) == (SYMBOLS, SType.PARENT)


def test_companions_of_both_tiers_on_one_day_are_one_request() -> None:
    day = date(2024, 9, 3)
    owned = _owned([day], TickSchema.TRADES) + _owned([day], TickSchema.TBBO)
    assert len(plan_purchases(_inputs(owned=owned)).requests) == 1


def test_nothing_owned_and_no_tier_plans_nothing() -> None:
    plan = plan_purchases(_inputs())
    assert plan.requests == ()
    assert plan.wanted == {}


def test_the_two_adopted_jobs_yield_four_definition_requests() -> None:
    """FR3: job 1 is 2024-08-30 → 09-30, job 2 is 2024-11-01 → 2025-01-01."""
    owned = _owned(_sessions(date(2024, 8, 30), date(2024, 9, 30)))
    owned += _owned(_sessions(date(2024, 11, 1), date(2025, 1, 1)), TickSchema.TBBO)
    plan = plan_purchases(_inputs(owned=owned))
    assert _shape(plan) == [
        ("definition", date(2024, 8, 30), date(2024, 8, 31)),
        ("definition", date(2024, 9, 1), date(2024, 9, 30)),
        ("definition", date(2024, 11, 1), date(2024, 11, 30)),
        ("definition", date(2024, 12, 1), date(2025, 1, 1)),
    ]
    assert all(p.request.symbols == SYMBOLS for p in plan.requests)


def test_a_universe_tier_wants_its_range_and_its_definitions() -> None:
    entry = TickUniverseEntry(
        "ES",
        SYMBOLS,
        SType.PARENT,
        TickSchema.TRADES,
        date(2024, 9, 2),
        date(2024, 9, 7),
    )
    plan = plan_purchases(_inputs(universe=[entry]))
    assert _shape(plan) == [
        ("definition", date(2024, 9, 2), date(2024, 9, 7)),
        ("trades", date(2024, 9, 2), date(2024, 9, 7)),
    ]


def test_an_open_ended_range_runs_to_the_edge_and_needs_one() -> None:
    entry = TickUniverseEntry(
        "ES", SYMBOLS, SType.PARENT, TickSchema.TRADES, date(2024, 9, 2), None
    )
    plan = plan_purchases(
        _inputs(universe=[entry], edge_end={CME_DATASET: date(2024, 9, 4)})
    )
    trades = [p for p in plan.requests if p.schema is TickSchema.TRADES]
    assert trades[0].request.end == date(2024, 9, 4)
    with pytest.raises(ValueError, match="availability edge"):
        plan_purchases(_inputs(universe=[entry], edge_end={}))


def test_a_window_narrows_and_never_widens() -> None:
    entry = TickUniverseEntry(
        "ES",
        SYMBOLS,
        SType.PARENT,
        TickSchema.TRADES,
        date(2024, 9, 2),
        date(2024, 9, 7),
    )
    narrowed = plan_purchases(
        _inputs(
            universe=[entry], window_start=date(2024, 9, 4), window_end=date(2024, 9, 6)
        )
    )
    assert {(p.request.start, p.request.end) for p in narrowed.requests} == {
        (date(2024, 9, 4), date(2024, 9, 6))
    }
    wider = plan_purchases(
        _inputs(
            universe=[entry], window_start=date(2024, 1, 1), window_end=date(2025, 1, 1)
        )
    )
    assert _shape(wider) == _shape(plan_purchases(_inputs(universe=[entry])))


def test_the_window_narrows_companion_wants_too() -> None:
    owned = _owned(_sessions(date(2024, 9, 2), date(2024, 9, 7)))
    plan = plan_purchases(_inputs(owned=owned, window_end=date(2024, 9, 4)))
    assert _shape(plan) == [("definition", date(2024, 9, 2), date(2024, 9, 4))]


def test_definitions_go_before_tiers_within_a_day_and_days_in_order() -> None:
    early = TickUniverseEntry(
        "ES",
        SYMBOLS,
        SType.PARENT,
        TickSchema.TRADES,
        date(2024, 9, 2),
        date(2024, 9, 4),
    )
    late_owned = _owned([date(2024, 11, 4)])
    plan = plan_purchases(_inputs(universe=[early], owned=late_owned))
    assert [(p.schema, p.request.start) for p in plan.requests] == [
        (TickSchema.DEFINITION, date(2024, 9, 2)),
        (TickSchema.TRADES, date(2024, 9, 2)),
        (TickSchema.DEFINITION, date(2024, 11, 4)),
    ]


def test_a_wanted_day_that_is_no_session_day_is_refused() -> None:
    saturday = date(2024, 9, 7)
    conditions = {(CME_DATASET, saturday): AVAILABLE}
    with pytest.raises(ValueError, match="not a session day"):
        plan_purchases(_inputs(owned=_owned([saturday]), conditions=conditions))


# -- history wants (slice 302 D5) ---------------------------------------------


def _entry(
    *history: HistoryWant, tier: TickSchema | None = TickSchema.TBBO
) -> TickUniverseEntry:
    return TickUniverseEntry(
        "ES",
        SYMBOLS,
        SType.PARENT,
        tier,
        date(2024, 9, 2),
        date(2024, 9, 4),
        history=history,
    )


def test_a_definition_history_want_unions_with_the_companion_set() -> None:
    wide = HistoryWant(TickSchema.DEFINITION, date(2024, 9, 2), date(2024, 9, 10))
    plan = plan_purchases(_inputs(universe=[_entry(wide)]))
    definitions = [p for p in plan.requests if p.schema is TickSchema.DEFINITION]
    assert [(p.request.start, p.request.end) for p in definitions] == [
        (date(2024, 9, 2), date(2024, 9, 10))
    ]  # the tier's two companion days sit inside the want: one request, no repeat
    assert plan.wanted[("ES", TickSchema.DEFINITION)] == 7


def test_bars_are_planned_over_their_own_range_without_companions() -> None:
    bars = HistoryWant(TickSchema.OHLCV_1M, date(2024, 9, 2), date(2024, 9, 7))
    plan = plan_purchases(_inputs(universe=[_entry(bars)]))
    assert _shape(plan) == [
        ("definition", date(2024, 9, 2), date(2024, 9, 4)),  # the tier's only
        ("ohlcv-1m", date(2024, 9, 2), date(2024, 9, 7)),
        ("tbbo", date(2024, 9, 2), date(2024, 9, 4)),
    ]


def test_definitions_are_ordered_before_bars_within_a_day() -> None:
    definitions = HistoryWant(TickSchema.DEFINITION, date(2024, 9, 2), date(2024, 9, 7))
    bars = HistoryWant(TickSchema.OHLCV_1M, date(2024, 9, 2), date(2024, 9, 7))
    plan = plan_purchases(_inputs(universe=[_entry(bars, definitions)]))
    schemas = [p.schema for p in plan.requests]
    assert schemas.index(TickSchema.DEFINITION) < schemas.index(TickSchema.OHLCV_1M)
    assert schemas[0] is TickSchema.DEFINITION


def test_a_multi_month_want_makes_one_request_per_month_run() -> None:
    bars = HistoryWant(TickSchema.OHLCV_1M, date(2024, 8, 26), date(2024, 10, 3))
    plan = plan_purchases(_inputs(universe=[_entry(bars, tier=None)]))
    assert _shape(plan) == [
        ("ohlcv-1m", date(2024, 8, 26), date(2024, 8, 31)),
        ("ohlcv-1m", date(2024, 9, 1), date(2024, 10, 1)),
        ("ohlcv-1m", date(2024, 10, 1), date(2024, 10, 3)),
    ]


def test_covered_bar_days_are_dropped() -> None:
    bars = HistoryWant(TickSchema.OHLCV_1M, date(2024, 9, 2), date(2024, 9, 7))
    covered = frozenset({_key(TickSchema.OHLCV_1M, date(2024, 9, 4))})
    plan = plan_purchases(_inputs(universe=[_entry(bars, tier=None)], covered=covered))
    assert _shape(plan) == [
        ("ohlcv-1m", date(2024, 9, 2), date(2024, 9, 4)),
        ("ohlcv-1m", date(2024, 9, 5), date(2024, 9, 7)),
    ]


def test_an_open_ended_history_want_runs_to_the_edge() -> None:
    bars = HistoryWant(TickSchema.OHLCV_1M, date(2024, 9, 2))
    plan = plan_purchases(
        _inputs(
            universe=[_entry(bars, tier=None)], edge_end={CME_DATASET: date(2024, 9, 5)}
        )
    )
    assert _shape(plan) == [("ohlcv-1m", date(2024, 9, 2), date(2024, 9, 5))]


def test_a_window_narrows_history_wants_too() -> None:
    bars = HistoryWant(TickSchema.OHLCV_1M, date(2024, 9, 2), date(2024, 9, 7))
    plan = plan_purchases(
        _inputs(universe=[_entry(bars, tier=None)], window_end=date(2024, 9, 4))
    )
    assert _shape(plan) == [("ohlcv-1m", date(2024, 9, 2), date(2024, 9, 4))]


def test_empty_history_leaves_the_plan_unchanged() -> None:
    plain = _entry()
    explicit = TickUniverseEntry(
        "ES", SYMBOLS, SType.PARENT, TickSchema.TBBO, date(2024, 9, 2), date(2024, 9, 4)
    )
    assert plan_purchases(_inputs(universe=[plain])) == plan_purchases(
        _inputs(universe=[explicit])
    )


def test_planner_imports_no_psycopg_or_provider_client() -> None:
    tree = ast.parse(Path(planner.__file__).read_text())
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    banned = ("psycopg", "databento", "manta_trading.data.tick.databento")
    assert not [m for m in imported if m.startswith(banned)]
    assert "manta_trading.data.tick.manifest_repo" not in imported


# -- the day span the pass loads sessions for (slice 302) ----------------------


def test_wanted_spans_cover_history_wants_not_just_tiers() -> None:
    from manta_trading.data.tick.purchase_plan import wanted_spans

    bars = HistoryWant(TickSchema.OHLCV_1M, date(2012, 1, 3), date(2012, 6, 1))
    definitions = HistoryWant(TickSchema.DEFINITION, date(2011, 12, 1))
    entry = _entry(definitions, bars)
    edges = {CME_DATASET: date(2026, 11, 1)}
    spans = wanted_spans([entry], [], edges)
    # The tier's days (2024) and the open definition want (2011 to the edge).
    assert spans == {"ES": (date(2011, 12, 1), date(2026, 11, 1))}


def test_wanted_spans_for_a_closed_history_want_only() -> None:
    from manta_trading.data.tick.purchase_plan import wanted_spans

    bars = HistoryWant(TickSchema.OHLCV_1M, date(2012, 1, 3), date(2012, 6, 1))
    entry = _entry(bars, tier=None)
    spans = wanted_spans([entry], [], {})
    assert spans == {"ES": (date(2012, 1, 3), date(2012, 6, 1))}


def test_an_open_history_want_without_an_edge_has_no_span() -> None:
    from manta_trading.data.tick.purchase_plan import wanted_spans

    bars = HistoryWant(TickSchema.OHLCV_1M, date(2012, 1, 3))
    assert wanted_spans([_entry(bars, tier=None)], [], {}) == {}
