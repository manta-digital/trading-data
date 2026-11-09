"""Minute classification for ``mt data tick parity`` (slice 306, D7)."""

from __future__ import annotations

from datetime import date

from manta_trading.data.tick.bar_parity import MinuteClass, classify_minutes

DAY = date(2024, 12, 3)
MINUTE = 60 * 10**9
CLOSE = 100 * MINUTE


def _row(bucket: int, o: int = 10, h: int = 12, lo: int = 9, c: int = 11, v: int = 5):
    """A ``BAR_COLUMNS`` row: time, session_date, raw_symbol, instrument_id, OHLCV."""
    return (bucket, DAY, "ESZ4", 1, o, h, lo, c, v)


def test_equal_minutes_agree() -> None:
    (record,) = classify_minutes([_row(0)], [_row(0)], CLOSE)

    assert record.cls is MinuteClass.AGREE and record.fields == ()


def test_a_differing_field_is_named() -> None:
    (record,) = classify_minutes([_row(0, v=5)], [_row(0, v=6)], CLOSE)

    assert (record.cls, record.fields) == (MinuteClass.DIFFER, ("volume",))


def test_two_differing_fields_are_both_named() -> None:
    (record,) = classify_minutes([_row(0, h=12, c=11)], [_row(0, h=13, c=10)], CLOSE)

    assert (record.cls, record.fields) == (MinuteClass.DIFFER, ("high", "close"))


def test_a_minute_on_one_side_only_is_provider_only_or_tick_only() -> None:
    records = classify_minutes([_row(MINUTE)], [_row(0)], CLOSE)

    assert [(r.bucket, r.cls) for r in records] == [
        (0, MinuteClass.PROVIDER_ONLY),
        (MINUTE, MinuteClass.TICK_ONLY),
    ]
    assert records[0].tick is None and records[0].provider is not None
    assert records[1].provider is None and records[1].tick is not None


def test_at_close_is_set_only_for_the_bucket_stamped_at_the_close() -> None:
    records = classify_minutes([], [_row(CLOSE - MINUTE), _row(CLOSE)], CLOSE)

    assert [r.at_close for r in records] == [False, True]


def test_two_empty_sides_have_no_minutes() -> None:
    assert classify_minutes([], [], CLOSE) == []
