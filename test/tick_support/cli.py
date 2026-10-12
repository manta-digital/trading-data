"""Run the ``mt`` app in-process against throwaway databases (slice 230 tests).

``cli_settings`` patches the app's ``Settings`` with one built from the given
URLs only (``_env_file=None``: no developer ``.env`` leaks in), and skips the
logging setup, whose handlers would bind CliRunner's stream, closed after
each invoke, and break a later module.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from manta_trading.config import Settings
from manta_trading.data.tick.constants import TICK_ENV_PREFIX


@contextmanager
def cli_settings(tick_url: str | None, calendar_url: str) -> Iterator[Settings]:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        tick_db_url=tick_url,
        timescale_db_url=calendar_url,
    )
    with (
        patch("manta_trading.cli.app.Settings", side_effect=lambda: settings),
        patch("manta_trading.cli.app.setup_logging"),
    ):
        yield settings


def clear_tick_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop every ``MT_TICK_*`` and Databento variable from the environment."""
    for name in list(os.environ):
        if name.upper().startswith(TICK_ENV_PREFIX) or "DATABENTO" in name.upper():
            monkeypatch.delenv(name)


def closed_port() -> int:
    """A local port nothing listens on: a connection to it fails at once."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
