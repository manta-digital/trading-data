"""Tests for FetchStatus and LastAttemptOutcome StrEnums."""

from __future__ import annotations

from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.data.quality.fetch_status import FetchStatus


class TestFetchStatus:
    def test_all_values_exist(self) -> None:
        assert FetchStatus.UNKNOWN == "UNKNOWN"
        assert FetchStatus.PROVIDER_HOLE == "PROVIDER_HOLE"
        assert FetchStatus.FAILED_RETRYABLE == "FAILED_RETRYABLE"
        assert FetchStatus.RETRY_EXHAUSTED == "RETRY_EXHAUSTED"

    def test_str_enum_contract(self) -> None:
        for member in FetchStatus:
            assert isinstance(member, str)
            assert member == member.value

    def test_exactly_four_values(self) -> None:
        assert len(list(FetchStatus)) == 4


class TestLastAttemptOutcome:
    def test_all_values_exist(self) -> None:
        assert LastAttemptOutcome.SUCCESS == "success"
        assert LastAttemptOutcome.PARTIAL == "partial"
        assert LastAttemptOutcome.EMPTY == "empty"
        assert LastAttemptOutcome.TRANSIENT_FAILURE == "transient_failure"

    def test_str_enum_contract(self) -> None:
        for member in LastAttemptOutcome:
            assert isinstance(member, str)
            assert member == member.value

    def test_exactly_four_values(self) -> None:
        assert len(list(LastAttemptOutcome)) == 4
