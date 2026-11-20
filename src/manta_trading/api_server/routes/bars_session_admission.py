"""What a session bars request is refused for, on its session rows (slice 174 D6).

Runs in the executor after the static checks (``bars_admission``). The symbol's
calendar gives the horizon; the range's ``trading_sessions`` rows, read once,
give the estimates: bars as ``ceil(phase_minutes / width)`` per phase against
the row ceiling, and phase minutes against the session minute ceiling. A
``limit`` read is admitted on ``limit`` alone (a minute bound for it is issue
#82), so only its horizon is checked here.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import psycopg
from fastapi import status as http_status

from manta_trading.api_server.admission import Refusal, RefusalReason
from manta_trading.constants import GRANULARITY_BAR_MINUTES, Granularity, SessionMode
from manta_trading.data.base.symbol_calendar import SymbolCalendar, symbol_calendar
from manta_trading.market.session_bars import session_bounds
from manta_trading.market.session_phases import estimate_bars, estimate_minutes

_422 = http_status.HTTP_422_UNPROCESSABLE_CONTENT


def _horizon(calendar: SymbolCalendar, end: date) -> None:
    bound = calendar.holidays_seeded_through
    if end > bound:
        raise Refusal(
            _422,
            f"{calendar.calendar_id} sessions are known through {bound.isoformat()}; "
            f"end {end.isoformat()} is past them",
            reason=RefusalReason.CALENDAR_HORIZON,
            holidays_seeded_through=bound.isoformat(),
        )


def admit_session_request(
    conn: psycopg.Connection[Any],
    symbol: str,
    request: tuple[Granularity, SessionMode],
    span: tuple[date | None, date | None, int | None],
    ceilings: tuple[int, int],
) -> None:
    """Refuse a session request past the horizon or over a ceiling.

    ``ceilings`` is (rows, minutes). An unknown symbol raises
    ``UnknownSymbolError`` (the app's 404).
    """
    granularity, mode = request
    start, end, limit = span
    max_rows, max_minutes = ceilings
    calendar = symbol_calendar(conn, symbol)
    if end is not None:
        _horizon(calendar, end)
    if limit is not None:
        return
    assert start is not None and end is not None  # check_request
    bounds = session_bounds(conn, calendar.calendar_id, start, end, mode)
    width = timedelta(minutes=GRANULARITY_BAR_MINUTES[granularity])
    bars = estimate_bars(bounds, mode, width)
    if bars > max_rows:
        raise Refusal(
            _422,
            f"requested range spans {bars:,} {granularity.value} {mode.value} session "
            f"bars, over the {max_rows:,} bar limit; narrow start/end or use a "
            "coarser granularity",
            reason=RefusalReason.ROW_CEILING,
            limit=max_rows,
            estimate=bars,
            granularity=granularity.value,
        )
    minutes = estimate_minutes(bounds, mode)
    if minutes > max_minutes:
        raise Refusal(
            _422,
            f"requested range reads {minutes:,} {mode.value} session minutes, over "
            f"the {max_minutes:,} minute limit; narrow start/end",
            reason=RefusalReason.MINUTE_CEILING,
            limit=max_minutes,
            estimate=minutes,
        )
