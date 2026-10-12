"""The tick line's states on a composed surface (slice 230, TD8).

Accounting and overview (and 234's health, and 231's API) say why they could
not read the tick database with these states, never by leaving the line out.
:func:`unavailable_text` is the one place the line's wording lives. No I/O,
no CLI import: 231 maps its own pool's failures onto the same states.

Named ``line_state``, not ``availability``: ``availability.py`` is 224's
dataset availability capture.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from manta_trading.data.tick.constants import TICK_MIGRATE_COMMAND


class TickLineState(StrEnum):
    OK = "ok"
    NOT_CONFIGURED = "not_configured"
    UNREACHABLE = "unreachable"
    MIGRATIONS_PENDING = "migrations_pending"
    #: Reachable, but the read was refused or failed: a calendar that does not
    #: reach the range, or an unexpected database error. Not an outage.
    FAILED = "failed"


@dataclass(frozen=True)
class TickUnavailable:
    """Why the tick line has no figures. ``detail`` is one line, no secrets."""

    state: TickLineState
    detail: str


def first_line(exc: BaseException) -> str:
    """An error as one line for an operator: its first line, or its type."""
    lines = str(exc).strip().splitlines()
    return lines[0] if lines else type(exc).__name__


def unavailable_text(unavailable: TickUnavailable) -> str:
    """The line's text: ``not configured (MT_TICK_DB_URL unset)``,
    ``unreachable: <error>``, ``migrations pending (<remedy>)``,
    ``failed: <error>``."""
    match unavailable.state:
        case TickLineState.NOT_CONFIGURED:
            return f"not configured ({unavailable.detail})"
        case TickLineState.UNREACHABLE:
            return f"unreachable: {unavailable.detail}"
        case TickLineState.MIGRATIONS_PENDING:
            return f"migrations pending ({TICK_MIGRATE_COMMAND})"
        case TickLineState.FAILED:
            return f"failed: {unavailable.detail}"
        case TickLineState.OK:
            raise ValueError("an ok tick line is not unavailable")
