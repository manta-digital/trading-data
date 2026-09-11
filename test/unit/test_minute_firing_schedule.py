"""MT_MINUTE_FIRING_DAYS: parsing, the per-day decision, the next firing."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from manta_trading.config import Settings
from manta_trading.minute_firing_schedule import (
    describe_firing_days,
    is_firing_day,
    next_minute_firing_at,
    parse_firing_days,
)

T1305 = (time(13, 5),)


class TestParse:
    def test_daily_means_every_day(self) -> None:
        assert parse_firing_days("daily") is None
        assert parse_firing_days(" Daily ") is None

    def test_one_weekday(self) -> None:
        assert parse_firing_days("Sat") == (5,)
        assert parse_firing_days("sat") == (5,)

    def test_several_weekdays_sorted_and_deduplicated(self) -> None:
        assert parse_firing_days("Thu, Mon,thu") == (0, 3)

    def test_empty_is_an_error_not_a_guess(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            parse_firing_days("  ")

    def test_unknown_name_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="unknown day 'Weekend'"):
            parse_firing_days("Weekend")

    def test_describe_round_trips(self) -> None:
        assert describe_firing_days(None) == "daily"
        assert describe_firing_days((0, 3)) == "Mon,Thu"


class TestSettingsField:
    def test_the_env_spelling_is_parsed(self) -> None:
        assert Settings._parse_firing_days("Sat") == (5,)
        assert Settings._parse_firing_days("daily") is None

    def test_a_programmatic_value_passes_through(self) -> None:
        assert Settings._parse_firing_days((5,)) == (5,)


class TestIsFiringDay:
    SATURDAY = date(2026, 9, 12)
    TUESDAY = date(2026, 9, 8)

    def test_daily_fires_every_day(self) -> None:
        assert is_firing_day(self.TUESDAY, None)
        assert is_firing_day(self.SATURDAY, None)

    def test_weekly_fires_only_on_the_named_day(self) -> None:
        assert is_firing_day(self.SATURDAY, (5,))
        assert not is_firing_day(self.TUESDAY, (5,))


class TestNextFiring:
    def test_weekly_from_a_tuesday_close(self) -> None:
        close = datetime(2026, 9, 8, 20, 0, tzinfo=UTC)  # Tuesday
        assert next_minute_firing_at(close, weekdays=(5,), times=T1305) == datetime(
            2026, 9, 12, 13, 5, tzinfo=UTC
        )

    def test_weekly_from_a_saturday_before_the_firing(self) -> None:
        early = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
        assert next_minute_firing_at(early, weekdays=(5,), times=T1305) == datetime(
            2026, 9, 12, 13, 5, tzinfo=UTC
        )

    def test_weekly_from_a_saturday_after_the_firing_is_next_week(self) -> None:
        late = datetime(2026, 9, 12, 14, 0, tzinfo=UTC)
        assert next_minute_firing_at(late, weekdays=(5,), times=T1305) == datetime(
            2026, 9, 19, 13, 5, tzinfo=UTC
        )

    def test_daily_after_the_firing_is_tomorrow(self) -> None:
        close = datetime(2026, 9, 8, 20, 0, tzinfo=UTC)
        assert next_minute_firing_at(close, weekdays=None, times=T1305) == datetime(
            2026, 9, 9, 13, 5, tzinfo=UTC
        )

    def test_naive_datetime_is_refused(self) -> None:
        with pytest.raises(ValueError, match="aware"):
            next_minute_firing_at(datetime(2026, 9, 8, 20, 0), weekdays=None)

    def test_empty_weekdays_fail_loudly(self) -> None:
        with pytest.raises(RuntimeError, match="empty"):
            next_minute_firing_at(datetime(2026, 9, 8, tzinfo=UTC), weekdays=())
