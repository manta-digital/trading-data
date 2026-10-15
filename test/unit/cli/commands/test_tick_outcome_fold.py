"""The tick outcome fold and the pass_runs map (slice 234, Data Flow step 4)."""

from __future__ import annotations

from itertools import product

import pytest

from manta_trading.cli.commands.tick_exit import (
    EXIT_BY_OUTCOME,
    EXIT_OK,
    EXIT_PROVIDER,
    EXIT_STORAGE,
    PASS_RUN_OUTCOME_BY_TICK_OUTCOME,
    TICK_OUTCOME_SEVERITY,
    worse_outcome,
)
from manta_trading.data.acquisition.pass_runs import PassRunOutcome
from manta_trading.data.tick.pass_contract import TickOutcome

PAIRS = list(product(TickOutcome, repeat=2))


@pytest.mark.parametrize(("first", "second"), PAIRS)
def test_fold_is_symmetric(first: TickOutcome, second: TickOutcome) -> None:
    assert worse_outcome(first, second) is worse_outcome(second, first)


@pytest.mark.parametrize(("first", "second"), PAIRS)
def test_fold_picks_the_higher_severity(
    first: TickOutcome, second: TickOutcome
) -> None:
    rank = TICK_OUTCOME_SEVERITY.index
    expected = first if rank(first) <= rank(second) else second
    assert worse_outcome(first, second) is expected


@pytest.mark.parametrize("outcome", list(TickOutcome))
def test_ok_is_the_identity(outcome: TickOutcome) -> None:
    assert worse_outcome(TickOutcome.OK, outcome) is outcome


def test_the_order_is_the_designed_one() -> None:
    assert TICK_OUTCOME_SEVERITY == (
        TickOutcome.STORAGE_ABORT,
        TickOutcome.PROVIDER_ABORT,
        TickOutcome.REFUSED,
        TickOutcome.IN_FLIGHT,
        TickOutcome.PARTIAL,
        TickOutcome.OK,
    )


def test_the_order_agrees_with_the_exit_vocabulary() -> None:
    """Derived from the exit codes, not a copy of the list: an outcome that
    exits with the storage or provider code is an abort and outranks every
    outcome that finished; the outcome that exits 0 ranks last."""
    rank = TICK_OUTCOME_SEVERITY.index
    aborts = {
        o
        for o, code in EXIT_BY_OUTCOME.items()
        if code in {EXIT_STORAGE, EXIT_PROVIDER}
    }
    clean = {o for o, code in EXIT_BY_OUTCOME.items() if code == EXIT_OK}
    finished = set(TickOutcome) - aborts - clean
    assert aborts and clean and finished
    assert max(rank(o) for o in aborts) < min(rank(o) for o in finished)
    assert max(rank(o) for o in finished) < min(rank(o) for o in clean)
    # Storage outranks provider: a broken store is the harder failure.
    storage = next(o for o, c in EXIT_BY_OUTCOME.items() if c == EXIT_STORAGE)
    provider = next(o for o, c in EXIT_BY_OUTCOME.items() if c == EXIT_PROVIDER)
    assert rank(storage) < rank(provider)


def test_the_map_has_the_designed_values() -> None:
    assert PASS_RUN_OUTCOME_BY_TICK_OUTCOME == {
        TickOutcome.OK: PassRunOutcome.COMPLETE,
        TickOutcome.PARTIAL: PassRunOutcome.INCOMPLETE,
        TickOutcome.IN_FLIGHT: PassRunOutcome.INCOMPLETE,
        TickOutcome.REFUSED: PassRunOutcome.INCOMPLETE,
        TickOutcome.PROVIDER_ABORT: PassRunOutcome.PROVIDER_UNAVAILABLE,
        TickOutcome.STORAGE_ABORT: PassRunOutcome.FAILED,
    }
