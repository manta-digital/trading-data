"""Keep a developer's tick database out of API tests (slice 231).

``lifespan`` builds ``Settings()``, which reads ``.env``. With
``create_app(tick_db_url=None)`` every API test would then open a pool against
the dev tick database named by ``MT_TICK_DB_URL``. The guard patches the
``Settings`` the app module uses so both tick URLs read as unset; a test that
wants a tick database passes ``tick_db_url=`` explicitly, as the load tier does
for the primary. Production code is unchanged.

Enabled by importing :func:`no_dev_tick_settings` into a conftest (or a test
module): it is autouse, so it applies to everything under that file.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from manta_trading.config import Settings

_APP_SETTINGS = "manta_trading.api_server.app.Settings"


def settings_without_tick() -> Settings:
    """The loaded settings with both tick URLs unset."""
    return Settings().model_copy(
        update={"tick_db_url": None, "tick_maintenance_url": None}
    )


@pytest.fixture(autouse=True)
def no_dev_tick_settings() -> Iterator[None]:
    with patch(_APP_SETTINGS, side_effect=settings_without_tick):
        yield
