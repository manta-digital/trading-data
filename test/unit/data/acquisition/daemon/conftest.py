"""Fixtures shared by the daemon runner behavior tests (slice 912 Task 5).

The simulation types themselves live in ``_harness.py``; this module holds only
what the tests need injected.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def today_at() -> Callable[..., datetime]:
    """Build an instant on the current UTC day.

    The cadence gates are expressed relative to *today's* UTC midnight, so a
    fixed calendar date would make these tests pass or fail by the date they
    happen to run on.
    """

    def _at(hour: int, minute: int = 0) -> datetime:
        today = datetime.now(UTC).date()
        return datetime(today.year, today.month, today.day, hour, minute, tzinfo=UTC)

    return _at


@pytest.fixture
def ca_update_done() -> Callable[[Callable[[], datetime]], Callable[[], MagicMock]]:
    """Build a ``conn_factory`` reporting the CA update as already done.

    ``ca_update_due`` is the runner's only self-owned query. Left due, it would
    set ``did_anything`` on every iteration and mask every idle path under test.
    The sentinel is stamped from the injected clock rather than wall-clock time,
    so the fixture cannot drift across a UTC-day boundary mid-test.
    """

    def _build(clock: Callable[[], datetime]) -> Callable[[], MagicMock]:
        def _factory() -> MagicMock:
            cur = MagicMock()
            cur.fetchone.return_value = (clock(),)
            cur.__enter__ = MagicMock(return_value=cur)
            cur.__exit__ = MagicMock(return_value=False)
            conn = MagicMock()
            conn.cursor.return_value = cur
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=conn)
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        return _factory

    return _build
