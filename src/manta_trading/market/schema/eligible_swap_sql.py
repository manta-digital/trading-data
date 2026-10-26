"""SQL for migration 064: swap the eligible ``_next`` minute caggs in (slice 173).

The swap is renames only. In one transaction every current cagg becomes
``_prev`` and every ``_next`` cagg takes the current name, then ``data_status``
is re-issued so it binds to the new ``minute_coverage`` (a view binds to its
source by OID, so without that it would keep reading the old one). The drops
run after the commit, coverage first because it sits on the 4h rollup.
Probed on TimescaleDB 2.29.1: renames work in a transaction, and a
hierarchical cagg follows its renamed parent.

Every step is guarded, so a rerun finishes a partly applied swap and a rerun
on a finished one changes nothing. A current cagg is renamed away only while
its ``_next`` twin exists, so a rerun can never strand the current name.

Kept out of ``migrations/minute.py`` so that file does not grow; it imports
this one, never the reverse.
"""

from __future__ import annotations

from typing import NamedTuple

from manta_trading.constants import (
    CAGG_PREV_SUFFIX,
    GRANULARITY_SOURCE,
    MINUTE_CAGG_GRANULARITIES,
    MINUTE_COVERAGE_VIEW,
    CaggTarget,
    target_view_name,
)


class SwapNames(NamedTuple):
    """The three names one minute cagg passes through in the swap."""

    current: str
    next: str
    prev: str


def _names(view: str) -> SwapNames:
    return SwapNames(
        current=view,
        next=target_view_name(view, CaggTarget.NEXT),
        prev=view + CAGG_PREV_SUFFIX,
    )


SWAP_NAMES: tuple[SwapNames, ...] = (
    *(_names(GRANULARITY_SOURCE[g]) for g in MINUTE_CAGG_GRANULARITIES),
    _names(MINUTE_COVERAGE_VIEW),
)
"""The four rollups, smallest bucket first, then ``minute_coverage``."""


def _exists(name: str) -> str:
    return f"to_regclass('public.{name}') IS NOT NULL"


def _absent(name: str) -> str:
    return f"to_regclass('public.{name}') IS NULL"


def _guarded(condition: str, statement: str) -> str:
    return f"DO $$ BEGIN IF {condition} THEN {statement}; END IF; END $$;"


def _rename(source: str, target: str) -> str:
    return f"ALTER MATERIALIZED VIEW {source} RENAME TO {target}"


def swap_rename_statements() -> list[str]:
    """The renames, in order: every current → ``_prev``, then every
    ``_next`` → current. Run them and the ``data_status`` re-issue in one
    transaction."""
    to_prev = [
        _guarded(
            f"{_exists(n.current)} AND {_exists(n.next)} AND {_absent(n.prev)}",
            _rename(n.current, n.prev),
        )
        for n in SWAP_NAMES
    ]
    to_current = [
        _guarded(
            f"{_exists(n.next)} AND {_absent(n.current)}", _rename(n.next, n.current)
        )
        for n in SWAP_NAMES
    ]
    return to_prev + to_current


def swap_drop_statements() -> list[str]:
    """The ``_prev`` drops, coverage first, each only once its replacement
    holds the current name. Run after the rename transaction commits."""
    return [
        _guarded(
            f"{_exists(n.prev)} AND {_exists(n.current)}",
            f"DROP MATERIALIZED VIEW {n.prev}",
        )
        for n in reversed(SWAP_NAMES)
    ]
