"""Whether a Databento plan includes a request (slice 300, D7).

Pure and total: no I/O, no clock read. A request is included only if *every*
day of it is inside the plan's window; a request straddling the boundary counts
in full, because the provider bills a request as a unit and is not known to
split it. The 365-day window is not proven shorter than Databento's real one:
check it against the first in-plan invoice (303).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from manta_trading.config.databento_plans import (
    PLAN_DATASET,
    PLAN_INCLUDED_HISTORY,
    DatabentoPlan,
)
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.provider import TickRequest


def is_plan_included(
    plan: DatabentoPlan | None,
    dataset: str,
    schema: TickSchema,
    start: date,
    end_exclusive: date,
    at: datetime,
) -> bool:
    """True if ``plan`` includes ``schema`` on ``dataset`` for ``[start, end)``.

    ``at`` is when the request is judged (submit time, or a job's
    ``ts_received``); it must be timezone-aware. No plan, or another dataset,
    is never included. A schema the plan's table lacks raises ``KeyError``.
    """
    if end_exclusive <= start:
        raise ValueError(f"empty request range: {start} to {end_exclusive}")
    if at.tzinfo is None:
        raise ValueError("`at` must be timezone-aware")
    if plan is None or dataset != PLAN_DATASET:
        return False
    window = PLAN_INCLUDED_HISTORY[plan][schema]
    if window is None:
        return True
    return start >= at.astimezone(UTC).date() - window


def request_is_plan_included(
    plan: DatabentoPlan | None, request: TickRequest, at: datetime
) -> bool:
    """:func:`is_plan_included` for a provider request."""
    return is_plan_included(
        plan, request.dataset, request.schema, request.start, request.end, at
    )
