"""The per-family health row: severity and detail (slice 305 D5). Pure."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from manta_trading.cli.commands.health import CheckSeverity
from manta_trading.cli.commands.health_tick import family_check
from manta_trading.data.tick.cross_tier_result import CrossTierResult
from manta_trading.data.tick.family_report import FamilyStatus, StatisticsFamilyStatus
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.statistics_status import ContentFinding
from manta_trading.data.tick.tick_status import TickSessionStatus

EDGE = datetime(2026, 11, 6, tzinfo=UTC)
BUCKETS = {s.value: 0 for s in TickSessionStatus} | {"complete": 4118}


def _report(**overrides: object) -> FamilyStatus:
    base = FamilyStatus(
        family=SchemaFamily.TICK_TIER.value,
        schemas=("tbbo",),
        schema="tbbo",
        wanted_start=date(2024, 11, 2),
        wanted_end=EDGE,
        sessions_held=4118,
        buckets=dict(BUCKETS),
        degraded=0,
        caught_up=True,
        holed_sessions=(),
        source_edge=EDGE,
        records=1,
        cross_tier=(),
        load_complete_only=False,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _buckets(**counts: int) -> dict[str, int]:
    return dict(BUCKETS) | counts


def _cross(over: int = 0, no_trades: int = 0) -> tuple[CrossTierResult, ...]:
    return (
        CrossTierResult(
            "traded_vs_cleared", SchemaFamily.TICK_TIER, 9, 3, over, no_trades
        ),
    )


def _statistics(findings: int) -> StatisticsFamilyStatus:
    return StatisticsFamilyStatus(
        **{**_report(schema="statistics").__dict__},
        findings=tuple(
            ContentFinding(date(2025, 1, 2), n, f"ES{n}", (1,)) for n in range(findings)
        ),
    )


FAILS = [
    pytest.param(_report(buckets=_buckets(failed=1)), id="failed unit"),
    pytest.param(_report(buckets=_buckets(retry_exhausted=1)), id="retry exhausted"),
    pytest.param(_report(cross_tier=_cross(over=1)), id="over verdict"),
]
INFOS = [
    pytest.param(
        _report(caught_up=None, source_edge=None),
        "no dataset edge; completeness not judged",
        id="no edge",
    ),
    pytest.param(
        _report(caught_up=False, buckets=_buckets(missing=13)),
        "not caught up: 13 missing",
        id="not caught up",
    ),
    pytest.param(_statistics(3), "3 content findings", id="content findings"),
    pytest.param(
        _report(cross_tier=_cross(no_trades=2)),
        "traded_vs_cleared tbbo: 0 over, 2 no_trades",
        id="no_trades",
    ),
]


@pytest.mark.parametrize("report", FAILS)
def test_a_defect_a_run_can_fix_is_a_counted_fail(report: FamilyStatus) -> None:
    check = family_check("ES", report)
    assert (check.ok, check.counted) == (False, True)
    assert check.name == "futures ES tbbo"


@pytest.mark.parametrize(("report", "detail"), INFOS)
def test_work_not_done_or_provider_facts_are_informational(
    report: FamilyStatus, detail: str
) -> None:
    check = family_check("ES", report)
    assert check.severity is CheckSeverity.INFORMATIONAL and not check.counted
    assert detail in check.detail


def test_caught_up_and_clean_is_a_counted_pass() -> None:
    check = family_check("ES", _report())
    assert (check.ok, check.counted) == (True, True)
    assert check.detail == "4,118/4,118 complete to 2026-11-06; caught up"


def test_a_failed_unit_outranks_a_missing_session() -> None:
    both = _report(caught_up=False, buckets=_buckets(failed=1, missing=2))
    check = family_check("ES", both)
    assert (check.ok, check.counted) == (False, True)
    assert "not caught up: 1 failed, 2 missing" in check.detail


def test_the_detail_names_buckets_cross_tier_counts_and_findings() -> None:
    gap = family_check(
        "ES",
        _report(caught_up=False, buckets=_buckets(complete=4118, missing=13)),
    )
    assert gap.detail == (
        "4,118/4,131 complete to 2026-11-06; not caught up: 13 missing"
    )
    volume = family_check("ES", _report(cross_tier=_cross(no_trades=2)))
    assert volume.detail == (
        "4,118/4,118 complete to 2026-11-06; caught up;"
        " traded_vs_cleared tbbo: 0 over, 2 no_trades"
    )
    content = family_check(
        "ES", replace(_statistics(3), buckets=_buckets(complete=5012))
    )
    assert content.detail == (
        "5,012/5,012 complete to 2026-11-06; caught up; 3 content findings"
    )


def test_a_family_with_no_statistics_check_says_so() -> None:
    check = family_check("ES", _report(load_complete_only=True))
    assert check.detail.endswith("load-complete only")


def test_a_caught_up_family_names_the_sessions_in_scope_outside_the_judged_range() -> (
    None
):
    check = family_check("ES", _report(buckets=_buckets(missing=2, edge_unknown=1)))
    assert check.detail == (
        "4,118/4,121 complete to 2026-11-06; caught up;"
        " other sessions in scope: 2 missing, 1 edge unknown"
    )
