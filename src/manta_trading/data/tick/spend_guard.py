"""The spend and space guards: pure verdicts before any paid call (224).

LLD 224 Technical Decision 7. No I/O: the caller hands in the planned costs,
the manifest's trailing spend rows and the provider's unheld jobs.

The spend guard is **all-or-nothing** and needs **both ceilings**. It allows a
purchase only when

1. both settings are present;
2. ``Σ planned ≤`` the per-pass ceiling;
3. ``trailing + unheld + Σ planned`` (re-submits already in ``trailing`` are
   not added again) ``≤`` the 30-day cap;
4. ``--estimate-only`` is not set.

Money is ``Decimal`` throughout. A refusal reports the planned total, each
ceiling, each overage, the absent settings, each unheld job by id, and for the
30-day cap the instant from which the plan fits (ageing the oldest in-window
spend out one item at a time) or that the cap must be raised.

An empty plan (nothing wanted) is allowed whatever the settings: there is
nothing to spend.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from manta_trading.data.tick.constants import (
    TICK_SPEND_30D_CEILING_ENV,
    TICK_SPEND_CEILING_ENV,
    TICK_SPEND_WINDOW,
)

_ZERO = Decimal(0)


class VerdictStatus(StrEnum):
    ALLOWED = "allowed"
    REFUSED = "refused"
    ESTIMATE_ONLY = "estimate_only"


@dataclass(frozen=True)
class PlannedCost:
    """One request's cost. ``counted`` marks a re-submit whose estimate is
    already in the trailing rows (its row is reused, so it is never added twice)."""

    cost: Decimal
    counted: bool = False


@dataclass(frozen=True)
class TrailingRow:
    """A manifest request row: ``COALESCE(actual, estimated)`` cost at
    ``COALESCE(committed_at, requested_at)``."""

    at: datetime
    cost: Decimal
    job_id: str | None


@dataclass(frozen=True)
class ListedJob:
    """A job in the provider's list; ``cost`` is ``None`` while unpriced."""

    job_id: str
    at: datetime
    cost: Decimal | None


@dataclass(frozen=True)
class UnheldJob:
    """A listed job no manifest row holds, with its cost resolved."""

    job_id: str
    at: datetime
    cost: Decimal


@dataclass(frozen=True)
class SpendVerdict:
    status: VerdictStatus
    planned_total: Decimal
    trailing_total: Decimal
    unheld_total: Decimal
    unheld_jobs: tuple[UnheldJob, ...]
    per_pass_ceiling: Decimal | None
    cap_30d: Decimal | None
    over_per_pass: Decimal
    over_30d: Decimal
    absent: tuple[str, ...]
    fits_from: datetime | None
    cap_must_be_raised: bool
    reasons: tuple[str, ...]

    @property
    def allowed(self) -> bool:
        return self.status is VerdictStatus.ALLOWED


@dataclass(frozen=True)
class SpaceVerdict:
    allowed: bool
    planned_bytes: int
    free_bytes: int
    shortfall: int

    @property
    def reason(self) -> str | None:
        if self.allowed:
            return None
        return (
            f"archive volume has {self.free_bytes} bytes free; the plan needs "
            f"{self.planned_bytes} ({self.shortfall} short)"
        )


def unheld_listed(
    listed: Sequence[ListedJob], trailing_rows: Sequence[TrailingRow]
) -> list[ListedJob]:
    """Listed jobs whose id no manifest row holds: spend the manifest cannot see."""
    held = {row.job_id for row in trailing_rows if row.job_id is not None}
    return [job for job in listed if job.job_id not in held]


def evaluate_space(planned_bytes: int, free_bytes: int) -> SpaceVerdict:
    """Allowed when the archive volume can hold the planned billable bytes."""
    shortfall = max(planned_bytes - free_bytes, 0)
    return SpaceVerdict(shortfall == 0, planned_bytes, free_bytes, shortfall)


def _usd(amount: Decimal) -> str:
    return f"${amount:.4f}"


def _over(total: Decimal, ceiling: Decimal | None) -> Decimal:
    return _ZERO if ceiling is None else max(total - ceiling, _ZERO)


def _fits_from(
    trailing: Sequence[TrailingRow],
    unheld: Sequence[UnheldJob],
    planned_new: Decimal,
    cap: Decimal,
) -> datetime | None:
    """The instant the plan fits, ageing in-window spend out oldest first.

    ``None`` when the planned total alone exceeds the cap: no wait helps.
    """
    if planned_new > cap:
        return None
    aging = sorted(
        [(row.at, row.cost) for row in trailing]
        + [(job.at, job.cost) for job in unheld]
    )
    held = sum((cost for _, cost in aging), _ZERO)
    for at, cost in aging:
        held -= cost
        if held + planned_new <= cap:
            return at + TICK_SPEND_WINDOW
    return None  # only reached when nothing is in the window to age out


def _cap_reasons(
    trailing_total: Decimal,
    unheld_total: Decimal,
    planned_new: Decimal,
    over: Decimal,
    cap: Decimal,
    fits_from: datetime | None,
) -> list[str]:
    fit = (
        "the cap must be raised for this plan"
        if fits_from is None
        else f"the plan fits from {fits_from.isoformat()}"
    )
    return [
        f"trailing {_usd(trailing_total)} + unheld {_usd(unheld_total)} + planned "
        f"{_usd(planned_new)} is {_usd(over)} over the 30-day cap {_usd(cap)}",
        fit,
    ]


def evaluate_spend(
    planned: Sequence[PlannedCost],
    trailing_rows: Sequence[TrailingRow],
    unheld_jobs: Sequence[UnheldJob],
    per_pass: Decimal | None,
    cap_30d: Decimal | None,
    now: datetime,
    estimate_only: bool,
) -> SpendVerdict:
    """The verdict for one run's plan; see the module docstring."""
    cutoff = now - TICK_SPEND_WINDOW
    trailing = [row for row in trailing_rows if row.at >= cutoff]
    unheld = [job for job in unheld_jobs if job.at >= cutoff]
    planned_total = sum((p.cost for p in planned), _ZERO)
    planned_new = sum((p.cost for p in planned if not p.counted), _ZERO)
    trailing_total = sum((row.cost for row in trailing), _ZERO)
    unheld_total = sum((job.cost for job in unheld), _ZERO)
    absent = tuple(
        env
        for env, value in (
            (TICK_SPEND_CEILING_ENV, per_pass),
            (TICK_SPEND_30D_CEILING_ENV, cap_30d),
        )
        if value is None
    )
    over_pass = _over(planned_total, per_pass)
    over_30d = _over(trailing_total + unheld_total + planned_new, cap_30d)
    breached = bool(planned) and bool(absent or over_pass or over_30d)
    reasons: list[str] = []
    fits_from = None
    if breached and absent:
        reasons.append(
            f"both {TICK_SPEND_CEILING_ENV} and {TICK_SPEND_30D_CEILING_ENV} must "
            f"be set to buy; absent: {', '.join(absent)}"
        )
    if breached and over_pass and per_pass is not None:
        reasons.append(
            f"planned {_usd(planned_total)} is {_usd(over_pass)} over the per-pass "
            f"ceiling {_usd(per_pass)}"
        )
    if breached and over_30d and cap_30d is not None:
        fits_from = _fits_from(trailing, unheld, planned_new, cap_30d)
        reasons.extend(
            _cap_reasons(
                trailing_total, unheld_total, planned_new, over_30d, cap_30d, fits_from
            )
        )
    if breached:
        reasons.extend(
            f"unheld provider job {job.job_id} ({_usd(job.cost)}) counts toward the cap"
            for job in unheld
        )
    if estimate_only:
        status = VerdictStatus.ESTIMATE_ONLY
    else:
        status = VerdictStatus.REFUSED if breached else VerdictStatus.ALLOWED
    return SpendVerdict(
        status=status,
        planned_total=planned_total,
        trailing_total=trailing_total,
        unheld_total=unheld_total,
        unheld_jobs=tuple(unheld),
        per_pass_ceiling=per_pass,
        cap_30d=cap_30d,
        over_per_pass=over_pass,
        over_30d=over_30d,
        absent=absent,
        fits_from=fits_from,
        cap_must_be_raised=breached and bool(over_30d) and fits_from is None,
        reasons=tuple(reasons),
    )
