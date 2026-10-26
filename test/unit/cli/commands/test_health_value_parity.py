"""The pure ``value parity`` verdict (slice 173)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from manta_trading.cli.commands.health_value_parity import (
    VALUE_PARITY_CHECK,
    BucketPair,
    ValueParitySample,
    check_value_parity,
)

_AT = datetime(2026, 9, 8, 14, 0, tzinfo=UTC)
_CLEAN = (Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100.5"), 50, 5)
_ALL_MARKED = (None, None, None, None, None, 5)


def _sample(*pairs: BucketPair) -> ValueParitySample:
    return ValueParitySample("2026-09-08", "SPY", 7, pairs)


def test_passes_when_every_bucket_matches() -> None:
    check = check_value_parity(
        _sample(
            BucketPair(_AT, _CLEAN, _CLEAN), BucketPair(_AT, _ALL_MARKED, _ALL_MARKED)
        )
    )
    assert check.name == VALUE_PARITY_CHECK
    assert check.ok
    assert "2026-09-08 SPY: 2 5m buckets" in check.detail


def test_fails_on_a_differing_bucket_naming_it_and_the_column() -> None:
    spiked = (*_CLEAN[:1], Decimal("150"), *_CLEAN[2:])
    check = check_value_parity(_sample(BucketPair(_AT, spiked, _CLEAN)))
    assert not check.ok
    assert _AT.isoformat() in check.detail
    assert "high (rollup 150, recomputed 101)" in check.detail
    assert "low" not in check.detail


def test_fails_on_a_differing_null_bucket() -> None:
    """An all-marked bucket is compared, not skipped: NULL vs a value differs."""
    check = check_value_parity(_sample(BucketPair(_AT, _CLEAN, _ALL_MARKED)))
    assert not check.ok
    assert "open (rollup 100, recomputed None)" in check.detail
    assert "minute_count" not in check.detail


def test_fails_on_a_minute_count_difference() -> None:
    check = check_value_parity(_sample(BucketPair(_AT, (*_CLEAN[:5], 4), _CLEAN)))
    assert not check.ok
    assert "minute_count (rollup 4, recomputed 5)" in check.detail


def test_fails_on_a_bucket_missing_from_either_side() -> None:
    missing = check_value_parity(_sample(BucketPair(_AT, None, _CLEAN)))
    extra = check_value_parity(_sample(BucketPair(_AT, _CLEAN, None)))
    assert not missing.ok and "missing from minute_5min_ohlcv" in missing.detail
    assert not extra.ok and "not in the 1m bars" in extra.detail


def test_names_the_first_differing_bucket() -> None:
    later = datetime(2026, 9, 8, 14, 5, tzinfo=UTC)
    check = check_value_parity(
        _sample(
            BucketPair(_AT, _CLEAN, _CLEAN),
            BucketPair(later, None, _CLEAN),
            BucketPair(datetime(2026, 9, 8, 14, 10, tzinfo=UTC), _CLEAN, None),
        )
    )
    assert later.isoformat() in check.detail
    assert "not in the 1m bars" not in check.detail


def test_fails_when_no_minute_is_marked() -> None:
    check = check_value_parity(ValueParitySample("2026-09-08", None, 0, ()))
    assert not check.ok
    assert check.detail == "2026-09-08: no marked minute, so nothing to compare"


def test_fails_with_no_session() -> None:
    check = check_value_parity(None)
    assert not check.ok
    assert check.detail == "no completed session to judge"
