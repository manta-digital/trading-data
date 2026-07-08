"""Unit tests for EODHDMinuteProvider (offline; uses captured fixture)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pandas as pd
import pytest

from manta_trading.data.historical_minute.provider import (
    RawDataResponse,
    ValidationResult,
)
from manta_trading.data.historical_minute.providers.eodhd import (
    EODHDMinuteProvider,
)

_UTC = timezone.utc
_FIXTURE_PATH = Path("test/fixtures/eodhd/aapl_2025-01-15_day.json")


@pytest.fixture(scope="module")
def fixture_bars() -> list[dict[str, object]]:
    """The captured EODHD intraday response (one trading day, AAPL)."""
    return json.loads(_FIXTURE_PATH.read_text())


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_empty_api_key_raises(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            EODHDMinuteProvider(api_key="")

    def test_defaults(self) -> None:
        p = EODHDMinuteProvider(api_key="dummy")
        assert p.max_days_per_request == 120
        rl = p.get_rate_limits()
        assert rl.requests_per_minute == 30
        assert rl.requests_per_day == 20_000
        assert rl.current_usage == 0


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------


class TestBuildUrl:
    def test_us_default_suffix_appended(self) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        url = p._build_url(
            "AAPL",
            datetime(2025, 1, 1, tzinfo=_UTC),
            datetime(2025, 1, 2, tzinfo=_UTC),
        )
        assert "/intraday/AAPL.US?" in url

    def test_explicit_us_suffix_not_doubled(self) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        url = p._build_url(
            "AAPL.US",
            datetime(2025, 1, 1, tzinfo=_UTC),
            datetime(2025, 1, 2, tzinfo=_UTC),
        )
        assert "/intraday/AAPL.US?" in url
        assert ".US.US" not in url

    def test_non_us_suffix_preserved(self) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        url = p._build_url(
            "BMW.XETRA",
            datetime(2025, 1, 1, tzinfo=_UTC),
            datetime(2025, 1, 2, tzinfo=_UTC),
        )
        assert "/intraday/BMW.XETRA?" in url

    def test_log_safe_url_redacts_key(self) -> None:
        p = EODHDMinuteProvider(api_key="SECRETSECRET")
        url = p._build_url(
            "AAPL",
            datetime(2025, 1, 1, tzinfo=_UTC),
            datetime(2025, 1, 2, tzinfo=_UTC),
        )
        safe = p._log_safe_url(url)
        assert "SECRETSECRET" not in safe
        assert "***" in safe


# ---------------------------------------------------------------------------
# validate_response
# ---------------------------------------------------------------------------


class TestValidateResponse:
    def test_valid_list(self, fixture_bars: list[dict[str, object]]) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        result = p.validate_response(fixture_bars)
        assert result.is_valid
        assert result.errors == []

    def test_empty_list_is_warning_not_error(self) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        result = p.validate_response([])
        assert result.is_valid
        assert result.errors == []
        assert result.warnings  # at least one warning

    def test_error_envelope(self) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        result = p.validate_response({"error": "Forbidden", "code": 403})
        assert not result.is_valid
        assert any("403" in e or "Forbidden" in e for e in result.errors)

    def test_422_envelope(self) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        body = {"errors": {"to": ["Max period length is 120 days"]}}
        result = p.validate_response(body)
        assert not result.is_valid
        assert any("validation envelope" in e for e in result.errors)

    def test_unexpected_top_level_type(self) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        result = p.validate_response("oops")
        assert not result.is_valid

    def test_missing_required_field(self) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        # First bar missing 'volume' field
        result = p.validate_response([
            {"timestamp": 1, "open": 1.0, "high": 2.0, "low": 0.5,
             "close": 1.5}
        ])
        assert not result.is_valid
        assert any("volume" in e for e in result.errors)


# ---------------------------------------------------------------------------
# convert_to_standard_format
# ---------------------------------------------------------------------------


class TestConvertToStandardFormat:
    def _wrap(self, raw: object) -> RawDataResponse:
        return RawDataResponse(
            symbol="AAPL",
            provider="eodhd",
            start_date=datetime(2025, 1, 15, tzinfo=_UTC),
            end_date=datetime(2025, 1, 15, 23, 59, tzinfo=_UTC),
            raw_data=raw,  # type: ignore[arg-type]  # protocol uses dict
            metadata={},
        )

    def test_against_fixture_canonical_schema(
        self, fixture_bars: list[dict[str, object]]
    ) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        df = p.convert_to_standard_format(self._wrap(fixture_bars))
        assert list(df.columns) == [
            "timestamp", "open", "high", "low", "close", "volume",
        ]
        assert len(df) == len(fixture_bars)

    def test_timestamps_utc_tz_aware(
        self, fixture_bars: list[dict[str, object]]
    ) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        df = p.convert_to_standard_format(self._wrap(fixture_bars))
        assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])
        ts0 = df["timestamp"].iloc[0]
        assert ts0.tzinfo is not None
        assert ts0.utcoffset() == timedelta(0)

    def test_sorted_ascending(
        self, fixture_bars: list[dict[str, object]]
    ) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        df = p.convert_to_standard_format(self._wrap(fixture_bars))
        assert df["timestamp"].is_monotonic_increasing

    def test_dedupe_last_wins(self) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        bars: list[dict[str, object]] = [
            {"timestamp": 1736899200, "open": 1.0, "high": 1.0, "low": 1.0,
             "close": 1.0, "volume": 1, "datetime": "x", "gmtoffset": 0},
            {"timestamp": 1736899200, "open": 9.0, "high": 9.0, "low": 9.0,
             "close": 9.0, "volume": 9, "datetime": "x", "gmtoffset": 0},
        ]
        df = p.convert_to_standard_format(self._wrap(bars))
        assert len(df) == 1
        assert df["close"].iloc[0] == 9.0

    def test_dtypes(self, fixture_bars: list[dict[str, object]]) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        df = p.convert_to_standard_format(self._wrap(fixture_bars))
        for col in ("open", "high", "low", "close"):
            assert df[col].dtype == float
        assert df["volume"].dtype.kind in ("i", "u")

    def test_empty_list_returns_canonical_empty(self) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        df = p.convert_to_standard_format(self._wrap([]))
        assert df.empty
        assert list(df.columns) == [
            "timestamp", "open", "high", "low", "close", "volume",
        ]

    def test_null_volume_coerced_to_zero(self) -> None:
        """EODHD emits null volume on indicative pre-market bars
        (no trades, just a quote-update / imbalance snapshot).
        Coerce to 0 so the int64 schema holds and downstream code
        can filter on volume > 0 to drop non-tradeable bars."""
        bars = [
            {
                "timestamp": 1_700_000_000,
                "gmtoffset": 0,
                "datetime": "2023-11-14 22:13:20",
                "open": 200.0,
                "high": 200.5,
                "low": 199.5,
                "close": 200.25,
                "volume": 1234,
            },
            {
                "timestamp": 1_700_000_060,
                "gmtoffset": 0,
                "datetime": "2023-11-14 22:14:20",
                "open": 200.0212,
                "high": 200.0295,
                "low": 200.0212,
                "close": 200.0295,
                "volume": None,
            },
        ]
        p = EODHDMinuteProvider(api_key="KEY")
        df = p.convert_to_standard_format(self._wrap(bars))
        assert len(df) == 2
        assert df["volume"].dtype.kind in ("i", "u")
        # The null-volume bar is preserved with volume = 0; the
        # tradeable bar keeps its real volume.
        volumes = df["volume"].tolist()
        assert 0 in volumes
        assert 1234 in volumes


# ---------------------------------------------------------------------------
# fetch_minute_data — mocked HTTP
# ---------------------------------------------------------------------------


def _make_mock_response(
    *, status_code: int, json_body: object
) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = json_body
    response.text = json.dumps(json_body)
    return response


class TestFetchMinuteData:
    @pytest.mark.asyncio
    async def test_empty_symbol_raises(self) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        with pytest.raises(ValueError, match="symbol"):
            await p.fetch_minute_data(
                "",
                datetime(2025, 1, 1, tzinfo=_UTC),
                datetime(2025, 1, 2, tzinfo=_UTC),
            )

    @pytest.mark.asyncio
    async def test_oversize_window_raises(self) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        start = datetime(2025, 1, 1, tzinfo=_UTC)
        end = start + timedelta(days=121)
        with pytest.raises(ValueError, match="max_days_per_request"):
            await p.fetch_minute_data("AAPL", start, end)

    @pytest.mark.asyncio
    async def test_success_increments_usage(
        self, fixture_bars: list[dict[str, object]]
    ) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        client = MagicMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(
            return_value=_make_mock_response(
                status_code=200, json_body=fixture_bars
            )
        )
        p._client = client  # type: ignore[assignment]

        before = p.get_rate_limits().current_usage
        resp = await p.fetch_minute_data(
            "AAPL",
            datetime(2025, 1, 15, tzinfo=_UTC),
            datetime(2025, 1, 15, 23, 59, tzinfo=_UTC),
        )
        after = p.get_rate_limits().current_usage

        assert after == before + 1
        assert resp.provider == "eodhd"
        assert resp.symbol == "AAPL"
        assert resp.metadata["bar_count"] == len(fixture_bars)

    @pytest.mark.asyncio
    async def test_non_200_raises_runtime(self) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        client = MagicMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(
            return_value=_make_mock_response(
                status_code=403, json_body={"error": "Forbidden"}
            )
        )
        p._client = client  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="HTTP 403"):
            await p.fetch_minute_data(
                "AAPL",
                datetime(2025, 1, 15, tzinfo=_UTC),
                datetime(2025, 1, 15, 23, 59, tzinfo=_UTC),
            )

    @pytest.mark.asyncio
    async def test_422_logged_as_programmer_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        client = MagicMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(
            return_value=_make_mock_response(
                status_code=422,
                json_body={"errors": {"to": ["Max period length is 120 days"]}},
            )
        )
        p._client = client  # type: ignore[assignment]

        with caplog.at_level("ERROR"):
            with pytest.raises(RuntimeError, match="HTTP 422"):
                await p.fetch_minute_data(
                    "AAPL",
                    datetime(2025, 1, 15, tzinfo=_UTC),
                    datetime(2025, 1, 15, 23, 59, tzinfo=_UTC),
                )
        assert any("programmer error" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


class TestClose:
    @pytest.mark.asyncio
    async def test_close_idempotent_when_uninitialised(self) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        await p.close()
        await p.close()  # second call is a no-op

    @pytest.mark.asyncio
    async def test_close_aclose_called_once(self) -> None:
        p = EODHDMinuteProvider(api_key="KEY")
        client = MagicMock(spec=httpx.AsyncClient)
        client.aclose = AsyncMock()
        p._client = client  # type: ignore[assignment]

        await p.close()
        client.aclose.assert_awaited_once()
        assert p._client is None
