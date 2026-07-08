"""Tests for migration helper functions in migrations/minute.py."""

from __future__ import annotations

from datetime import timedelta

import pytest

from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.market.schema.migrations.minute import (
    _fetch_status_check_sql,
    _interval_literal,
    _outcome_check_sql,
)


class TestFetchStatusCheckSql:
    def test_contains_all_fetch_status_values(self) -> None:
        sql = _fetch_status_check_sql()
        for member in FetchStatus:
            assert f"'{member.value}'" in sql

    def test_contains_no_extra_values(self) -> None:
        sql = _fetch_status_check_sql()
        expected = {f"'{m.value}'" for m in FetchStatus}
        # Strip the "fetch_status IN (" prefix and ")" suffix, split by ", "
        inner = sql.removeprefix("fetch_status IN (").removesuffix(")")
        actual = set(inner.split(", "))
        assert actual == expected

    def test_is_deterministic(self) -> None:
        assert _fetch_status_check_sql() == _fetch_status_check_sql()

    def test_values_are_sorted(self) -> None:
        sql = _fetch_status_check_sql()
        inner = sql.removeprefix("fetch_status IN (").removesuffix(")")
        values = [v.strip("'") for v in inner.split(", ")]
        assert values == sorted(values)


class TestOutcomeCheckSql:
    def test_contains_all_outcome_values(self) -> None:
        sql = _outcome_check_sql()
        for member in LastAttemptOutcome:
            assert f"'{member.value}'" in sql

    def test_contains_no_extra_values(self) -> None:
        sql = _outcome_check_sql()
        expected = {f"'{m.value}'" for m in LastAttemptOutcome}
        inner = sql.removeprefix("last_attempt_outcome IN (").removesuffix(")")
        actual = set(inner.split(", "))
        assert actual == expected

    def test_is_deterministic(self) -> None:
        assert _outcome_check_sql() == _outcome_check_sql()


class TestIntervalLiteral:
    def test_whole_minutes(self) -> None:
        assert _interval_literal(timedelta(minutes=30)) == "30 minutes"

    def test_whole_days(self) -> None:
        assert _interval_literal(timedelta(days=2)) == "2 days"

    def test_one_day(self) -> None:
        assert _interval_literal(timedelta(days=1)) == "1 days"

    def test_one_minute(self) -> None:
        assert _interval_literal(timedelta(minutes=1)) == "1 minutes"

    def test_unsupported_timedelta_raises(self) -> None:
        with pytest.raises(ValueError):
            _interval_literal(timedelta(seconds=90))
