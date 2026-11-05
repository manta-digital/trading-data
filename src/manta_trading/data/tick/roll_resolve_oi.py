"""The open-interest roll rule ``n`` (slice 303, D9). Pure.

Mirrors the volume rule: session S is answered from the open interest of the
session ``RANK_LAG_SESSIONS`` before it, never S's own (CME publishes it after S,
so it would look ahead). Candidates are the outrights eligible at S and listed
in L, ranked by resolved open interest at L, ties to the earlier expiration.

A candidate with no open interest record at L is not ranked: CME reports open
interest only for contracts that hold some (GC's deferred contracts report it on
8 to 11 of 40 sessions, ES's far months never; slice 303 task 8), so absence is
not a gap to refuse over, and it is never read as zero.

What the provider's data cannot decide is an answer, ``Unresolved``: L's
statistics session is not load-complete, or no candidate has open interest at
L. A broken invariant is ``RollInputError``, as everywhere in the resolver.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from manta_trading.data.tick.constants import RANK_LAG_SESSIONS
from manta_trading.data.tick.roll_rules import RollRule
from manta_trading.data.tick.roll_types import Answer, Unresolved
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.tick_status import TickSessionStatus

if TYPE_CHECKING:
    from manta_trading.data.tick.roll_resolve import _Resolver


def open_interest_answer(resolver: _Resolver, rule: RollRule, index: int) -> Answer:
    """The contract ``rule`` picks on session ``index`` by open interest at L."""
    day = resolver.sessions[index].session_date
    lag = index - RANK_LAG_SESSIONS
    if lag < 0:
        return Unresolved(
            day,
            f"open interest inputs: no session {RANK_LAG_SESSIONS} before {day}"
            " in the calendar read",
        )
    basis = resolver.sessions[lag].session_date
    status = resolver.inputs.statistics_statuses.get(basis)
    if status is not TickSessionStatus.COMPLETE:
        shown = "not judged" if status is None else status.value
        return Unresolved(day, f"open interest inputs: session {basis} is {shown}")
    opened, lag_close = resolver.opens[index], resolver.closes[lag]
    candidates = [
        c
        for c in resolver.contracts
        if c.activation_ns <= opened <= c.expiration_ns and c.activation_ns <= lag_close
    ]
    held = resolver.interest.get(basis, {})
    candidates = [c for c in candidates if c.instrument_id in held]
    if not candidates:
        return Unresolved(
            day, f"open interest inputs: no candidate has open interest in {basis}"
        )
    # Stable sort over expiration order: equal interest keeps the earlier expiry.
    candidates.sort(key=lambda c: -held[c.instrument_id].value)
    chosen = resolver.pick(rule, index, candidates)
    if isinstance(chosen, Unresolved):
        return chosen
    units = tuple(sorted({held[c.instrument_id].unit_id for c in candidates}))
    return resolver.make_contract(index, chosen, basis, units, SchemaFamily.STATISTICS)
