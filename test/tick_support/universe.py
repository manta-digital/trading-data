"""An explicit untiered universe for tests of the no-tier path (slice 226).

``TICK_UNIVERSE`` is production configuration: since 226 it names ES's tier
and range. Tests written for the pass's mechanics with no tier chosen use
this instead, so a configuration edit never changes what they test.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from manta_trading.data.tick import acquisition_pass, purchase_plan
from manta_trading.data.tick.universe import TICK_UNIVERSE, TickUniverseEntry

UNTIERED: tuple[TickUniverseEntry, ...] = tuple(
    replace(entry, tier=None, start=None, end=None) for entry in TICK_UNIVERSE
)


def use_untiered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the pass's two readers of the universe at ``UNTIERED``."""
    monkeypatch.setattr(acquisition_pass, "TICK_UNIVERSE", UNTIERED)
    monkeypatch.setattr(purchase_plan, "TICK_UNIVERSE", UNTIERED)
