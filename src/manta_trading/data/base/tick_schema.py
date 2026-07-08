"""Tick event schema constants.

Application-level tick data classes belong to Initiative 120.
"""

from __future__ import annotations

from enum import StrEnum


class TickEventType(StrEnum):
    """Tick event type discriminator.

    Values match the CHECK constraint on ``tick_events.event_type`` exactly.
    """

    TRADE = "trade"
    QUOTE = "quote"
