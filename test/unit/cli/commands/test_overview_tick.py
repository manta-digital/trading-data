"""The ``futures ticks`` source line in ``mt data overview`` (slice 230, TD7, TD8).

Rendering and JSON of each state, ``gather`` adding the line on the CLI side
only, and the API's ``SourceRecord`` mapping never carrying it (FR13).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from manta_trading.api_server.models.operations import OverviewResponse
from manta_trading.cli.commands import overview as mod
from manta_trading.cli.overview_types import Overview, SourceFreshness
from manta_trading.cli.rendering.overview import overview_payload, render_overview
from manta_trading.data.tick.line_state import (
    TickLineState,
    TickUnavailable,
    unavailable_text,
)

NOW = datetime(2026, 10, 12, 14, 0, tzinfo=UTC)
MINUTE = SourceFreshness("minute bars", NOW - timedelta(hours=17))
STATES = [
    TickUnavailable(TickLineState.NOT_CONFIGURED, "MT_TICK_DB_URL unset"),
    TickUnavailable(TickLineState.UNREACHABLE, "connection refused"),
    TickUnavailable(TickLineState.MIGRATIONS_PENDING, "tick_009"),
]


def _overview(*sources: SourceFreshness) -> Overview:
    return Overview(
        now=NOW,
        passes=(),
        sources=sources,
        health_verdict=None,
        health_at=None,
        credits=None,
        credits_text="no key",
        universe=None,
        universe_at=None,
    )


@pytest.mark.parametrize("state", STATES, ids=lambda s: s.state.value)
def test_each_state_renders_td8_line(state: TickUnavailable) -> None:
    tick = SourceFreshness(mod.TICK_SOURCE_NAME, None, unavailable_text(state))

    text = render_overview(_overview(MINUTE, tick))

    assert f"futures ticks    {unavailable_text(state)}" in text.splitlines()


def test_a_reachable_line_renders_the_newest_tick() -> None:
    tick = SourceFreshness(
        mod.TICK_SOURCE_NAME, datetime(2025, 1, 1, 21, 59, tzinfo=UTC)
    )

    lines = render_overview(_overview(MINUTE, tick)).splitlines()

    assert any(
        line.startswith("futures ticks    2025-01-01 21:59 UTC") for line in lines
    )


def test_json_carries_unavailable_on_every_source() -> None:
    tick = SourceFreshness(mod.TICK_SOURCE_NAME, None, unavailable_text(STATES[0]))

    sources = overview_payload(_overview(MINUTE, tick))["sources"]

    assert sources[0]["unavailable"] is None
    assert list(sources[0]) == ["name", "newest", "unavailable"]
    assert sources[1] == {
        "name": "futures ticks",
        "newest": None,
        "unavailable": "not configured (MT_TICK_DB_URL unset)",
    }


def test_the_api_record_carries_the_tick_state() -> None:
    """231 TD7 closed 230's gap: ``SourceRecord`` carries ``unavailable``, so
    the API and the CLI say the same thing about an unread source."""
    tick = SourceFreshness(mod.TICK_SOURCE_NAME, None, "unreachable: x")

    body = json.loads(OverviewResponse.from_overview(_overview(tick)).model_dump_json())

    assert body["sources"] == [
        {"name": "futures ticks", "newest": None, "unavailable": "unreachable: x"}
    ]


def test_gather_appends_the_tick_line_and_gather_db_facts_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Repo:
        def __init__(self, _connect: Any) -> None:
            pass

        def open_runs(self, _kind: Any) -> list[Any]:
            return []

        def latest_ended(self, _kind: Any) -> None:
            return None

    class _Settings:
        minute_firing_days = (5,)
        eodhd_api_key = None

    tick = SourceFreshness(mod.TICK_SOURCE_NAME, None, "stub")
    monkeypatch.setattr(mod, "PassRunRepository", _Repo)
    monkeypatch.setattr(mod, "read_source_freshness", lambda _conn: [MINUTE])
    monkeypatch.setattr(mod, "read_tick_source", lambda _settings: tick)

    db_only = mod.gather_db_facts(object(), _Settings(), now=NOW, hostname="h")  # type: ignore[arg-type]
    full = mod.gather(object(), _Settings(), now=NOW, hostname="h")  # type: ignore[arg-type]

    assert db_only.sources == [MINUTE]
    assert full.sources == [MINUTE, tick]
