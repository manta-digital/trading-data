"""Unit tests for migrate_cold_start.run_preflight (slice 142)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from manta_trading.data.quality.migrate_cold_start import (
    INSTRUMENTS_MAX_COUNT,
    INSTRUMENTS_MIN_COUNT,
    PreflightFailed,
    run_preflight,
)


# ---------------------------------------------------------------------------
# DB-mock helpers
# ---------------------------------------------------------------------------


def _make_conn(
    *,
    applied_141: list[str] | None = None,
    instruments_count: int = 32_875,
    eodhd_type_null: int = 0,
    has_active_col: bool = False,
) -> MagicMock:
    """Build a psycopg-style connection mock returning canned values."""

    conn = MagicMock()

    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)

    applied = applied_141 if applied_141 is not None else [
        "015_instruments_lifecycle_columns",
        "016_instruments_eodhd_type_not_null",
        "017_instruments_drop_active",
    ]

    def _execute(sql: str, *args: Any) -> None:
        s = sql.strip().lower()
        if "schema_migrations" in s:
            cursor.fetchall.return_value = [
                {"migration_id": m} for m in applied
            ]
        elif "eodhd_type is null" in s:
            cursor.fetchone.return_value = (eodhd_type_null,)
        elif "from instruments" in s and "count" in s:
            cursor.fetchone.return_value = (instruments_count,)
        elif "information_schema.columns" in s:
            cursor.fetchone.return_value = (has_active_col,)
        else:
            cursor.fetchone.return_value = (0,)
            cursor.fetchall.return_value = []

    cursor.execute.side_effect = _execute
    conn.cursor.return_value = cursor
    return conn


# ---------------------------------------------------------------------------
# Tests — DB rules
# ---------------------------------------------------------------------------


class TestPreflightDBRules:
    def test_passes_with_clean_141_state(self) -> None:
        conn = _make_conn()
        result = run_preflight(conn, skip_probe=True)
        assert result.instruments_count == 32_875
        assert result.probe_skipped is True

    def test_missing_016_migration_halts(self) -> None:
        conn = _make_conn(applied_141=[
            "015_instruments_lifecycle_columns",
            "017_instruments_drop_active",
        ])
        with pytest.raises(PreflightFailed, match="Slice 141"):
            run_preflight(conn, skip_probe=True)

    def test_eodhd_type_null_halts(self) -> None:
        conn = _make_conn(eodhd_type_null=1)
        with pytest.raises(PreflightFailed, match="eodhd_type IS NULL"):
            run_preflight(conn, skip_probe=True)

    def test_count_below_minimum_halts(self) -> None:
        conn = _make_conn(instruments_count=1_000)
        with pytest.raises(PreflightFailed, match="below the minimum"):
            run_preflight(conn, skip_probe=True)

    def test_count_above_maximum_halts(self) -> None:
        conn = _make_conn(instruments_count=90_000)
        with pytest.raises(PreflightFailed, match="sanity ceiling"):
            run_preflight(conn, skip_probe=True)

    def test_count_at_minimum_passes(self) -> None:
        conn = _make_conn(instruments_count=INSTRUMENTS_MIN_COUNT)
        result = run_preflight(conn, skip_probe=True)
        assert result.instruments_count == INSTRUMENTS_MIN_COUNT

    def test_count_at_maximum_passes(self) -> None:
        conn = _make_conn(instruments_count=INSTRUMENTS_MAX_COUNT)
        result = run_preflight(conn, skip_probe=True)
        assert result.instruments_count == INSTRUMENTS_MAX_COUNT

    def test_active_column_still_present_halts(self) -> None:
        conn = _make_conn(has_active_col=True)
        with pytest.raises(PreflightFailed, match="active column still exists"):
            run_preflight(conn, skip_probe=True)


# ---------------------------------------------------------------------------
# Tests — EODHD probe (mocked httpx)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_body: Any = None,
        json_raises: bool = False,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json = json_body
        self._raises = json_raises
        self.text = text

    def json(self) -> Any:
        if self._raises:
            raise ValueError("malformed JSON")
        return self._json


class _FakeClient:
    def __init__(self, *, response: _FakeResponse | None = None,
                 raises: Exception | None = None) -> None:
        self._response = response
        self._raises = raises

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def get(self, *_a, **_kw) -> _FakeResponse:
        if self._raises is not None:
            raise self._raises
        assert self._response is not None
        return self._response


@contextmanager
def _patched_httpx(monkeypatch, fake_client: _FakeClient):
    monkeypatch.setattr(
        httpx, "Client", lambda *a, **kw: fake_client
    )
    yield


class TestPreflightProbe:
    def test_skip_probe_does_not_call_httpx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """skip_probe=True must not touch httpx at all."""
        called: list[bool] = []
        original_client = httpx.Client

        def _spy(*a, **kw):
            called.append(True)
            return original_client(*a, **kw)

        monkeypatch.setattr(httpx, "Client", _spy)
        conn = _make_conn()
        run_preflight(conn, skip_probe=True)
        assert called == []

    def test_missing_api_key_halts(self) -> None:
        conn = _make_conn()
        with pytest.raises(PreflightFailed, match="EODHD probe required"):
            run_preflight(conn, skip_probe=False, eodhd_api_key=None)

    def test_empty_api_key_halts(self) -> None:
        conn = _make_conn()
        with pytest.raises(PreflightFailed, match="not supplied"):
            run_preflight(conn, skip_probe=False, eodhd_api_key="")

    def test_timeout_halts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient(raises=httpx.TimeoutException("slow"))
        with _patched_httpx(monkeypatch, client):
            conn = _make_conn()
            with pytest.raises(PreflightFailed, match="timeout"):
                run_preflight(conn, skip_probe=False, eodhd_api_key="key")

    def test_http_401_halts_with_credential_guidance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient(response=_FakeResponse(status_code=401))
        with _patched_httpx(monkeypatch, client):
            conn = _make_conn()
            with pytest.raises(
                PreflightFailed, match="authentication rejected"
            ):
                run_preflight(conn, skip_probe=False, eodhd_api_key="key")

    def test_http_400_other_halts_with_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient(
            response=_FakeResponse(
                status_code=404, text="symbol not found"
            )
        )
        with _patched_httpx(monkeypatch, client):
            conn = _make_conn()
            with pytest.raises(PreflightFailed, match="HTTP 404"):
                run_preflight(conn, skip_probe=False, eodhd_api_key="key")

    def test_http_500_halts_with_retry_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient(response=_FakeResponse(status_code=503))
        with _patched_httpx(monkeypatch, client):
            conn = _make_conn()
            with pytest.raises(PreflightFailed, match="server error"):
                run_preflight(conn, skip_probe=False, eodhd_api_key="key")

    def test_empty_body_halts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient(
            response=_FakeResponse(status_code=200, json_body=[])
        )
        with _patched_httpx(monkeypatch, client):
            conn = _make_conn()
            with pytest.raises(PreflightFailed, match="empty"):
                run_preflight(conn, skip_probe=False, eodhd_api_key="key")

    def test_malformed_json_halts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient(
            response=_FakeResponse(status_code=200, json_raises=True)
        )
        with _patched_httpx(monkeypatch, client):
            conn = _make_conn()
            with pytest.raises(PreflightFailed, match="malformed JSON"):
                run_preflight(conn, skip_probe=False, eodhd_api_key="key")

    def test_valid_response_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient(
            response=_FakeResponse(
                status_code=200, json_body=[{"date": "2026-01-01", "close": 1}]
            )
        )
        with _patched_httpx(monkeypatch, client):
            conn = _make_conn()
            result = run_preflight(
                conn, skip_probe=False, eodhd_api_key="key"
            )
            assert result.probe_skipped is False
