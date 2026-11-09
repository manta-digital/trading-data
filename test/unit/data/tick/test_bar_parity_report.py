"""The parity session report: counts, close-stamped minutes, listing, verdict."""

from __future__ import annotations

from datetime import date

from manta_trading.data.tick.bar_parity import MinuteClass, MinuteRecord
from manta_trading.data.tick.bar_parity_report import (
    SkippedSession,
    build_report,
    session_report,
)
from manta_trading.data.tick.tick_status import TickSessionStatus

DAY = date(2024, 12, 3)
BAR = (10, 12, 9, 11, 5)


def _minute(
    bucket: int,
    cls: MinuteClass,
    *,
    tick: tuple[int, ...] | None = BAR,
    provider: tuple[int, ...] | None = BAR,
    at_close: bool = False,
) -> MinuteRecord:
    return MinuteRecord(bucket, cls, (), tick, provider, at_close)


MINUTES = [
    _minute(0, MinuteClass.AGREE),
    _minute(1, MinuteClass.DIFFER, provider=(10, 12, 9, 11, 6)),
    _minute(2, MinuteClass.PROVIDER_ONLY, tick=None),
    _minute(3, MinuteClass.TICK_ONLY, provider=None),
    _minute(4, MinuteClass.AGREE, at_close=True),
]


def test_counts_cover_every_class_with_zeros() -> None:
    report = session_report(DAY, MINUTES[:1])

    assert {c.value: n for c, n in report.counts.items()} == {
        "agree": 1,
        "differ": 0,
        "provider_only": 0,
        "tick_only": 0,
    }


def test_counts_volumes_and_close_stamped_minutes() -> None:
    report = session_report(DAY, MINUTES)

    assert report.counts[MinuteClass.AGREE] == 2
    assert (report.close_stamped, report.close_stamped_agree) == (1, 1)
    assert (report.tick_volume, report.provider_volume) == (
        5 + 5 + 5 + 5,
        5 + 6 + 5 + 5,
    )


def test_the_listing_is_every_non_agreeing_minute_plus_every_close_stamped_one() -> (
    None
):
    report = session_report(DAY, MINUTES)

    assert [m.bucket for m in report.minutes_listed] == [1, 2, 3, 4]


def test_the_verdict_is_all_agree_only_when_every_minute_agrees() -> None:
    clean = build_report([session_report(DAY, MINUTES[:1] + MINUTES[4:])], [])
    dirty = build_report([session_report(DAY, MINUTES)], [])

    assert clean.all_agree and not dirty.all_agree


def test_skipped_sessions_do_not_change_the_verdict() -> None:
    skipped = SkippedSession(DAY, TickSessionStatus.COMPLETE, TickSessionStatus.MISSING)

    assert build_report([], [skipped]).all_agree
