"""Unit tests for ``mt data kalshi`` (slice 262 Task 8.3; slice 263 Task 4.2).

The core, the connection preflight, and the client are monkeypatched; the
exit-code mapping, option parsing, and summary output are the subject.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from psycopg import errors
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands import kalshi as cmd
from manta_trading.data.kalshi.client import KalshiClient
from manta_trading.data.kalshi.collection_pass import PassPhaseName, PhaseReport
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
        """The shared context's only exit-1 mapping (263 Task 1.3) — asserted
        here once for every command that opens it."""
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


# ---------------------------------------------------------------------------
# status (Task 8.6)
# ---------------------------------------------------------------------------

STATUS_CMD = ["data", "kalshi", "status"]


@contextlib.contextmanager
def _patched_status(settings: MagicMock, status: object) -> Iterator[None]:
    with (
        patch("manta_trading.cli.app.Settings", return_value=settings),
        patch("manta_trading.cli.app.setup_logging"),
        patch("manta_trading.cli.commands.kalshi.psycopg.connect"),
        patch(
            "manta_trading.data.kalshi.status.read_catalog_status", return_value=status
        ),
    ):
        yield


def _catalog_status():
    from datetime import timedelta

    from manta_trading.data.kalshi.constants import MarketStatus
    from manta_trading.data.kalshi.status import AwaitingStatus, CatalogStatus

    return CatalogStatus(
        last_full_sync_at=NOW,
        watermark_ts=NOW,
        series=3,
        events=5,
        markets_by_status={s: 0 for s in MarketStatus} | {MarketStatus.ACTIVE: 9},
        awaiting=AwaitingStatus(
            total=4,
            age_histogram=(1, 1, 1, 1),
            past_threshold=2,
            oldest_ticker="OLD",
            oldest_age=timedelta(days=40),
            checked_directly=1,
        ),
    )


class TestStatus:
    def test_never_synced_reports_and_exits_zero(self):
        with _patched_status(_settings(), None):
            result = runner.invoke(app, STATUS_CMD)
        assert result.exit_code == cmd.EXIT_OK
        assert cmd.NEVER_SYNCED in result.output

    def test_never_synced_json(self):
        with _patched_status(_settings(), None):
            result = runner.invoke(app, [*STATUS_CMD, "--json"])
        assert result.exit_code == cmd.EXIT_OK
        assert json.loads(result.stdout) == {"synced": False}

    def test_sections_rendered(self):
        with _patched_status(_settings(), _catalog_status()):
            result = runner.invoke(app, STATUS_CMD)
        assert result.exit_code == cmd.EXIT_OK
        for needle in (
            "Kalshi catalog",
            "Markets by status",
            "active 9",
            "Awaiting settlement",
            "past 7d threshold",
            "OLD (40 d)",
            "checked directly    1",
        ):
            assert needle in result.output, needle

    def test_json_is_flat_with_documented_keys(self):
        with _patched_status(_settings(), _catalog_status()):
            result = runner.invoke(app, [*STATUS_CMD, "--json"])
        payload = json.loads(result.stdout)
        assert payload["synced"] is True
        assert payload["awaiting_age"] == {"<1d": 1, "1d-7d": 1, "7d-30d": 1, ">30d": 1}
        assert payload["awaiting_past_threshold"] == 2
        assert payload["awaiting_oldest_ticker"] == "OLD"
        assert payload["markets_by_status"]["active"] == 9
        assert payload["stuck_threshold_days"] == 7

    def test_missing_db_url(self):
        with _patched_status(_settings(timescale_url=None), None):
            result = runner.invoke(app, STATUS_CMD)
        assert result.exit_code == cmd.EXIT_PREFLIGHT


# ---------------------------------------------------------------------------
# pass (slice 263, Task 4.2)
# ---------------------------------------------------------------------------

PASS_CMD = ["data", "kalshi", "pass"]


class _FakePhase:
    """A pass phase returning a scripted report (the CLI is the subject)."""

    outcome: SyncOutcome = SyncOutcome.OK
    name = PassPhaseName.CATALOG

    async def run(self, run: object) -> PhaseReport:
        summary = SyncResult(run_id=uuid4(), started_at=NOW)
        summary.phases[SyncPhase.MARKETS].fetched = 7
        return PhaseReport(
            name=self.name,
            outcome=type(self).outcome,
            summary=summary.to_dict(),
            duration_ms=12,
            error=None if type(self).outcome is SyncOutcome.OK else "boom",
        )


@contextlib.contextmanager
def _patched_pass(
    settings: MagicMock,
    *,
    outcome: SyncOutcome = SyncOutcome.OK,
    preflight: BaseException | None = None,
) -> Iterator[None]:
    _FakePhase.outcome = outcome
    conn = MagicMock()
    conn.close = AsyncMock()
    client = MagicMock()
    client.aclose = AsyncMock()
    client.mode = "public"
    client.rate_limit.requests_per_minute = 300
    with (
        patch("manta_trading.cli.app.Settings", return_value=settings),
        patch("manta_trading.cli.app.setup_logging"),
        patch(
            "manta_trading.data.kalshi.db.open_sync_connection",
            AsyncMock(return_value=conn, side_effect=preflight),
        ),
        patch.object(KalshiClient, "from_settings", return_value=client),
        patch("manta_trading.data.kalshi.collection_pass.PASS_PHASES", (_FakePhase(),)),
    ):
        yield


class TestPassHelp:
    def test_group_lists_pass(self):
        with _patched_pass(_settings()):
            result = runner.invoke(app, ["data", "kalshi", "--help"])
        assert result.exit_code == 0
        assert "pass" in result.output

    def test_only_two_options(self):
        with _patched_pass(_settings()):
            result = runner.invoke(app, [*PASS_CMD, "--help"])
        assert result.exit_code == 0
        assert "--events-file" in result.output and "--json" in result.output
        assert "--settled-since" not in result.output


class TestPassExitCodes:
    """Criterion 2: the pass uses the same constants ``sync`` does."""

    @pytest.mark.parametrize(
        ("outcome", "expected"),
        [
            (SyncOutcome.OK, cmd.EXIT_OK),
            (SyncOutcome.PROVIDER_ABORT, cmd.EXIT_PROVIDER),
            (SyncOutcome.PARTIAL, cmd.EXIT_SYNC_PARTIAL),
            (SyncOutcome.STORAGE_ABORT, cmd.EXIT_STORAGE),
        ],
    )
    def test_outcome_maps_to_exit_code(self, outcome: SyncOutcome, expected: int):
        with _patched_pass(_settings(), outcome=outcome):
            result = runner.invoke(app, PASS_CMD)
        assert result.exit_code == expected, result.output

    def test_missing_db_url(self):
        with _patched_pass(_settings(timescale_url=None)):
            result = runner.invoke(app, PASS_CMD)
        assert result.exit_code == cmd.EXIT_PREFLIGHT


class TestPassOutput:
    def test_rich_prints_a_row_per_phase_and_the_catalog_block(self):
        with _patched_pass(_settings()):
            result = runner.invoke(app, PASS_CMD)
        assert result.exit_code == cmd.EXIT_OK, result.output
        assert "Kalshi collection pass" in result.output
        assert "catalog" in result.output
        # the phase's own summary block, rendered by the shared helper
        assert "Kalshi catalog sync" in result.output
        assert "outcome" in result.output and "(exit 0)" in result.output

    def test_json_payload_shape(self):
        with _patched_pass(_settings()):
            result = runner.invoke(app, [*PASS_CMD, "--json"])
        payload = json.loads(result.stdout)
        assert set(payload) == {
            "run_id",
            "started_at",
            "phases",
            "outcome",
            "exit_code",
            "duration_ms",
        }
        assert payload["phases"][0]["name"] == "catalog"
        assert payload["phases"][0]["summary"]["phases"]["markets"]["fetched"] == 7
        assert payload["outcome"] == "ok" and payload["exit_code"] == 0

    def test_events_file_is_written(self, tmp_path):
        path = tmp_path / "pass.jsonl"
        with _patched_pass(_settings()):
            result = runner.invoke(app, [*PASS_CMD, "--events-file", str(path)])
        assert result.exit_code == cmd.EXIT_OK
        lines = path.read_text().splitlines()
        types = [json.loads(line)["event_type"] for line in lines]
        assert types == ["pass_started", "pass_finished"]
