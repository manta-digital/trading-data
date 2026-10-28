"""Database and report side of the slice 173 cutover: the swap's state.

Pure where it can be: ``swap_state`` reads a set of names, ``failing_health``
reads ``mt data health --json`` text. The cutover script owns the order and
the host. Names come from ``eligible_swap_sql.SWAP_NAMES``, never spelled out.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import psycopg
from cutover_172_build import BuildRefused
from psycopg import sql

from manta_trading.constants import GRANULARITY_SOURCE, MINUTE_CAGG_GRANULARITIES
from manta_trading.market.schema.eligible_swap_sql import SWAP_NAMES

ROLLUPS: tuple[str, ...] = tuple(
    GRANULARITY_SOURCE[g] for g in MINUTE_CAGG_GRANULARITIES
)
CURRENT: frozenset[str] = frozenset(n.current for n in SWAP_NAMES)
NEXT: frozenset[str] = frozenset(n.next for n in SWAP_NAMES)
PREV: frozenset[str] = frozenset(n.prev for n in SWAP_NAMES)
#: The ``mt data health`` rows the swap must leave passing: the four rollups'
#: freshness, the coverage cagg's freshness, and the value-parity check.
REQUIRED_HEALTH: tuple[str, ...] = (
    *(f"cagg {view}" for view in ROLLUPS),
    f"cagg {SWAP_NAMES[-1].current}",
    "value parity",
)


class CheckFailed(BuildRefused):
    """The database is not in a state the cutover will act on or accept. A
    ``BuildRefused``, so the 172 step runner records it as a step failure."""


class SwapState(StrEnum):
    BEFORE = "before"  # every current and _next object, no _prev: 064 not run
    SWAPPED = "swapped"  # renames committed, some _prev drops still to run
    DONE = "done"  # only the current names remain


def swap_state(existing: set[str]) -> SwapState:
    """Classify the swap from which of its names exist. 064's renames run in
    one transaction, so no other combination can arise from it; one that does
    is refused rather than guessed at."""
    current, nxt, prev = existing & CURRENT, existing & NEXT, existing & PREV
    if current == CURRENT and nxt == NEXT and not prev:
        return SwapState.BEFORE
    if current == CURRENT and not nxt and prev:
        return SwapState.SWAPPED
    if current == CURRENT and not nxt and not prev:
        return SwapState.DONE
    raise CheckFailed(
        f"unexpected swap state: missing current {sorted(CURRENT - current)}, "
        f"_next {sorted(nxt)}, _prev {sorted(prev)}"
    )


def existing_names(conn: psycopg.Connection[Any]) -> set[str]:
    rows = conn.execute(
        "SELECT relname FROM pg_class WHERE relname = ANY(%s)",
        (sorted(CURRENT | NEXT | PREV),),
    ).fetchall()
    return {row["relname"] for row in rows}


def read_swap_state(conn: psycopg.Connection[Any]) -> SwapState:
    return swap_state(existing_names(conn))


def json_object(output: str) -> dict[str, Any]:
    """The JSON object in a command's output; log lines may surround it."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue  # a "{" inside a log line, not the payload
        if isinstance(value, dict):
            return value
    raise CheckFailed(f"no JSON object in the output: {output[-500:]}")


def failing_health(output: str) -> list[str]:
    """The required health rows that failed or are missing, with details."""
    checks = {c["name"]: c for c in json_object(output).get("checks", [])}
    failing = []
    for name in REQUIRED_HEALTH:
        check = checks.get(name)
        if check is None:
            failing.append(f"{name}: missing from mt data health")
        elif not check["ok"]:
            failing.append(f"{name}: {check['detail']}")
    return failing


def row_counts(conn: psycopg.Connection[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for view in (n.current for n in SWAP_NAMES):
        query = sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(view))
        row = conn.execute(query).fetchone()
        if row is None:
            raise CheckFailed(f"count(*) on {view} returned no row")
        counts[view] = int(row["n"])
    return counts


def report_path(notes_dir: Path, started: str) -> Path:
    return notes_dir / f"{started[:10]}-173-cutover.md"


def render_report(
    started: str,
    ref: str,
    ok: bool,
    steps: list[tuple[str, str, list[str]]],
    counts: dict[str, int] | None,
    detail: str,
) -> str:
    """The cutover report. ``counts`` is None when the database could not be
    read, and ``detail`` then says why; otherwise it is the verify output."""
    lines = [f"# Slice 173 cutover — {started}", "", f"Ref: {ref}", ""]
    lines += [f"Outcome: {'PASS' if ok else 'FAIL'}", "", "## Steps", ""]
    for title, outcome, seen in steps:
        lines.append(f"- **{title}**: {outcome}")
        lines += [f"  - {text}" for text in seen]
    if counts is None:
        lines += ["", f"Database figures unavailable: {detail}"]
        return "\n".join(lines) + "\n"
    lines += ["", "## Rows per minute cagg", ""]
    lines += [f"- {view}: {count:,}" for view, count in counts.items()]
    if detail:
        lines += ["", "## caggs verify", "", "```", detail.rstrip(), "```"]
    return "\n".join(lines) + "\n"
