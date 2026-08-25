"""Unit tests for ``mt data kalshi sync`` (slice 262, Task 8.3).

The core, the connection preflight, and the client are monkeypatched; the
exit-code mapping, option parsing, and summary output are the subject.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from psycopg import errors
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands import kalshi as cmd
from manta_trading.data.kalshi.client import KalshiClient
from manta_trading.data.kalshi.db import PreflightError
from manta_trading.data.kalshi.sync_types import SyncOutcome, SyncPhase, SyncResult
from manta_trading.providers.errors import ProviderTransientError

runner = CliRunner()
CMD = ["data", "kalshi", "sync"]
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def _settings(*, timescale_url: str | None = "postgresql://ts/db") -> MagicMock:
    s = MagicMock()
    s.timescale_db_url = timescale_url
    s.kalshi_api_key_id = None
    s.kalshi_private_key_path = None
    s.kalshi_requests_per_minute = None
    return s


class _FakeSync:
    """Stands in for ``CatalogSync``: records the call, returns or raises."""

    raise_with: BaseException | None = None
    item_error = False
    calls: list[dict[str, object]] = []

    def __init__(self, source: object, repository: object, sink: object) -> None:
        from uuid import uuid4

        self.result = SyncResult(run_id=uuid4(), started_at=NOW)
        if self.item_error:
            from manta_trading.data.kalshi.sync_types import ItemError

            self.result.item_errors.append(ItemError("X", SyncPhase.MARKETS, "why"))
        self.result.phases[SyncPhase.MARKETS].fetched = 7
        self._sink = sink

    async def run(self, settled_since: datetime | None = None) -> SyncResult:
        type(self).calls.append({"settled_since": settled_since, "sink": self._sink})
        if self.raise_with is not None:
            raise self.raise_with
        return self.result


@contextlib.contextmanager
def _patched(
    settings: MagicMock,
    *,
    raise_with: BaseException | None = None,
    item_error: bool = False,
    preflight: BaseException | None = None,
) -> Iterator[type[_FakeSync]]:
    _FakeSync.raise_with = raise_with
    _FakeSync.item_error = item_error
    _FakeSync.calls = []
    conn = MagicMock()
    conn.close = AsyncMock()
    open_conn = AsyncMock(return_value=conn, side_effect=preflight)
    client = MagicMock()
    client.aclose = AsyncMock()
    with (
        patch("manta_trading.cli.app.Settings", return_value=settings),
        patch("manta_trading.cli.app.setup_logging"),
        patch("manta_trading.data.kalshi.db.open_sync_connection", open_conn),
        patch.object(KalshiClient, "from_settings", return_value=client),
        patch("manta_trading.data.kalshi.sync.CatalogSync", _FakeSync),
    ):
        yield _FakeSync


class TestHelp:
    def test_group_lists_sync(self):
        with _patched(_settings()):
            result = runner.invoke(app, ["data", "kalshi", "--help"])
        assert result.exit_code == 0
        assert "sync" in result.output


class TestExitCodes:
    def test_ok(self):
        with _patched(_settings()):
            result = runner.invoke(app, CMD)
        assert result.exit_code == cmd.EXIT_OK, result.output
        assert "Kalshi catalog sync" in result.output

    def test_partial(self):
        with _patched(_settings(), item_error=True):
            result = runner.invoke(app, CMD)
        assert result.exit_code == cmd.EXIT_SYNC_PARTIAL

    def test_provider_abort(self):
        with _patched(_settings(), raise_with=ProviderTransientError("503")):
            result = runner.invoke(app, CMD)
        assert result.exit_code == cmd.EXIT_PROVIDER
        assert "provider abort" in result.output

    def test_storage_abort(self):
        with _patched(_settings(), raise_with=errors.OperationalError("gone")):
            result = runner.invoke(app, CMD)
        assert result.exit_code == cmd.EXIT_STORAGE
        assert "storage abort" in result.output

    def test_preflight_lock(self):
        with _patched(
            _settings(), preflight=PreflightError("another sync holds the lock")
        ):
            result = runner.invoke(app, CMD)
        assert result.exit_code == cmd.EXIT_PREFLIGHT
        assert "holds the lock" in result.output

    def test_missing_db_url(self):
        with _patched(_settings(timescale_url=None)):
            result = runner.invoke(app, CMD)
        assert result.exit_code == cmd.EXIT_PREFLIGHT

    def test_mapping_covers_every_outcome(self):
        assert set(cmd.EXIT_BY_OUTCOME) == set(SyncOutcome)
        assert sorted(cmd.EXIT_BY_OUTCOME.values()) == [0, 2, 3, 4]


class TestOptions:
    def test_settled_since_naive_rejected(self):
        with _patched(_settings()) as fake:
            result = runner.invoke(
                app, [*CMD, "--settled-since", "2026-08-24T00:00:00"]
            )
        assert result.exit_code == cmd.EXIT_PREFLIGHT
        assert "offset" in result.output
        assert fake.calls == []

    def test_settled_since_aware_reaches_core(self):
        with _patched(_settings()) as fake:
            result = runner.invoke(
                app, [*CMD, "--settled-since", "2026-08-24T00:00:00Z"]
            )
        assert result.exit_code == cmd.EXIT_OK
        assert fake.calls[0]["settled_since"] == datetime(2026, 8, 24, tzinfo=UTC)

    def test_events_file_selects_jsonl_sink(self, tmp_path):
        from manta_trading.data.kalshi.events import JsonlSyncEventSink

        with _patched(_settings()) as fake:
            result = runner.invoke(
                app, [*CMD, "--events-file", str(tmp_path / "e.jsonl")]
            )
        assert result.exit_code == cmd.EXIT_OK
        assert isinstance(fake.calls[0]["sink"], JsonlSyncEventSink)

    def test_json_output(self):
        with _patched(_settings()):
            result = runner.invoke(app, [*CMD, "--json"])
        assert result.exit_code == cmd.EXIT_OK
        payload = json.loads(result.stdout)
        assert payload["phases"]["markets"]["fetched"] == 7
        assert payload["outcome"] == "ok" and payload["exit_code"] == 0


class TestBudgetOverride:
    def test_setting_reaches_client(self):
        settings = _settings()
        settings.kalshi_requests_per_minute = 120
        assert (
            KalshiClient.from_settings(settings).rate_limit.requests_per_minute == 120
        )

    def test_unset_keeps_mode_default(self):
        from manta_trading.data.kalshi.constants import KALSHI_PUBLIC_RATE_LIMIT

        assert (
            KalshiClient.from_settings(_settings()).rate_limit
            == KALSHI_PUBLIC_RATE_LIMIT
        )


@pytest.mark.parametrize("value", ["2026-08-24T00:00:00Z", "2026-08-24T02:00:00+02:00"])
def test_parse_settled_since_normalizes_to_utc(value: str):
    assert cmd.parse_settled_since(value) == datetime(2026, 8, 24, tzinfo=UTC)
