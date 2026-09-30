"""The purchase planner: what to buy, as monthly batch requests (224).

LLD 224 Technical Decisions 4 and 5. Pure: no I/O, no clock, no psycopg and no
provider client. It reads only manifest facts, day conditions and the
calendar's session days (journal 20260725, rule 2: no aggregate informs
acquisition), all handed in by the caller.

**Wants** are the union of

* tier wants: for each universe entry with a tier, the product's session days
  in its range (an open end runs to the availability edge); and
* companion definitions: for every tier day the manifest owns, or is about to
  be bought, the same ``dataset``, ``symbols``, ``stype_in`` and day at
  ``TickSchema.DEFINITION`` (TD5),

each intersected with the run's ``--start/--end`` window (which only narrows),
minus what is already covered. A wanted day is purchasable when its condition
is ``available`` or ``degraded``; ``pending`` (and any day with no condition
yet, or past the edge) is tallied and left for a later run; ``missing`` is
tallied and never bought (TD4, TD8).

**Grouping**: per ``(dataset, schema, symbols, stype_in)``, purchasable days
form runs of consecutive session days, broken by any session day that is not
purchasable and never crossing a UTC month. A non-session day (a Saturday) sits
inside the request's range and does not break a run.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import NamedTuple

from manta_trading.data.tick.constants import (
    CME_DATASET,
    DatasetCondition,
    SType,
    TickSchema,
)
from manta_trading.data.tick.provider import TickRequest
from manta_trading.data.tick.universe import TickUniverseEntry

#: Conditions the provider will deliver a file for (TD4).
PURCHASABLE_CONDITIONS = frozenset(
    {DatasetCondition.AVAILABLE, DatasetCondition.DEGRADED}
)


class DayKey(NamedTuple):
    """One unit's identity for coverage: the request shape and the UTC day."""

    dataset: str
    schema: TickSchema
    symbols: tuple[str, ...]
    stype_in: SType
    day: date


@dataclass(frozen=True)
class OwnedTierDay:
    """A tier day the manifest holds; ``product`` selects its calendar."""

    product: str
    dataset: str
    schema: TickSchema
    symbols: tuple[str, ...]
    stype_in: SType
    day: date


@dataclass(frozen=True)
class PlannedRequest:
    """One batch job to buy: ``days`` are the session days that get units."""

    product: str
    request: TickRequest
    days: tuple[date, ...]

    @property
    def schema(self) -> TickSchema:
        return self.request.schema


ProductSchema = tuple[str, TickSchema]


@dataclass(frozen=True)
class Plan:
    """The requests to buy plus the day tallies per ``(product, schema)``.

    ``wanted`` counts every uncovered wanted day; ``pending`` and ``missing``
    are the subsets left unbought.
    """

    requests: tuple[PlannedRequest, ...]
    wanted: Mapping[ProductSchema, int]
    pending: Mapping[ProductSchema, int]
    missing: Mapping[ProductSchema, int]


@dataclass(frozen=True)
class PlanInputs:
    """Everything the planner reads. ``window_end`` is exclusive."""

    universe: Sequence[TickUniverseEntry]
    owned: Sequence[OwnedTierDay]
    covered: frozenset[DayKey]
    sessions: Mapping[str, Sequence[date]]
    conditions: Mapping[tuple[str, date], DatasetCondition]
    edge_end: Mapping[str, date]
    window_start: date | None = None
    window_end: date | None = None


class _Shape(NamedTuple):
    product: str
    dataset: str
    schema: TickSchema
    symbols: tuple[str, ...]
    stype_in: SType


@dataclass
class _Tallies:
    wanted: Counter[ProductSchema] = field(default_factory=Counter)
    pending: Counter[ProductSchema] = field(default_factory=Counter)
    missing: Counter[ProductSchema] = field(default_factory=Counter)


def _in_window(day: date, inputs: PlanInputs) -> bool:
    if inputs.window_start is not None and day < inputs.window_start:
        return False
    return inputs.window_end is None or day < inputs.window_end


def _tier_wants(inputs: PlanInputs) -> dict[_Shape, set[date]]:
    """Universe tier days: session days in each entry's range (TD3, TD4)."""
    wants: dict[_Shape, set[date]] = {}
    for entry in inputs.universe:
        if entry.tier is None or entry.start is None:
            continue
        end = entry.end if entry.end is not None else inputs.edge_end.get(CME_DATASET)
        if end is None:
            raise ValueError(
                f"{entry.product}: the universe range is open-ended but no "
                f"availability edge is recorded for {CME_DATASET}"
            )
        shape = _Shape(
            entry.product, CME_DATASET, entry.tier, entry.symbols, entry.stype_in
        )
        wants[shape] = {
            day
            for day in inputs.sessions[entry.product]
            if entry.start <= day < end and _in_window(day, inputs)
        }
    return wants


def _add_companion(
    wants: dict[_Shape, set[date]],
    tier: _Shape,
    days: Iterable[date],
) -> None:
    """Record definition wants for ``days`` of the tier's request shape."""
    shape = tier._replace(schema=TickSchema.DEFINITION)
    wants.setdefault(shape, set()).update(days)


def _companion_wants(
    tier: dict[_Shape, set[date]], inputs: PlanInputs
) -> dict[_Shape, set[date]]:
    """A definition day for every owned or about-to-be-bought tier day (TD5)."""
    wants: dict[_Shape, set[date]] = {}
    for shape, days in tier.items():
        _add_companion(wants, shape, days)
    for owned in inputs.owned:
        if _in_window(owned.day, inputs):
            shape = _Shape(
                owned.product,
                owned.dataset,
                owned.schema,
                owned.symbols,
                owned.stype_in,
            )
            _add_companion(wants, shape, [owned.day])
    return wants


def _classify(
    shape: _Shape, days: set[date], inputs: PlanInputs, tallies: _Tallies
) -> list[date]:
    """The purchasable uncovered days of one shape; tallies the rest."""
    key = (shape.product, shape.schema)
    purchasable = []
    for day in sorted(days):
        if DayKey(shape.dataset, shape.schema, shape.symbols, shape.stype_in, day) in (
            inputs.covered
        ):
            continue
        tallies.wanted[key] += 1
        edge = inputs.edge_end.get(shape.dataset)
        condition = inputs.conditions.get((shape.dataset, day))
        if condition is DatasetCondition.MISSING:
            tallies.missing[key] += 1
        elif condition in PURCHASABLE_CONDITIONS and (edge is None or day < edge):
            purchasable.append(day)
        else:  # pending, no condition yet, or past the edge
            tallies.pending[key] += 1
    return purchasable


def _runs(days: list[date], sessions: Sequence[date]) -> list[list[date]]:
    """Runs of consecutive session days within one UTC month (TD4)."""
    position = {day: n for n, day in enumerate(sessions)}
    runs: list[list[date]] = []
    for day in days:
        if day not in position:
            raise ValueError(f"{day} is wanted but is not a session day of its product")
        previous = runs[-1][-1] if runs else None
        joins = (
            previous is not None
            and position[day] == position[previous] + 1
            and (day.year, day.month) == (previous.year, previous.month)
        )
        if joins:
            runs[-1].append(day)
        else:
            runs.append([day])
    return runs


def _requests(
    shape: _Shape, days: list[date], sessions: Sequence[date]
) -> Iterator[PlannedRequest]:
    for run in _runs(days, sessions):
        request = TickRequest(
            dataset=shape.dataset,
            symbols=shape.symbols,
            stype_in=shape.stype_in,
            schema=shape.schema,
            start=run[0],
            end=run[-1] + timedelta(days=1),
        )
        yield PlannedRequest(shape.product, request, tuple(run))


def _order(planned: PlannedRequest) -> tuple[date, bool, str, str, tuple[str, ...]]:
    """First day; within a day definitions before tiers (TD4)."""
    request = planned.request
    return (
        request.start,
        request.schema is not TickSchema.DEFINITION,
        planned.product,
        request.schema.value,
        request.symbols,
    )


def plan_purchases(inputs: PlanInputs) -> Plan:
    """The purchase plan for one run. Pure; see the module docstring."""
    tier = _tier_wants(inputs)
    wants = {**tier, **_companion_wants(tier, inputs)}
    tallies = _Tallies()
    requests: list[PlannedRequest] = []
    for shape, days in wants.items():
        purchasable = _classify(shape, days, inputs, tallies)
        requests.extend(_requests(shape, purchasable, inputs.sessions[shape.product]))
    return Plan(
        requests=tuple(sorted(requests, key=_order)),
        wanted=dict(tallies.wanted),
        pending=dict(tallies.pending),
        missing=dict(tallies.missing),
    )
