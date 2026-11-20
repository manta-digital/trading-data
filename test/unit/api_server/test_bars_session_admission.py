"""Static session refusals of ``GET /api/v1/bars`` (slice 174 task 11.2)."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

from manta_trading.api_server.admission import Refusal, RefusalReason
from manta_trading.api_server.routes.bars_admission import check_request
from manta_trading.constants import Granularity, PrintMode, SessionMode

WINDOW = (date(2026, 1, 5), date(2026, 1, 9), None)
CEILING = 75_000


@pytest.mark.parametrize(
    "granularity", [Granularity.D1, Granularity.W1, Granularity.MO1, Granularity.Q1]
)
@pytest.mark.parametrize("session", [SessionMode.REGULAR, SessionMode.EXTENDED])
def test_session_with_daily_or_longer_is_refused(
    granularity: Granularity, session: SessionMode
) -> None:
    with pytest.raises(Refusal) as raised:
        check_request(granularity, (PrintMode.ELIGIBLE, session), WINDOW, CEILING)
    assert raised.value.status_code == 422
    assert raised.value.fields == {
        "reason": RefusalReason.SESSION_GRANULARITY,
        "granularity": granularity.value,
        "session": session.value,
    }


def test_utc_daily_is_admitted() -> None:
    check_request(
        Granularity.D1, (PrintMode.ELIGIBLE, SessionMode.UTC), WINDOW, CEILING
    )


def test_prints_all_at_1h_admitted_in_session_mode_refused_for_utc() -> None:
    check_request(Granularity.H1, (PrintMode.ALL, SessionMode.REGULAR), WINDOW, CEILING)
    with pytest.raises(HTTPException) as raised:
        check_request(Granularity.H1, (PrintMode.ALL, SessionMode.UTC), WINDOW, CEILING)
    assert raised.value.status_code == 422


def test_session_window_skips_the_utc_estimate() -> None:
    """A year of 1m is over the 186 estimate; session mode is admitted on its
    session rows instead (bars_session_admission)."""
    year = (date(2025, 1, 1), date(2025, 12, 31), None)
    with pytest.raises(Refusal):
        check_request(
            Granularity.M1, (PrintMode.ELIGIBLE, SessionMode.UTC), year, CEILING
        )
    check_request(
        Granularity.M1, (PrintMode.ELIGIBLE, SessionMode.REGULAR), year, CEILING
    )


def test_session_reversed_range_is_refused() -> None:
    reversed_span = (date(2026, 1, 9), date(2026, 1, 5), None)
    with pytest.raises(Refusal) as raised:
        check_request(
            Granularity.H1,
            (PrintMode.ELIGIBLE, SessionMode.REGULAR),
            reversed_span,
            CEILING,
        )
    assert raised.value.fields["reason"] == RefusalReason.RANGE_REVERSED


def test_session_limit_over_the_row_ceiling_is_refused() -> None:
    with pytest.raises(Refusal) as raised:
        check_request(
            Granularity.H1,
            (PrintMode.ELIGIBLE, SessionMode.REGULAR),
            (None, None, CEILING + 1),
            CEILING,
        )
    assert raised.value.fields["reason"] == RefusalReason.ROW_CEILING
