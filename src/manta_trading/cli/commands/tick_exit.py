"""Exit codes for ``mt data tick`` — defined once, here (slices 220, 223, 224).

Integers live in this module only; ``tick.py`` re-exports what it uses.
``EXIT_BY_OUTCOME`` maps every pass outcome, with an exhaustiveness assert so a
new ``TickOutcome`` member cannot silently exit 0. ``exit_code_for`` maps the
failures a verb can raise (preflight, provider, storage) to their code.

Slice 234 adds the severity order that folds the acquisition and ingest
outcomes of ``mt data tick run`` into one, and the map from a tick outcome to
the ``pass_runs`` outcome that run records.
"""

from __future__ import annotations

from manta_trading.data.acquisition.pass_runs import PassRunOutcome
from manta_trading.data.tick.pass_contract import TickOutcome
from manta_trading.providers.errors import ProviderError

EXIT_OK = 0
EXIT_PREFLIGHT = 1
EXIT_PROVIDER = 2
#: The run finished and part of it failed: some units (223 adopt, 224 pass),
#: a session ``active`` could not resolve, or a ``status`` roll line (229).
EXIT_PARTIAL = 3
EXIT_STORAGE = 4  # archive write, calendar, or tick database failure (223)
EXIT_REFUSED = 5  # a guard refused the purchase (224 pass)
EXIT_IN_FLIGHT = 6  # the wait budget ended with jobs still processing (224 pass)

EXIT_BY_OUTCOME: dict[TickOutcome, int] = {
    TickOutcome.OK: EXIT_OK,
    TickOutcome.PARTIAL: EXIT_PARTIAL,
    TickOutcome.PROVIDER_ABORT: EXIT_PROVIDER,
    TickOutcome.STORAGE_ABORT: EXIT_STORAGE,
    TickOutcome.REFUSED: EXIT_REFUSED,
    TickOutcome.IN_FLIGHT: EXIT_IN_FLIGHT,
}

# Every outcome must have a code — a new member cannot silently exit 0.
assert set(EXIT_BY_OUTCOME) == set(TickOutcome), (
    "the tick exit mapping is not exhaustive — update it after adding a "
    "TickOutcome member"
)

#: Worst first (234 Data Flow step 4). Aborts outrank a finished run that
#: left work undone; ``OK`` is the identity of the fold.
TICK_OUTCOME_SEVERITY: tuple[TickOutcome, ...] = (
    TickOutcome.STORAGE_ABORT,
    TickOutcome.PROVIDER_ABORT,
    TickOutcome.REFUSED,
    TickOutcome.IN_FLIGHT,
    TickOutcome.PARTIAL,
    TickOutcome.OK,
)

assert len(TICK_OUTCOME_SEVERITY) == len(set(TICK_OUTCOME_SEVERITY)) and set(
    TICK_OUTCOME_SEVERITY
) == set(TickOutcome), (
    "the tick severity order is not exhaustive — rank a new TickOutcome member"
)


def worse_outcome(first: TickOutcome, second: TickOutcome) -> TickOutcome:
    """The more severe of two outcomes, by :data:`TICK_OUTCOME_SEVERITY`."""
    rank = TICK_OUTCOME_SEVERITY.index
    return first if rank(first) <= rank(second) else second


#: What a ``mt data tick run`` row records for its combined outcome.
PASS_RUN_OUTCOME_BY_TICK_OUTCOME: dict[TickOutcome, PassRunOutcome] = {
    TickOutcome.OK: PassRunOutcome.COMPLETE,
    TickOutcome.PARTIAL: PassRunOutcome.INCOMPLETE,
    TickOutcome.IN_FLIGHT: PassRunOutcome.INCOMPLETE,
    TickOutcome.REFUSED: PassRunOutcome.INCOMPLETE,
    TickOutcome.PROVIDER_ABORT: PassRunOutcome.PROVIDER_UNAVAILABLE,
    TickOutcome.STORAGE_ABORT: PassRunOutcome.FAILED,
}

assert set(PASS_RUN_OUTCOME_BY_TICK_OUTCOME) == set(TickOutcome), (
    "the tick pass_runs mapping is not exhaustive — update it after adding a "
    "TickOutcome member"
)


def exit_code_for(exc: BaseException) -> int | None:
    """The exit code for a verb's failure; ``None`` lets it propagate."""
    import psycopg

    from manta_trading.data.tick.adopt import TickVerifyInterrupted
    from manta_trading.data.tick.adopt_files import (
        TickAdoptionRefused,
        TickArchiveWriteError,
    )
    from manta_trading.data.tick.roll_types import RollInputError
    from manta_trading.data.tick.run_context import TickPreflightError
    from manta_trading.data.tick.tick_calendar import TickCalendarError

    if isinstance(exc, TickPreflightError | TickAdoptionRefused | RollInputError):
        return EXIT_PREFLIGHT
    if isinstance(exc, ProviderError | TickVerifyInterrupted):
        return EXIT_PROVIDER
    if isinstance(
        exc, TickArchiveWriteError | TickCalendarError | psycopg.OperationalError
    ):
        return EXIT_STORAGE
    return None
