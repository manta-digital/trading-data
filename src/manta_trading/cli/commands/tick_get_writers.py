"""The three output writers for ``mt data tick get`` (slice 230, TD5, TD11).

One interface: ``begin(plan)`` before the first row, ``rows(batch)`` per
fetched batch, then ``finish(exit_code)`` or, when the read stops after rows
went out, ``partial(n_rows, error)``. Rows are written as they arrive, never
collected: text is fixed-width lines (Rich would buffer a table), CSV keeps
stdout to the header and rows, and JSON is one object written incrementally
in TD5's key order. Every price goes through ``price_text`` and every
nanosecond time through ``ts_text``.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from typing import Any, Protocol, TextIO

from manta_trading.cli.commands.tick_active_render import contract_label, rule_label
from manta_trading.cli.commands.tick_exit import EXIT_STORAGE
from manta_trading.data.tick.constants import TICK_GET_TICKS
from manta_trading.data.tick.get_plan import (
    ContractTarget,
    DisplaySegment,
    GetPlan,
)
from manta_trading.data.tick.roll_payload import unresolved_dict
from manta_trading.data.tick.roll_rules import DEFAULT_ROLL_RULE
from manta_trading.data.tick.tick_format import price_text, ts_text
from manta_trading.data.tick.tick_reads import (
    BAR_COLUMNS,
    BAR_PRICE_COLUMNS,
    TICK_COLUMNS,
    TICK_PRICE_COLUMNS,
    TIME_COLUMNS,
    Row,
)

#: Text column widths; a column not listed is 8 wide.
_WIDTHS = {
    "ts_event": 30,
    "time": 30,
    "session_date": 10,
    "raw_symbol": 10,
    "instrument_id": 10,
    "price": 14,
    "bid_px": 14,
    "ask_px": 14,
    "open": 14,
    "high": 14,
    "low": 14,
    "close": 14,
    "volume": 10,
    "side": 4,
    "sequence": 12,
}
_TEXT_NULL = "-"


class GetWriter(Protocol):
    def begin(self, plan: GetPlan) -> None: ...
    def rows(self, batch: Sequence[Row]) -> None: ...
    def finish(self, exit_code: int) -> None: ...
    def partial(self, n_rows: int, error: str) -> None: ...


def columns_for(what: str) -> tuple[str, ...]:
    return TICK_COLUMNS if what == TICK_GET_TICKS else BAR_COLUMNS


def _cells(columns: Sequence[str], row: Row) -> list[Any]:
    """JSON-ready values: exact prices as strings (``None`` if undefined),
    times as ISO with nine digits, dates as ISO, the rest as stored."""
    out: list[Any] = []
    for name, value in zip(columns, row, strict=True):
        if value is None:
            out.append(None)
        elif name in TICK_PRICE_COLUMNS or name in BAR_PRICE_COLUMNS:
            out.append(price_text(value))
        elif name in TIME_COLUMNS:
            out.append(ts_text(value))
        elif name == "session_date":
            out.append(value.isoformat())
        else:
            out.append(value)
    return out


def _target_dict(plan: GetPlan) -> dict[str, Any]:
    target = plan.target
    if isinstance(target, ContractTarget):
        return {"contract": target.contract, "instrument_id": target.instrument_id}
    return {
        "product": target.product,
        "rule": str(target.rule),
        "is_default": target.rule == DEFAULT_ROLL_RULE,
        "definitions_as_of": _iso(plan.definitions_as_of),
    }


def _iso(day: Any) -> str | None:
    return None if day is None else day.isoformat()


def _segment_text(s: DisplaySegment) -> str:
    return (
        f"{s.first_session} → {s.last_session}  "
        f"{contract_label(s.raw_symbol, s.instrument_id)}"
    )


def _head_lines(plan: GetPlan, what: str) -> list[str]:
    target = plan.target
    if isinstance(target, ContractTarget):
        name = contract_label(target.contract, target.instrument_id)
    else:
        name = f"{target.product}  {rule_label(target.rule).rstrip()}"
        if plan.definitions_as_of is not None:
            name += f"  definitions as of {plan.definitions_as_of}"
    if plan.start is None or plan.end is None:
        span = "nothing held"  # no range given, and no held session to default to
    else:
        span = f"{plan.start} → {plan.end} (end exclusive)"
    return [f"{name}  {what}  {span}", *(f"  {_segment_text(s)}" for s in plan.display)]


def _list_lines(plan: GetPlan) -> list[str]:
    return [
        *(f"unresolved {u.session_date}  {u.reason}" for u in plan.unresolved),
        *(f"incomplete {i.session_date}  {i.status.value}" for i in plan.incomplete),
    ]


class TextWriter:
    def __init__(self, what: str, out: TextIO, err: TextIO) -> None:
        self._what, self._out, self._err = what, out, err
        self._columns = columns_for(what)
        self._plan: GetPlan | None = None
        self._n = 0

    def _line(self, cells: Sequence[Any]) -> str:
        return "  ".join(
            str(c).ljust(_WIDTHS.get(n, 8))
            for n, c in zip(self._columns, cells, strict=True)
        ).rstrip()

    def begin(self, plan: GetPlan) -> None:
        self._plan = plan
        lines = [*_head_lines(plan, self._what), self._line(self._columns)]
        self._out.write("\n".join(lines) + "\n")

    def rows(self, batch: Sequence[Row]) -> None:
        self._n += len(batch)
        self._out.write(
            "".join(
                self._line(
                    [_TEXT_NULL if c is None else c for c in _cells(self._columns, r)]
                )
                + "\n"
                for r in batch
            )
        )

    def _lists(self) -> None:
        assert self._plan is not None
        lines = _list_lines(self._plan)
        if lines:
            self._out.write("\n".join(lines) + "\n")

    def finish(self, exit_code: int) -> None:
        self._out.write(f"{self._n} row(s)\n")
        self._lists()

    def partial(self, n_rows: int, error: str) -> None:
        self._out.write(f"PARTIAL: {n_rows} row(s), then: {error}\n")
        self._lists()
        self._err.write(f"{error}\n")


class CsvWriter:
    """Header and rows on stdout; everything else on stderr (FR10)."""

    def __init__(self, what: str, out: TextIO, err: TextIO) -> None:
        self._what, self._err = what, err
        self._columns = columns_for(what)
        self._csv = csv.writer(out, lineterminator="\n")

    def begin(self, plan: GetPlan) -> None:
        self._csv.writerow(self._columns)
        lines = [*_head_lines(plan, self._what), *_list_lines(plan)]
        self._err.write("\n".join(lines) + "\n")

    def rows(self, batch: Sequence[Row]) -> None:
        self._csv.writerows(
            ["" if c is None else c for c in _cells(self._columns, r)] for r in batch
        )

    def finish(self, exit_code: int) -> None:
        return None

    def partial(self, n_rows: int, error: str) -> None:
        self._err.write(f"partial output: {n_rows} rows written, then: {error}\n")


class JsonWriter:
    """One object, written as the rows arrive; valid even when cut short."""

    def __init__(self, what: str, out: TextIO, err: TextIO) -> None:
        self._what, self._out = what, out
        self._columns = columns_for(what)
        self._n = 0

    def begin(self, plan: GetPlan) -> None:
        head = {
            "target": _target_dict(plan),
            "what": self._what,
            "start": _iso(plan.start),
            "end": _iso(plan.end),
            "segments": [
                {
                    "first_session": s.first_session.isoformat(),
                    "last_session": s.last_session.isoformat(),
                    "raw_symbol": s.raw_symbol,
                    "instrument_id": s.instrument_id,
                }
                for s in plan.display
            ],
            "unresolved": [unresolved_dict(u) for u in plan.unresolved],
            "incomplete": [
                {"session_date": i.session_date.isoformat(), "status": i.status.value}
                for i in plan.incomplete
            ],
        }
        # The object stays open at "rows": [ ; the tail closes it.
        self._out.write(json.dumps(head)[:-1] + ', "rows": [')

    def rows(self, batch: Sequence[Row]) -> None:
        if not batch:
            return
        objects = (
            json.dumps(dict(zip(self._columns, _cells(self._columns, r), strict=True)))
            for r in batch
        )
        lead = "" if self._n == 0 else ",\n"
        self._n += len(batch)
        self._out.write(lead + ",\n".join(objects))

    def _close(self, complete: bool, error: str | None, exit_code: int) -> None:
        tail = {
            "row_count": self._n,
            "complete": complete,
            "error": error,
            "exit_code": exit_code,
        }
        self._out.write("], " + json.dumps(tail)[1:] + "\n")

    def finish(self, exit_code: int) -> None:
        self._close(True, None, exit_code)

    def partial(self, n_rows: int, error: str) -> None:
        self._close(False, error, EXIT_STORAGE)


def writer_for(
    what: str, *, json_output: bool, csv_output: bool, out: TextIO, err: TextIO
) -> GetWriter:
    if json_output:
        return JsonWriter(what, out, err)
    if csv_output:
        return CsvWriter(what, out, err)
    return TextWriter(what, out, err)
