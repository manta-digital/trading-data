"""The roll resolver: which contract a rule picks on each session (229). Pure.

LLD 229 TD2 (calendar), TD3 (volume), TD7 (next roll). Input is a
:class:`RollInputs` read from one snapshot; output is one :data:`Answer` per
session. An answer the inputs cannot decide is :class:`Unresolved` with a
reason; a broken invariant raises :class:`RollInputError`. No fallback: an
unresolved volume session is never filled from calendar, partial volume, or
the previous answer.

Calendar eligibility needs only the session ``offset`` places after S: a
contract is eligible on S when that session opens at or before its expiration
(TD2's ``index(S) <= index(E(c)) - offset``, without locating E(c)). A
contract expiring past the populated calendar is therefore never an error; a
session the rule needs past it is (``mt data extend``).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import date

from manta_trading.data.tick.constants import VOLUME_RANK_LAG_SESSIONS, RollMethod
from manta_trading.data.tick.ingest_records import utc_ns
from manta_trading.data.tick.roll_rules import RollRule
from manta_trading.data.tick.roll_types import (
    EXTEND_REMEDY,
    ActiveContract,
    ActiveLine,
    Answer,
    ContractDefinition,
    NextRoll,
    RollInputError,
    RollInputs,
    Unresolved,
    utc_from_ns,
)
from manta_trading.data.tick.tick_status import TickSessionStatus


class _Resolver:
    """Indexes one ``RollInputs`` once; answers any session by position."""

    def __init__(self, inputs: RollInputs) -> None:
        self.inputs = inputs
        self.sessions = inputs.sessions
        self.position = {s.session_date: i for i, s in enumerate(self.sessions)}
        self.opens = [utc_ns(s.open_utc) for s in self.sessions]
        self.closes = [utc_ns(s.close_utc) for s in self.sessions]
        self.contracts = sorted(inputs.contracts, key=lambda c: c.expiration_ns)
        self.volume: dict[date, dict[int, int]] = defaultdict(dict)
        self.units: dict[date, set[int]] = defaultdict(set)
        for row in inputs.volumes:
            day = self.volume[row.session_date]
            day[row.instrument_id] = day.get(row.instrument_id, 0) + row.volume
            self.units[row.session_date].add(row.unit_id)

    def index_of(self, day: date) -> int:
        try:
            return self.position[day]
        except KeyError:
            raise RollInputError(f"session {day} is not in the calendar read") from None

    def session_at(self, index: int, needed_by: date) -> int:
        """``index`` if the calendar read reaches it, else the extend error."""
        if index >= len(self.sessions):
            last = self.sessions[-1].session_date if self.sessions else None
            raise RollInputError(
                f"session {needed_by} needs a calendar session after {last};"
                f" {EXTEND_REMEDY}"
            )
        return index

    def answer(self, rule: RollRule, index: int) -> Answer:
        if rule.method is RollMethod.CALENDAR:
            return self._calendar(rule, index)
        return self._volume(rule, index)

    def _pick(
        self, rule: RollRule, index: int, ranked: list[ContractDefinition]
    ) -> ContractDefinition | Unresolved:
        if rule.rank < len(ranked):
            return ranked[rule.rank]
        day = self.sessions[index].session_date
        return Unresolved(
            day, f"rank {rule.rank}: only {len(ranked)} contracts eligible on {day}"
        )

    def _contract(
        self,
        index: int,
        chosen: ContractDefinition,
        basis: date,
        units: tuple[int, ...],
    ) -> ActiveContract:
        return ActiveContract(
            session_date=self.sessions[index].session_date,
            instrument_id=chosen.instrument_id,
            raw_symbol=chosen.raw_symbol,
            expiration=utc_from_ns(chosen.expiration_ns),
            basis_session=basis,
            definitions_as_of=self.inputs.definitions_as_of,
            definition_unit_id=chosen.unit_id,
            basis_unit_ids=units,
        )

    def _calendar(self, rule: RollRule, index: int) -> Answer:
        """TD2: listed by S's open, and the session ``offset`` after S opens
        at or before expiration; ordered by expiration."""
        day = self.sessions[index].session_date
        horizon = self.opens[self.session_at(index + rule.offset_sessions, day)]
        opened = self.opens[index]
        eligible = [
            c
            for c in self.contracts
            if c.activation_ns <= opened and horizon <= c.expiration_ns
        ]
        chosen = self._pick(rule, index, eligible)
        if isinstance(chosen, Unresolved):
            return chosen
        return self._contract(index, chosen, day, ())

    def _volume(self, rule: RollRule, index: int) -> Answer:
        """TD3: eligible at S (offset 0) and listed in the lag session L,
        ranked by L's volume, ties to the earlier expiration."""
        day = self.sessions[index].session_date
        lag = index - VOLUME_RANK_LAG_SESSIONS
        if lag < 0:
            return Unresolved(
                day,
                f"volume inputs: no session {VOLUME_RANK_LAG_SESSIONS} before {day}"
                " in the calendar read",
            )
        basis = self.sessions[lag].session_date
        status = self.inputs.statuses.get(basis)
        if status is not TickSessionStatus.COMPLETE:
            shown = "not judged" if status is None else status.value
            return Unresolved(day, f"volume inputs: session {basis} is {shown}")
        opened, lag_close = self.opens[index], self.closes[lag]
        ranked = [
            c
            for c in self.contracts
            if c.activation_ns <= opened <= c.expiration_ns
            and c.activation_ns <= lag_close
        ]
        volumes = self.volume.get(basis, {})
        for c in ranked:
            if c.instrument_id not in volumes:
                raise RollInputError(
                    f"{c.raw_symbol} ({c.instrument_id}) is listed in session {basis}"
                    " but has no ledger row (I13)"
                )
        # Stable sort over expiration order: equal volume keeps the earlier expiry.
        ranked.sort(key=lambda c: -volumes[c.instrument_id])
        chosen = self._pick(rule, index, ranked)
        if isinstance(chosen, Unresolved):
            return chosen
        return self._contract(index, chosen, basis, tuple(sorted(self.units[basis])))


def resolve_sessions(
    rule: RollRule, inputs: RollInputs, start: date, end: date
) -> tuple[Answer, ...]:
    """One answer per session dated ``[start, end)`` in ``inputs.sessions``."""
    resolver = _Resolver(inputs)
    return tuple(
        resolver.answer(rule, i)
        for i, s in enumerate(inputs.sessions)
        if start <= s.session_date < end
    )


def active_line(
    product: str, rule: RollRule, inputs: RollInputs, latest: date
) -> ActiveLine:
    """The answer for the latest complete session ``latest`` and its forecast."""
    resolver = _Resolver(inputs)
    index = resolver.index_of(latest)
    answer = resolver.answer(rule, index)
    line = ActiveLine(product, rule, latest, answer)
    if isinstance(answer, Unresolved):
        return line
    if rule.method is RollMethod.CALENDAR:
        return _calendar_forecast(resolver, rule, index, answer, line)
    return _volume_forecast(resolver, rule, index, answer, line)


def _changed(answer: Answer, current: ActiveContract) -> ActiveContract | None:
    """``answer`` when it names a different contract than ``current``."""
    if (
        isinstance(answer, ActiveContract)
        and answer.instrument_id != current.instrument_id
    ):
        return answer
    return None


def _calendar_forecast(
    resolver: _Resolver,
    rule: RollRule,
    index: int,
    current: ActiveContract,
    line: ActiveLine,
) -> ActiveLine:
    """Exact: the first later session whose answer differs."""
    for later in range(index + 1, len(resolver.sessions)):
        answer = _changed(resolver.answer(rule, later), current)
        if answer is not None:
            return replace(
                line,
                next_roll=NextRoll(answer.session_date, answer),
                known_through=resolver.inputs.calendar_horizon,
            )
    raise RollInputError(
        f"no {rule} roll after session {current.session_date} within the "
        f"calendar read; {EXTEND_REMEDY}"
    )


def _volume_forecast(
    resolver: _Resolver,
    rule: RollRule,
    index: int,
    current: ActiveContract,
    line: ActiveLine,
) -> ActiveLine:
    """The lag sessions after L are decided by held volume (L + 1 and L + 2
    rank by L - 1 and L); beyond them only the bound: the first session after
    the active contract's expiration session."""
    day = current.session_date
    last = resolver.session_at(index + VOLUME_RANK_LAG_SESSIONS, day)
    next_roll = None
    for later in range(index + 1, last + 1):
        answer = _changed(resolver.answer(rule, later), current)
        if answer is not None:
            next_roll = NextRoll(answer.session_date, answer)
            break
    expiry = utc_ns(current.expiration)
    after = next(
        (i for i in range(index, len(resolver.sessions)) if resolver.opens[i] > expiry),
        None,
    )
    if after is None:
        raise RollInputError(
            f"session {day}: the calendar read ends before {current.raw_symbol}'s"
            f" expiration session; {EXTEND_REMEDY}"
        )
    return replace(
        line,
        next_roll=next_roll,
        known_through=resolver.sessions[last].session_date,
        roll_no_later_than=resolver.sessions[after].session_date,
    )
