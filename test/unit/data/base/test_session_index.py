"""Unit tests for Session / SessionIndex (221 D7). No DB."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import numpy as np
import pytest

from manta_trading.data.base.session_index import NO_SESSION, Session, SessionIndex

_ONE_NS = np.int64(1)


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _ns(ts: datetime) -> np.int64:
    return np.int64(int(ts.timestamp()) * 1_000_000_000)


# Three CME-shaped sessions (17:00 → 16:00 CT, CST = UTC-6). 2024-12-25 is
# closed, so the 12-26 session opens on the closed day.
_SESSIONS = [
    Session(
        "CME_TEST", date(2024, 12, 23), _utc(2024, 12, 22, 23), _utc(2024, 12, 23, 22)
    ),
    Session(
        "CME_TEST",
        date(2024, 12, 24),
        _utc(2024, 12, 23, 23),
        _utc(2024, 12, 24, 18, 15),
    ),
    Session(
        "CME_TEST", date(2024, 12, 26), _utc(2024, 12, 25, 23), _utc(2024, 12, 26, 22)
    ),
]


@pytest.fixture
def index() -> SessionIndex:
    return SessionIndex(_SESSIONS)


class TestLocate:
    @pytest.mark.parametrize("session", _SESSIONS, ids=lambda s: str(s.session_date))
    def test_open_and_close_are_included(
        self, index: SessionIndex, session: Session
    ) -> None:
        assert index.locate(session.open_utc) == session
        assert index.locate(session.close_utc) == session

    @pytest.mark.parametrize("session", _SESSIONS, ids=lambda s: str(s.session_date))
    def test_one_ns_outside_is_no_session(
        self, index: SessionIndex, session: Session
    ) -> None:
        positions = index.locate_ns(
            np.array(
                [_ns(session.open_utc) - _ONE_NS, _ns(session.close_utc) + _ONE_NS]
            )
        )
        assert positions.tolist() == [NO_SESSION, NO_SESSION]

    def test_daily_break_is_none(self, index: SessionIndex) -> None:
        assert index.locate(_utc(2024, 12, 23, 22, 30)) is None

    def test_closed_day_is_none(self, index: SessionIndex) -> None:
        assert index.locate(_utc(2024, 12, 25, 12)) is None

    def test_before_first_and_after_last_is_none(self, index: SessionIndex) -> None:
        assert index.locate(_utc(2024, 12, 1)) is None
        assert index.locate(_utc(2025, 1, 1)) is None

    def test_non_utc_aware_input_is_converted(self, index: SessionIndex) -> None:
        ct = timezone(timedelta(hours=-6))
        assert index.locate(datetime(2024, 12, 24, 12, 0, tzinfo=ct)) == _SESSIONS[1]

    def test_naive_datetime_raises(self, index: SessionIndex) -> None:
        with pytest.raises(ValueError, match="naive"):
            index.locate(datetime(2024, 12, 23, 12))


class TestConstruction:
    def test_overlapping_raises(self) -> None:
        overlap = Session(
            "CME_TEST",
            date(2024, 12, 24),
            _utc(2024, 12, 23, 21),
            _utc(2024, 12, 24, 18),
        )
        with pytest.raises(ValueError, match="overlaps"):
            SessionIndex([_SESSIONS[0], overlap])

    def test_inverted_raises(self) -> None:
        inverted = Session(
            "CME_TEST",
            date(2024, 12, 23),
            _utc(2024, 12, 23, 22),
            _utc(2024, 12, 22, 23),
        )
        with pytest.raises(ValueError, match="inverted"):
            SessionIndex([inverted])

    def test_unsorted_raises(self) -> None:
        with pytest.raises(ValueError, match="not sorted"):
            SessionIndex([_SESSIONS[1], _SESSIONS[0]])

    def test_empty_index_finds_nothing(self) -> None:
        assert SessionIndex([]).locate(_utc(2024, 12, 23, 12)) is None


def test_locate_ns_agrees_with_locate(index: SessionIndex) -> None:
    """Property: over random instants, the vectorized and scalar forms agree.

    Instants are whole microseconds so the datetime path sees the exact same
    instant as the nanosecond path.
    """
    rng = np.random.default_rng(221)
    lo_us, hi_us = (
        _ns(_utc(2024, 12, 22, 12)) // 1_000,
        _ns(_utc(2024, 12, 27)) // 1_000,
    )
    micros = rng.integers(lo_us, hi_us, size=1_000, dtype=np.int64)
    positions = index.locate_ns(micros * 1_000)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    for us, pos in zip(micros.tolist(), positions.tolist(), strict=True):
        expected = index.locate(epoch + timedelta(microseconds=us))
        assert (None if pos == NO_SESSION else index.sessions[pos]) == expected
