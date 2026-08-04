"""How ``TimescaleMinuteDataDB.get_minute_data`` reports failure (slice 186).

A cancelled query and an empty window used to be the same value — an empty
DataFrame. After slice 186's D5 that ambiguity reached clients as
``200 {"count": 0}``, so a statement timeout looked exactly like a closed
market. Cancellation now propagates; every other failure keeps its previous
"log and return empty" behavior, which the CLI and daemon rely on.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import psycopg
import pytest

from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

_START = datetime(2024, 3, 1, tzinfo=UTC)
_END = datetime(2024, 3, 8, tzinfo=UTC)


def _db_raising(error: Exception) -> TimescaleMinuteDataDB:
    """A DB instance whose pooled connection raises on use."""
    with patch.object(TimescaleMinuteDataDB, "_init_pool"):
        db = TimescaleMinuteDataDB("postgresql://localhost/nonexistent")
    pool = MagicMock()
    pool.connection.side_effect = error
    db._pool = pool
    return db


def test_cancelled_query_propagates(caplog: pytest.LogCaptureFixture) -> None:
    """Re-raised so the API maps it to 504 (186 D10) instead of reporting
    "no bars" for a query that never finished."""
    db = _db_raising(psycopg.errors.QueryCanceled("statement timeout"))
    with (
        caplog.at_level(
            logging.WARNING, logger="manta_trading.market.timescale_minute_db"
        ),
        pytest.raises(psycopg.errors.QueryCanceled),
    ):
        db.get_minute_data("AAPL", _START, _END, None, adjusted=False)

    assert any("cancelled" in r.getMessage().lower() for r in caplog.records)


@pytest.mark.parametrize(
    "error",
    [
        psycopg.OperationalError("connection lost"),
        RuntimeError("unexpected"),
    ],
)
def test_other_failures_still_return_an_empty_frame(error: Exception) -> None:
    """Deliberately unchanged: the CLI and daemon treat an empty frame as "no
    data for this symbol" and narrowing that behavior is not this slice's
    business."""
    result: Any = _db_raising(error).get_minute_data(
        "AAPL", _START, _END, None, adjusted=False
    )
    assert isinstance(result, pd.DataFrame)
    assert result.empty
