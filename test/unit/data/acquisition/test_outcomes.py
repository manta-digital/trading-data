"""Unit tests for classify_outcome and outcome_to_fetch_status.

Covers every row of slice-145 Decision F table plus exhaustiveness
of the outcome→fetch_status mapping.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from manta_trading.data.acquisition.outcomes import (
    ProviderResponseError,
    classify_outcome,
    outcome_to_fetch_status,
)
from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.data.quality.fetch_status import FetchStatus

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 14, 30, tzinfo=UTC)


def _resp(
    status_code: int,
    body: Any = None,
    json_raises: bool = False,
) -> MagicMock:
    """Build a mock response object."""
    resp = MagicMock()
    resp.status_code = status_code
    if json_raises:
        resp.json.side_effect = ValueError("bad json")
    else:
        resp.json.return_value = body
    return resp


def _bar(date_str: str) -> dict:
    return {"date": date_str, "open": 100, "high": 101, "low": 99, "close": 100}


# ---------------------------------------------------------------------------
# classify_outcome
# ---------------------------------------------------------------------------


class TestClassifyOutcome:
    from_ts = _dt(2024, 1, 2)
    to_ts = _dt(2024, 12, 31)

    def _call(self, resp: MagicMock) -> LastAttemptOutcome:
        return classify_outcome(resp, self.from_ts, self.to_ts)

    # --- transient classes ---

    def test_http_5xx_is_transient(self) -> None:
        assert self._call(_resp(500)) == LastAttemptOutcome.TRANSIENT_FAILURE

    def test_http_503_is_transient(self) -> None:
        assert self._call(_resp(503)) == LastAttemptOutcome.TRANSIENT_FAILURE

    def test_http_429_is_transient(self) -> None:
        assert self._call(_resp(429)) == LastAttemptOutcome.TRANSIENT_FAILURE

    def test_json_decode_error_is_transient(self) -> None:
        assert self._call(_resp(200, json_raises=True)) == LastAttemptOutcome.TRANSIENT_FAILURE

    def test_200_with_error_dict_is_transient(self) -> None:
        """EODHD quirk: {"error": "..."} with 200 status → transient."""
        assert self._call(_resp(200, {"error": "Unknown symbol"})) == LastAttemptOutcome.TRANSIENT_FAILURE

    def test_200_non_list_body_is_transient(self) -> None:
        assert self._call(_resp(200, {"data": []})) == LastAttemptOutcome.TRANSIENT_FAILURE

    # --- 4xx other than 429 and 404 raises ---

    @pytest.mark.parametrize("status", [400, 401, 403, 422])
    def test_4xx_non_429_raises_provider_error(self, status: int) -> None:
        with pytest.raises(ProviderResponseError):
            self._call(_resp(status))

    def test_http_404_is_empty(self) -> None:
        """EODHD uses 404 to mean no intraday data for the symbol/range."""
        assert self._call(_resp(404)) == LastAttemptOutcome.EMPTY

    # --- body-shape classification ---

    def test_200_empty_list_is_empty(self) -> None:
        assert self._call(_resp(200, [])) == LastAttemptOutcome.EMPTY

    def test_200_partial_range_is_partial(self) -> None:
        # Latest bar is before range_end (2024-12-31)
        bars = [_bar("2024-06-15")]
        assert self._call(_resp(200, bars)) == LastAttemptOutcome.PARTIAL

    def test_200_full_range_is_success(self) -> None:
        # Latest bar is on or after range_end
        bars = [_bar("2024-01-02"), _bar("2024-12-31")]
        assert self._call(_resp(200, bars)) == LastAttemptOutcome.SUCCESS

    def test_200_latest_bar_exactly_on_range_end_is_success(self) -> None:
        bars = [_bar("2024-12-31")]
        assert self._call(_resp(200, bars)) == LastAttemptOutcome.SUCCESS

    def test_200_bars_without_date_field_is_partial(self) -> None:
        """Bars with no date/datetime field — can't determine coverage."""
        bars = [{"open": 100, "close": 100}]
        assert self._call(_resp(200, bars)) == LastAttemptOutcome.PARTIAL

    def test_1xx_status_is_transient(self) -> None:
        assert self._call(_resp(100)) == LastAttemptOutcome.TRANSIENT_FAILURE

    def test_3xx_status_is_transient(self) -> None:
        assert self._call(_resp(302)) == LastAttemptOutcome.TRANSIENT_FAILURE


# ---------------------------------------------------------------------------
# outcome_to_fetch_status — exhaustiveness
# ---------------------------------------------------------------------------


class TestOutcomeToFetchStatus:
    def test_success_maps_to_none(self) -> None:
        assert outcome_to_fetch_status(LastAttemptOutcome.SUCCESS) is None

    def test_partial_maps_to_unknown(self) -> None:
        assert outcome_to_fetch_status(LastAttemptOutcome.PARTIAL) == FetchStatus.UNKNOWN

    def test_empty_maps_to_provider_hole(self) -> None:
        assert outcome_to_fetch_status(LastAttemptOutcome.EMPTY) == FetchStatus.PROVIDER_HOLE

    def test_transient_failure_maps_to_failed_retryable(self) -> None:
        assert (
            outcome_to_fetch_status(LastAttemptOutcome.TRANSIENT_FAILURE)
            == FetchStatus.FAILED_RETRYABLE
        )

    def test_all_outcomes_covered(self) -> None:
        """Every LastAttemptOutcome value has a defined mapping."""
        for outcome in LastAttemptOutcome:
            # Should not raise KeyError
            _ = outcome_to_fetch_status(outcome)
