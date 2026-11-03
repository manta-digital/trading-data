"""The ``unit_day`` check: every bar lies in the unit's UTC day (slice 302, D4).

A bar unit is one whole UTC day of a request, so a bar stamped outside
``[day 00:00 UTC, day + 1 00:00 UTC)`` is a defect in the file or its request.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np

from manta_trading.data.tick.ingest_checks import IngestCheck, unit_day_reason
from manta_trading.data.tick.ingest_records import I64, UnitCheckFailed, utc_ns


def unit_day_bounds_ns(unit_date: date) -> tuple[int, int]:
    """``[start, end)`` of the UTC day as epoch nanoseconds."""
    start = datetime(unit_date.year, unit_date.month, unit_date.day, tzinfo=UTC)
    return utc_ns(start), utc_ns(start + timedelta(days=1))


def check_unit_day(ts_event: I64, unit_date: date) -> None:
    """Raise ``unit_day`` unless every ``ts_event`` is in the unit's UTC day."""
    start, end = unit_day_bounds_ns(unit_date)
    ts = ts_event.astype(np.int64)
    outside = (ts < start) | (ts >= end)
    if outside.any():
        first = int(ts[np.argmax(outside)])
        reason = unit_day_reason(int(outside.sum()), unit_date, first)
        raise UnitCheckFailed(IngestCheck.UNIT_DAY, reason)
