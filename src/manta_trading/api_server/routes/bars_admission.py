"""What ``GET /api/v1/bars/{symbol}`` refuses before any database work.

A window is admitted on an estimate computed from the request alone (186 D4),
and ``limit`` (issue #13) on its own value, so a request that cannot be served
costs one comparison, not a pooled connection and an executor thread.
"""

from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from fastapi import status as http_status

from manta_trading.api_server.admission import (
    Refusal,
    RefusalReason,
    range_reversed,
)
from manta_trading.api_server.latest_bars import bars_per_calendar_day
from manta_trading.constants import (
    GRANULARITY_BAR_MINUTES,
    PRINTS_ALL_1M_ONLY_MESSAGE,
    Granularity,
    PrintMode,
    SessionMode,
)


def _estimate_bars(granularity: Granularity, start: date, end: date) -> float:
    """Estimate the bars an inclusive ``[start, end]`` window could contain.

    Computed from the request alone — no database work — so a rejection costs
    one comparison (186 D4). It is an admission policy on the *window*, not a
    promise about ``count``: a sparse symbol over an admitted span returns far
    fewer bars.
    """
    span_days = (end - start).days + 1
    return span_days * bars_per_calendar_day(granularity)


def max_span_days(granularity: Granularity, ceiling: int) -> int:
    """Largest inclusive window this granularity can request under ``ceiling``.

    Derived from the live ceiling so an operator override never yields a
    message that contradicts the limit actually enforced.
    """
    return int(ceiling / bars_per_calendar_day(granularity))


def _admit_range(
    granularity: Granularity, start: date, end: date, ceiling: int
) -> None:
    """Reject a window that cannot be served, before any DB work (186 D4).

    Raises:
        HTTPException: 422 for a reversed range, and 422 when the estimated bar
            count exceeds ``ceiling``. Both messages carry what the caller needs
            to fix the request; neither number is a literal.
    """
    if start > end:
        raise range_reversed(
            f"start ({start.isoformat()}) is after end ({end.isoformat()}); "
            "the requested range is empty"
        )

    estimate = _estimate_bars(granularity, start, end)
    if estimate > ceiling:
        max_days = max_span_days(granularity, ceiling)
        # The prose is unchanged: trading-ui parsed it before the fields existed
        # (issue #23). ``estimate`` rounds as the prose's ``,.0f`` does.
        raise Refusal(
            http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"requested range spans about {estimate:,.0f} {granularity.value} "
            f"bars, over the {ceiling:,} bar limit; at {granularity.value} "
            f"request at most {max_days:,} days per call, or use a coarser "
            "granularity",
            reason=RefusalReason.ROW_CEILING,
            limit=ceiling,
            estimate=round(estimate),
            granularity=granularity.value,
            max_span_days=max_days,
        )


def _check_session(granularity: Granularity, session: SessionMode) -> None:
    """Session bars are intraday only (174 D6)."""
    if session is not SessionMode.UTC and granularity not in GRANULARITY_BAR_MINUTES:
        raise Refusal(
            http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"session={session.value} applies to intraday granularities "
            f"({', '.join(g.value for g in GRANULARITY_BAR_MINUTES)}); "
            f"{granularity.value} bars are one per session or longer",
            reason=RefusalReason.SESSION_GRANULARITY,
            granularity=granularity.value,
            session=session.value,
        )


def check_request(
    granularity: Granularity,
    options: tuple[PrintMode, SessionMode],
    span: tuple[date | None, date | None, int | None],
    ceiling: int,
) -> None:
    """Every refusal decided from the request alone, before any DB work.

    In session mode ``prints=all`` is served at every intraday size (session
    bars are built from 1m bars), and the window is admitted on the session
    rows instead (``bars_session_admission``), so the 186 estimate applies to
    ``utc`` only.
    """
    prints, session = options
    start, end, limit = span
    _check_session(granularity, session)
    utc = session is SessionMode.UTC
    if prints is PrintMode.ALL and utc and granularity != Granularity.M1:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=PRINTS_ALL_1M_ONLY_MESSAGE,
        )
    if limit is None:
        if start is None or end is None:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="start and end are required unless limit is given",
            )
        if utc:
            _admit_range(granularity, start, end, ceiling)
        elif start > end:
            raise range_reversed(
                f"start ({start.isoformat()}) is after end ({end.isoformat()}); "
                "the requested range is empty"
            )
        return
    if start is not None:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="limit counts back from end; omit start",
        )
    if limit > ceiling:
        raise Refusal(
            http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"limit {limit:,} is over the {ceiling:,} bar limit",
            reason=RefusalReason.ROW_CEILING,
            limit=ceiling,
            estimate=limit,
        )
