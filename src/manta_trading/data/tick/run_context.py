"""The tick run context: the store context plus the provider (223, 225).

LLD 224 Technical Decision 2, split by LLD 225 Technical Decision 1. Every
manifest writer that talks to the provider (``adopt``, ``reset``, 224's
``pass``) runs inside :func:`open_tick_run`. Before it yields, it refuses —
:class:`TickPreflightError`, exit 1 at the CLI — in this order:

1. an unknown ``MT_TICK_*`` key (``store_context.check_env_keys``);
2. the Databento API key is unset (the provider refuses to build);
3. then :func:`~manta_trading.data.tick.store_context.open_tick_store`'s
   remaining refusals under ``TICK_ACQUISITION_LOCK_KEY``: the tick URL, the
   archive directory, connect, pending migrations, the lock.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, fields
from pathlib import Path
from types import TracebackType
from typing import Protocol

from manta_trading.config import ENV_FILE, Settings
from manta_trading.data.tick.constants import TICK_ACQUISITION_LOCK_KEY
from manta_trading.data.tick.provider import (
    ITickAcquisitionProvider,
    ITickMetadataProvider,
)
from manta_trading.data.tick.store_context import (
    TRACK_NOT_APPLIED,
    Clock,
    TickPreflightError,
    TickStore,
    check_env_keys,
    known_tick_env_names,
    lock_held_message,
    open_tick_store,
    utc_now,
)
from manta_trading.providers.errors import ProviderAuthError

LOCK_HELD = lock_held_message(TICK_ACQUISITION_LOCK_KEY)

__all__ = [
    "LOCK_HELD",
    "TRACK_NOT_APPLIED",
    "Clock",
    "ProviderFactory",
    "TickPreflightError",
    "TickProvider",
    "TickRun",
    "known_tick_env_names",
    "open_tick_run",
    "utc_now",
]


class TickProvider(ITickMetadataProvider, ITickAcquisitionProvider, Protocol):
    """One provider instance per run, closed when the run ends (220 TD2)."""

    def __enter__(self) -> TickProvider: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
        /,
    ) -> None: ...


ProviderFactory = Callable[[Settings], TickProvider]


@dataclass(frozen=True)
class TickRun(TickStore):
    """The store plus the run's provider: what every provider verb works with."""

    provider: TickProvider


def _default_provider(settings: Settings) -> TickProvider:
    # Imported here: ``databento`` adds ~0.15 s to every ``mt`` startup.
    from manta_trading.data.tick.databento.adapter import DatabentoTickProvider

    return DatabentoTickProvider.from_settings(settings)


@asynccontextmanager
async def open_tick_run(
    settings: Settings,
    *,
    clock: Clock = utc_now,
    provider_factory: ProviderFactory = _default_provider,
    env_file: Path | None = Path(ENV_FILE),
) -> AsyncIterator[TickRun]:
    """Run the preflight in TD2's order, then yield the run; release on exit.

    The env-key check runs before the provider is built so a misspelt key is
    reported first; ``open_tick_store`` repeats it (cheap, and the store must
    check on its own when opened directly).
    """
    check_env_keys(env_file)
    try:
        provider = provider_factory(settings)
    except ProviderAuthError as exc:
        raise TickPreflightError(str(exc)) from exc
    async with AsyncExitStack() as stack:
        stack.enter_context(provider)
        store = await stack.enter_async_context(
            open_tick_store(
                settings,
                lock_key=TICK_ACQUISITION_LOCK_KEY,
                clock=clock,
                env_file=env_file,
            )
        )
        yield TickRun(
            **{field.name: getattr(store, field.name) for field in fields(store)},
            provider=provider,
        )
