"""
Shared types and constants for the acquisition daemon package.

This module is leaf-level: no I/O and no imports from slice 121/122.
All other daemon modules import from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DAILY_DAEMON_ID: str = "daily-acquisition"
"""Canonical daemon identity key for the daily acquisition daemon.
Both the daemon (writer) and the status CLI (reader) must reference this
constant — never a bare string literal."""

MINUTE_DAEMON_ID: str = "minute-acquisition"
"""Canonical daemon identity key for the minute acquisition daemon.
Coexists with DAILY_DAEMON_ID in the shared daemon_heartbeat table via
distinct primary keys."""

HEARTBEAT_ALIVE_THRESHOLD_SECONDS: int = 300
"""Daemon is considered alive if last_beat_at is within this many seconds of
now. Used by the status CLI's is_alive check."""


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class SymbolSource(Protocol):
    """Provider of the symbol universe for the daemon's work queue."""

    def get_symbols(self) -> list[str]: ...


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class DaemonConfig:
    """Runtime configuration for a DailyAcquisitionDaemon instance.

    Attributes:
        poll_interval: Seconds to sleep when all symbols are caught up.
        max_retries: Max consecutive failures before a symbol is excluded from
            the work queue (must be manually reset or re-triggered externally).
        daemon_id: Logical identity key written to the heartbeat table.
    """

    poll_interval: int
    max_retries: int
    daemon_id: str
