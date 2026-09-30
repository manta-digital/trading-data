"""The pure spend and space guards (slice 224, LLD Technical Decision 7; FR4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from manta_trading.data.tick.constants import (
    TICK_SPEND_30D_CEILING_ENV,
    TICK_SPEND_CEILING_ENV,
    TICK_SPEND_WINDOW,
)
from manta_trading.data.tick.spend_guard import (
    ListedJob,
    PlannedCost,
    TrailingRow,
    UnheldJob,
    VerdictStatus,
    evaluate_space,
    evaluate_spend,
    unheld_listed,
)

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
D = Decimal
PER_PASS = D("1")
CAP_30D = D("5")


def _spend(
    planned: list[PlannedCost],
    *,
    trailing: list[TrailingRow] | None = None,
    unheld: list[UnheldJob] | None = None,
    per_pass: Decimal | None = PER_PASS,
    cap: Decimal | None = CAP_30D,
    estimate_only: bool = False,
):
    return evaluate_spend(
        planned, trailing or [], unheld or [], per_pass, cap, NOW, estimate_only
    )


def _row(cost: str, days_ago: float, job_id: str | None = None) -> TrailingRow:
    return TrailingRow(NOW - timedelta(days=days_ago), D(cost), job_id)


def _plan(*costs: str) -> list[PlannedCost]:
    return [PlannedCost(D(c)) for c in costs]


@pytest.mark.parametrize(
    ("per_pass", "cap"), [(None, D("5")), (D("1"), None), (None, None)]
)
def test_either_ceiling_absent_refuses_naming_both_variables(
    per_pass: Decimal | None, cap: Decimal | None
) -> None:
    verdict = _spend(_plan("0.004"), per_pass=per_pass, cap=cap)
    assert verdict.status is VerdictStatus.REFUSED
    text = " ".join(verdict.reasons)
    assert TICK_SPEND_CEILING_ENV in text
    assert TICK_SPEND_30D_CEILING_ENV in text
    absent = {TICK_SPEND_CEILING_ENV} if per_pass is None else set()
    absent |= {TICK_SPEND_30D_CEILING_ENV} if cap is None else set()
    assert set(verdict.absent) == absent


def test_a_plan_inside_both_ceilings_is_allowed() -> None:
    verdict = _spend(_plan("0.4", "0.5"), trailing=[_row("1", 3)])
    assert verdict.allowed
    assert verdict.planned_total == D("0.9")
    assert verdict.reasons == ()


def test_inside_per_pass_but_over_the_30_day_cap_reports_overage_and_fit_date() -> None:
    trailing = [_row("2", 20), _row("2.5", 10)]
    verdict = _spend(_plan("0.8"), trailing=trailing)
    assert verdict.status is VerdictStatus.REFUSED
    assert verdict.over_30d == D("0.3")
    assert verdict.over_per_pass == 0
    # ageing the 20-day-old $2 out first is enough: 2.5 + 0.8 <= 5
    assert verdict.fits_from == NOW - timedelta(days=20) + TICK_SPEND_WINDOW
    assert not verdict.cap_must_be_raised
    assert "0.3000" in " ".join(verdict.reasons)


def test_the_fit_date_needs_as_many_rows_aged_out_as_it_takes() -> None:
    trailing = [_row("2", 25), _row("2", 20), _row("1", 5)]
    verdict = _spend(_plan("1"), trailing=trailing, cap=D("3"), per_pass=D("1"))
    # 5 + 1 > 3; age out the 25-day (→ 3+1 > 3) then the 20-day (→ 1+1 <= 3)
    assert verdict.fits_from == NOW - timedelta(days=20) + TICK_SPEND_WINDOW


def test_planned_alone_over_the_cap_says_raise() -> None:
    verdict = _spend(_plan("0.9"), per_pass=D("1"), cap=D("0.5"))
    assert verdict.status is VerdictStatus.REFUSED
    assert verdict.cap_must_be_raised
    assert verdict.fits_from is None
    assert "must be raised" in " ".join(verdict.reasons)


def test_over_the_per_pass_ceiling_refuses_with_the_overage() -> None:
    verdict = _spend(_plan("0.7", "0.6"), per_pass=D("1"))
    assert verdict.status is VerdictStatus.REFUSED
    assert verdict.over_per_pass == D("0.3")


def test_a_zero_dollar_plan_passes() -> None:
    assert _spend(_plan("0")).allowed


def test_an_empty_plan_is_allowed_whatever_the_settings() -> None:
    assert _spend([], per_pass=None, cap=None).allowed


def test_adopted_and_unaccepted_rows_in_the_window_count_rows_outside_do_not() -> None:
    inside = [_row("2", 29.9), _row("2.5", 1)]  # e.g. an adopted and a refused one
    outside = [_row("50", 30.1)]
    verdict = _spend(_plan("0.6"), trailing=inside + outside)
    assert verdict.trailing_total == D("4.5")
    assert verdict.status is VerdictStatus.REFUSED
    assert _spend(_plan("0.5"), trailing=inside + outside).allowed


def test_estimate_only_never_allows_but_still_reports() -> None:
    verdict = _spend(_plan("0.4"), estimate_only=True)
    assert verdict.status is VerdictStatus.ESTIMATE_ONLY
    assert not verdict.allowed
    assert verdict.planned_total == D("0.4")
    over = _spend(_plan("2"), estimate_only=True)
    assert over.status is VerdictStatus.ESTIMATE_ONLY
    assert over.over_per_pass == D("1")


def test_an_unheld_job_counts_and_is_named_in_the_refusal() -> None:
    unheld = [UnheldJob("GLBX-UNHELD", NOW - timedelta(days=2), D("4.5"))]
    verdict = _spend(_plan("0.6"), unheld=unheld)
    assert verdict.status is VerdictStatus.REFUSED
    assert verdict.unheld_total == D("4.5")
    assert "GLBX-UNHELD" in " ".join(verdict.reasons)
    assert verdict.over_30d == D("0.1")


def test_a_re_submit_already_in_the_trailing_rows_is_not_added_twice() -> None:
    trailing = [_row("4.5", 0.1)]  # its estimate, already on its request row
    planned = [PlannedCost(D("0.9"), counted=True)]
    verdict = _spend(planned, trailing=trailing, cap=D("5"), per_pass=D("1"))
    assert verdict.allowed  # 4.5 <= 5, not 4.5 + 0.9
    assert verdict.planned_total == D("0.9")  # but it still counts per pass
    assert _spend(planned, trailing=trailing, per_pass=D("0.5")).over_per_pass == D(
        "0.4"
    )


def test_a_listed_job_a_row_holds_is_not_unheld() -> None:
    listed = [
        ListedJob("GLBX-HELD", NOW, D("3")),
        ListedJob("GLBX-OTHER", NOW, None),
    ]
    rows = [_row("3", 1, job_id="GLBX-HELD")]
    assert [job.job_id for job in unheld_listed(listed, rows)] == ["GLBX-OTHER"]
    with_held = _spend(_plan("0.5"), trailing=rows)
    assert with_held.trailing_total == D("3")
    assert with_held.unheld_total == 0


def test_an_unpriced_unheld_job_is_counted_at_the_supplied_request_cost() -> None:
    unpriced = ListedJob("GLBX-UNPRICED", NOW, None)
    (kept,) = unheld_listed([unpriced], [])
    assert kept.cost is None  # the caller resolves it with cost(job.request)
    resolved = UnheldJob(kept.job_id, kept.at, D("1.2"))
    assert _spend(_plan("0.1"), unheld=[resolved]).unheld_total == D("1.2")


def test_space_allowed_and_shortfall() -> None:
    assert evaluate_space(100, 100).allowed
    verdict = evaluate_space(900, 400)
    assert not verdict.allowed
    assert verdict.shortfall == 500
    assert "500 short" in (verdict.reason or "")
    assert evaluate_space(0, 0).reason is None
