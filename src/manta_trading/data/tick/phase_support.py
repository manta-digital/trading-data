"""Shared phase plumbing: turn a phase body into a ``PhaseReport`` (224).

LLD 224 *Errors* and the error mapping in the task file: run-level failures
become the phase outcome, following Kalshi's template — ``ProviderError`` →
``provider_abort``; ``psycopg.OperationalError``, the archive write error and
a calendar failure → ``storage_abort``. Anything else propagates: there is no
catch-all. The body fills ``summary`` as it goes, so an aborted phase still
reports what it did before it stopped (for example the jobs already submitted).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import psycopg

from manta_trading.data.tick.adopt_files import TickArchiveWriteError
from manta_trading.data.tick.pass_contract import (
    PhaseReport,
    TickOutcome,
    TickPassPhaseName,
)
from manta_trading.data.tick.tick_calendar import TickCalendarError
from manta_trading.logging import get_logger
from manta_trading.providers.errors import ProviderError

logger = get_logger(__name__)

#: Failures that end the pass as ``storage_abort``.
STORAGE_FAILURES = (psycopg.OperationalError, TickArchiveWriteError, TickCalendarError)

PhaseBody = Callable[[dict[str, Any]], Awaitable[TickOutcome]]


async def run_phase(name: TickPassPhaseName, body: PhaseBody) -> PhaseReport:
    """Run ``body`` (which fills ``summary`` and returns its outcome)."""
    started = time.monotonic()
    summary: dict[str, Any] = {}
    error: str | None = None
    try:
        outcome = await body(summary)
    except ProviderError as exc:
        outcome, error = TickOutcome.PROVIDER_ABORT, str(exc)
    except STORAGE_FAILURES as exc:
        logger.exception("tick %s phase storage failure", name)
        outcome, error = TickOutcome.STORAGE_ABORT, str(exc)
    return PhaseReport(
        name=name,
        outcome=outcome,
        summary=summary,
        duration_ms=int((time.monotonic() - started) * 1000),
        error=error,
    )
