"""MT_MINUTE_FIRING_DAYS: parsing, the per-day decision, the next firing."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from manta_trading.config import Settings
from manta_trading.constants import (
    ACCOUNTING_PASS_FIRING_TIMES_UTC,
    DAILY_PASS_FIRING_TIMES_UTC,
    HEALTH_FIRING_MINUTE,
    KALSHI_PASS_FIRING_MINUTE,
    MINUTE_PASS_FIRING_TIMES_UTC,
)
from manta_trading.data.acquisition.pass_runs import PassKind
from manta_trading.firing_schedule import (
    FiringSchedule,
    describe_firing_days,
    is_firing_day,
    next_firing_at,
    next_minute_firing_at,
    parse_firing_days,
    schedule_for,
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


# ---------------------------------------------------------------------------
# Generalised schedules (slice 922)
# ---------------------------------------------------------------------------


class TestFiringScheduleHourly:
    def test_it_builds_one_firing_an_hour(self) -> None:
        schedule = FiringSchedule.hourly(20)
        assert len(schedule.times_utc) == 24
        assert {t.minute for t in schedule.times_utc} == {20}
        assert {t.hour for t in schedule.times_utc} == set(range(24))

    def test_it_runs_every_day(self) -> None:
        assert FiringSchedule.hourly(20).weekdays is None

    def test_it_describes_itself_compactly(self) -> None:
        assert FiringSchedule.hourly(20).describe() == "hourly :20"
        assert FiringSchedule.hourly(5).describe() == "hourly :05"


class TestDescribe:
    def test_a_twice_daily_schedule_lists_its_times_in_order(self) -> None:
        schedule = FiringSchedule((time(12, 35), time(0, 35)))
        assert schedule.describe() == "00:35, 12:35"

    def test_a_weekday_restricted_schedule_names_the_days(self) -> None:
        schedule = FiringSchedule((time(13, 5),), (5,))
        assert schedule.describe() == "13:05 on Sat"

    def test_a_single_daily_time_is_just_the_time(self) -> None:
        assert FiringSchedule((time(16, 30),)).describe() == "16:30"

    def test_hourly_on_some_weekdays_is_not_called_hourly(self) -> None:
        """The compact spelling would hide the weekday restriction."""
        schedule = FiringSchedule(tuple(time(hour, 20) for hour in range(24)), (5,))
        assert schedule.describe() != "hourly :20"
        assert "Sat" in schedule.describe()


class TestNextFiringAt:
    def test_it_returns_a_firing_later_the_same_day(self) -> None:
        schedule = FiringSchedule((time(0, 35), time(12, 35)))
        after = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
        assert next_firing_at(after, schedule) == datetime(
            2026, 9, 12, 12, 35, tzinfo=UTC
        )

    def test_it_crosses_midnight_when_the_day_is_spent(self) -> None:
        schedule = FiringSchedule((time(0, 35), time(12, 35)))
        after = datetime(2026, 9, 12, 23, 59, tzinfo=UTC)
        assert next_firing_at(after, schedule) == datetime(
            2026, 9, 13, 0, 35, tzinfo=UTC
        )

    def test_a_firing_exactly_now_counts_as_next(self) -> None:
        schedule = FiringSchedule((time(12, 35),))
        now = datetime(2026, 9, 12, 12, 35, tzinfo=UTC)
        assert next_firing_at(now, schedule) == now

    def test_an_hourly_schedule_finds_the_next_hour(self) -> None:
        schedule = FiringSchedule.hourly(20)
        after = datetime(2026, 9, 12, 13, 21, tzinfo=UTC)
        assert next_firing_at(after, schedule) == datetime(
            2026, 9, 12, 14, 20, tzinfo=UTC
        )

    def test_an_hourly_schedule_crosses_midnight(self) -> None:
        schedule = FiringSchedule.hourly(20)
        after = datetime(2026, 9, 12, 23, 30, tzinfo=UTC)
        assert next_firing_at(after, schedule) == datetime(
            2026, 9, 13, 0, 20, tzinfo=UTC
        )

    def test_it_skips_a_weekday_gap(self) -> None:
        """Saturday only: from Wednesday the answer is three days out."""
        schedule = FiringSchedule((time(13, 5),), (5,))
        wednesday = datetime(2026, 9, 9, 14, 0, tzinfo=UTC)
        assert next_firing_at(wednesday, schedule) == datetime(
            2026, 9, 12, 13, 5, tzinfo=UTC
        )

    def test_it_wraps_a_whole_week(self) -> None:
        """Just after Saturday's firing, the next is the following Saturday."""
        schedule = FiringSchedule((time(13, 5),), (5,))
        after = datetime(2026, 9, 12, 13, 6, tzinfo=UTC)
        assert next_firing_at(after, schedule) == datetime(
            2026, 9, 19, 13, 5, tzinfo=UTC
        )

    def test_a_naive_datetime_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="aware"):
            next_firing_at(datetime(2026, 9, 12, 13, 0), FiringSchedule((time(13, 5),)))

    def test_an_empty_time_list_raises_rather_than_guessing(self) -> None:
        with pytest.raises(RuntimeError, match="empty"):
            next_firing_at(datetime(2026, 9, 12, tzinfo=UTC), FiringSchedule(()))

    def test_an_empty_weekday_list_raises(self) -> None:
        with pytest.raises(RuntimeError, match="empty"):
            next_firing_at(
                datetime(2026, 9, 12, tzinfo=UTC),
                FiringSchedule((time(13, 5),), ()),
            )


class TestScheduleFor:
    """One place assembles each kind's cadence, from the shipped constants."""

    @pytest.mark.parametrize("kind", list(PassKind))
    def test_every_pass_kind_has_a_schedule(self, kind: PassKind) -> None:
        schedule = schedule_for(kind)
        assert schedule.times_utc, f"{kind} has no firing times"

    def test_minute_observes_the_operator_setting(self) -> None:
        assert schedule_for(PassKind.MINUTE, (5,)).weekdays == (5,)
        assert schedule_for(PassKind.MINUTE, None).weekdays is None

    def test_no_other_kind_observes_it(self) -> None:
        for kind in PassKind:
            if kind is PassKind.MINUTE:
                continue
            assert schedule_for(kind, (5,)).weekdays is None, kind

    def test_the_schedules_come_from_the_constants(self) -> None:
        assert schedule_for(PassKind.MINUTE).times_utc == MINUTE_PASS_FIRING_TIMES_UTC
        assert schedule_for(PassKind.DAILY).times_utc == DAILY_PASS_FIRING_TIMES_UTC
        assert (
            schedule_for(PassKind.ACCOUNTING).times_utc
            == ACCOUNTING_PASS_FIRING_TIMES_UTC
        )
        assert schedule_for(PassKind.KALSHI).describe() == (
            f"hourly :{KALSHI_PASS_FIRING_MINUTE:02d}"
        )
        assert schedule_for(PassKind.HEALTH).describe() == (
            f"hourly :{HEALTH_FIRING_MINUTE:02d}"
        )

    def test_next_firing_resolves_for_every_kind(self) -> None:
        now = datetime(2026, 9, 12, 13, 10, tzinfo=UTC)
        for kind in PassKind:
            assert next_firing_at(now, schedule_for(kind)) >= now


class TestMinuteScheduleStillAgrees:
    """The generalised path must answer exactly as the minute-specific one."""

    @pytest.mark.parametrize(
        "weekdays", [None, (5,), (0, 3)], ids=["daily", "sat", "mon-thu"]
    )
    def test_both_functions_agree(self, weekdays: tuple[int, ...] | None) -> None:
        after = datetime(2026, 9, 9, 14, 0, tzinfo=UTC)
        assert next_firing_at(
            after, schedule_for(PassKind.MINUTE, weekdays)
        ) == next_minute_firing_at(after, weekdays=weekdays)
