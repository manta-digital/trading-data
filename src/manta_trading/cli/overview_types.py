"""The shapes ``mt data overview`` builds and renders — a leaf module.

Both the command (:mod:`manta_trading.cli.commands.overview`, which gathers
and builds) and the renderer (:mod:`manta_trading.cli.rendering.overview`,
which prints) depend on these, and neither depends on the other. The
renderer used to import them from the command, inverting the usual
direction — commands import rendering — and the only thing preventing an
import cycle was that the command deferred its rendering import into a
function body (922 review F008).

Nothing here performs I/O or derives anything: the facts are what was read,
the overview is what will be printed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from manta_trading.api.eodhd_account import CreditUsage
from manta_trading.data.acquisition.pass_runs import PassKind, PassRun, PassRunOutcome

CREDITS_NO_KEY = "unavailable (MT_EODHD_API_KEY not configured)"
"""Shown when no API key is configured — a setting, not a fault."""

NEVER_RUN = "never run"
"""Shown for a pass kind with no recorded run at all."""

NO_ACCOUNTING = "never computed — run mt data accounting"
"""Shown when no accounting pass has recorded a universe line."""


def credits_unavailable(reason: str) -> str:
    """The credit line when the account endpoint could not be reached."""
    return f"unavailable ({reason})"


# ---------------------------------------------------------------------------
# Facts (what gather reads)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceFreshness:
    """The newest row in one source table."""

    name: str
    newest: datetime | None


@dataclass
class OverviewFacts:
    """Everything :func:`build_overview` needs, and nothing derived."""

    now: datetime
    hostname: str
    open_runs: dict[PassKind, list[PassRun]] = field(default_factory=dict)
    latest_ended: dict[PassKind, PassRun | None] = field(default_factory=dict)
    sources: list[SourceFreshness] = field(default_factory=list)
    credits: CreditUsage | None = None
    credits_error: str | None = None
    minute_firing_days: tuple[int, ...] | None = None


# ---------------------------------------------------------------------------
# The rendered shape (what build_overview returns)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunningRow:
    """One still-open run of a pass kind."""

    phase: str | None
    done: int | None
    total: int | None
    since: datetime
    progress_at: datetime | None
    hostname: str
    pid: int
    abandoned: bool
    """True when this host owns the row and the process is gone."""


@dataclass(frozen=True)
class LastRun:
    """The most recent ended run of a pass kind."""

    started_at: datetime
    ended_at: datetime
    outcome: PassRunOutcome
    exit_code: int | None
    detail: str | None

    @property
    def failed(self) -> bool:
        return self.outcome is PassRunOutcome.FAILED


@dataclass(frozen=True)
class PassLine:
    """One row of the PASSES block."""

    kind: PassKind
    cadence: str
    running: tuple[RunningRow, ...]
    last: LastRun | None
    next_firing: datetime | None


@dataclass(frozen=True)
class Overview:
    """Everything the renderer prints."""

    now: datetime
    passes: tuple[PassLine, ...]
    sources: tuple[SourceFreshness, ...]
    health_verdict: str | None
    health_at: datetime | None
    credits: CreditUsage | None
    credits_text: str
    universe: str | None
    universe_at: datetime | None
