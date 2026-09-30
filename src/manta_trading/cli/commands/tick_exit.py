"""Exit codes for ``mt data tick`` — defined once, here (slices 220, 223, 224).

Integers live in this module only; ``tick.py`` re-exports what it uses.
``EXIT_BY_OUTCOME`` maps every pass outcome, with an exhaustiveness assert so a
new ``TickOutcome`` member cannot silently exit 0. ``exit_code_for`` maps the
failures a verb can raise (preflight, provider, storage) to their code.
"""

from __future__ import annotations

from manta_trading.data.tick.pass_contract import TickOutcome
from manta_trading.providers.errors import ProviderError

EXIT_OK = 0
EXIT_PREFLIGHT = 1
EXIT_PROVIDER = 2
EXIT_PARTIAL = 3  # some units failed; the run itself finished (223 adopt, 224 pass)
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


def exit_code_for(exc: BaseException) -> int | None:
    """The exit code for a verb's failure; ``None`` lets it propagate."""
    import psycopg

    from manta_trading.data.tick.adopt import TickVerifyInterrupted
    from manta_trading.data.tick.adopt_files import (
        TickAdoptionRefused,
        TickArchiveWriteError,
    )
    from manta_trading.data.tick.run_context import TickPreflightError
    from manta_trading.data.tick.tick_calendar import TickCalendarError

    if isinstance(exc, TickPreflightError | TickAdoptionRefused):
        return EXIT_PREFLIGHT
    if isinstance(exc, ProviderError | TickVerifyInterrupted):
        return EXIT_PROVIDER
    if isinstance(
        exc, TickArchiveWriteError | TickCalendarError | psycopg.OperationalError
    ):
        return EXIT_STORAGE
    return None
