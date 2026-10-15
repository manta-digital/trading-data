"""Pure logic for the slice 234 cutover (``cutover_234_tick_wiring.py``).

No host access: text and numbers in, verdicts out, so the unit tests exercise
exactly what the cutover decides. The step scaffolding (``Step``,
``StepStatus``, ``StepFailed``, ``require``) is 227's, reused rather than
re-spelled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from cutover_227_helpers import Step

from manta_trading.data.tick.constants import (
    TICK_API_POOL_MAX,
    TICK_INGEST_WORKERS,
    calendar_for_product,
)
from manta_trading.data.tick.universe import TICK_UNIVERSE

ADD_TICK_ENV = Path("deploy/add-tick-env.sh")
SLICE_FILE = "manta-acquisition.slice"
#: systemd's size suffixes are 1024-based.
_SIZE_SUFFIX = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
_MEMORY_MAX = re.compile(r"^\s*MemoryMax\s*=\s*(\d+)\s*([KMGT]?)\s*$", re.MULTILINE)

# -- Connection budget (LLD 234 TD4 step 6) --------------------------------------
#: 231's API budget per worker on the tick cluster: its pool plus one roll
#: snapshot connection per checkout (231 slice design, "Connection budget").
TICK_API_CONNECTIONS = 2 * TICK_API_POOL_MAX
#: 231's extra on the primary per worker: one calendar connection per tick
#: checkout. The 231 design's "Dev values" table records it as 4 ("Added by
#: 231"), which is this constant's value.
PRIMARY_API_EXTRA = TICK_API_POOL_MAX
#: The pass on the tick cluster: its store connection plus the ingest workers.
TICK_PASS_CONNECTIONS = 1 + TICK_INGEST_WORKERS
#: ``TradingCalendar``'s pool ceiling (``data/base/trading_calendar.py``,
#: ``max_size=3``); ingest holds one calendar per calendar id in the universe.
CALENDAR_POOL_MAX = 3
#: The pass on the primary: the pass_runs recorder (one short-lived
#: connection) plus ingest's calendar pools. The design said 1; the code
#: holds the calendars too, so the cutover counts them.
PRIMARY_PASS_CONNECTIONS = 1 + CALENDAR_POOL_MAX * len(
    {calendar_for_product(entry.product) for entry in TICK_UNIVERSE}
)


@dataclass(frozen=True)
class ClusterLoad:
    """One cluster's connection figures, read live."""

    max_connections: int
    superuser_reserved: int
    live: int

    @property
    def headroom(self) -> int:
        return self.max_connections - self.superuser_reserved - self.live


def fits(load: ClusterLoad, needed: int) -> bool:
    return needed <= load.headroom


def budget_line(name: str, load: ClusterLoad, needed: int) -> str:
    return (
        f"{name}: need {needed}; max_connections {load.max_connections} − "
        f"reserved {load.superuser_reserved} − live {load.live} = {load.headroom}"
    )


def serve_has_workers(unit_text: str) -> bool:
    """Whether the installed ``mt-serve`` unit's ExecStart sets ``--workers``."""
    lines = [x for x in unit_text.splitlines() if x.strip().startswith("ExecStart")]
    return any("--workers" in line for line in lines)


# -- Memory bound (issue #33) ---------------------------------------------------------


def memory_max_bytes(slice_text: str) -> int:
    """The slice file's ``MemoryMax`` in bytes (``14G`` → 15032385536)."""
    match = _MEMORY_MAX.search(slice_text)
    if match is None:
        raise ValueError(f"no MemoryMax=<size> in {SLICE_FILE}")
    return int(match.group(1)) * _SIZE_SUFFIX[match.group(2)]


def oom_kills(memory_events: str) -> int:
    """``oom_kill`` from a cgroup's ``memory.events``."""
    for line in memory_events.splitlines():
        name, _, value = line.partition(" ")
        if name == "oom_kill":
            return int(value)
    raise ValueError("memory.events has no oom_kill line")


# -- Settings (LLD 234 TD3) ------------------------------------------------------------


def tick_env_keys(script_text: str) -> list[str]:
    """The keys ``add-tick-env.sh`` copies: its ``KEYS=( … )`` block, the one
    list both the script and this cutover use."""
    match = re.search(r"^KEYS=\((.*?)\)", script_text, re.DOTALL | re.MULTILINE)
    if match is None:
        raise ValueError(f"no KEYS=( … ) in {ADD_TICK_ENV}")
    return match.group(1).split()


def env_key_names(env_text: str) -> set[str]:
    """Names assigned in an env file; values are never returned."""
    names = set()
    for line in env_text.splitlines():
        stripped = line.strip().removeprefix("export ").lstrip()
        name, sep, value = stripped.partition("=")
        if sep and not stripped.startswith("#") and value.strip():
            names.add(name.strip())
    return names


def missing_keys(env_text: str, keys: list[str]) -> list[str]:
    present = env_key_names(env_text)
    return [key for key in keys if key not in present]


def add_tick_env_command(checkout: Path) -> str:
    """The one PM host act (TD3), spelled out for the operator."""
    return f"sudo {checkout / ADD_TICK_ENV} --from {checkout / '.env'}"


# -- Purchase guard (TD4 step 7) ----------------------------------------------------


def purchase_wants(estimate: dict[str, Any]) -> list[str]:
    """Why an estimate-only pass would buy something; empty means nothing.

    Reads the purchase phase of ``mt data tick pass --estimate-only --json``.
    A phase that did not run is a reason too: the guard cannot vouch for it.
    """
    phase = next((p for p in estimate["phases"] if p["name"] == "purchase"), None)
    if phase is None or "verdict" not in phase["summary"]:
        outcome = "absent" if phase is None else phase["outcome"]
        return [f"purchase phase did not plan (outcome {outcome})"]
    summary = phase["summary"]
    reasons = [
        f"request {r['schema']} {r['start']} → {r['end']} ({r['days']} day(s))"
        for r in summary["requests"]
    ]
    planned = Decimal(str(summary["planned_usd"]))
    if planned != 0:
        reasons.append(f"planned cost {planned} USD")
    return reasons


def purchase_count(journal_messages: list[str]) -> int:
    """Jobs the pass submitted, from its rendered ``submitted job`` lines."""
    return sum(1 for m in journal_messages if "submitted job " in m)


# -- Report ----------------------------------------------------------------------------


def render_report(
    steps: list[Step], started: str, outcome: str, preamble: list[str]
) -> str:
    """The cutover report: frontmatter, preamble, one section per step."""
    stamp = started[:10].replace("-", "")
    out = [
        "---",
        "docType: notes",
        "slice: tick-pass-production-wiring",
        "project: trading-data",
        f"dateCreated: {stamp}",
        f"dateUpdated: {stamp}",
        "---",
        "",
        "# Slice 234 cutover report",
        "",
        f"Started {started}. Outcome: **{outcome}**.",
        "",
        *preamble,
    ]
    for step in steps:
        out += [f"## Step {step.number}: {step.title} — {step.status}", ""]
        out += [f"Expected: {step.expected}", "", "Seen:", "", "```"]
        out += [*step.seen, "```", ""]
    return "\n".join(out)
