#!/usr/bin/env python3
"""Cut slice 267 (Kalshi historical backfill phase) over to production.

Runbook 100's *Update procedure* for this release, then the first supervised
firing(s), then the completion record — everything in task file
``267-tasks.historical-backfill-phase-2.md`` Section 11 between the Project
Manager's two manual acts: tagging the release before, reading the report
after.

    uv run python scripts/cutover_267_historical.py v0.12.0

Run it as the operator from the dev checkout root (the migration uses this
checkout's ``.env`` maintenance credential). ``sudo`` is used for the
privileged steps. Every step is check-then-act; after a failure fix the
cause and re-run. The timer is released again no matter how the run ends.

Steps
  1. hold the timer       wait out a running pass; stop mt-kalshi-pass.timer
  2. install the ref      deploy/install-production.sh --ref <ref>
  3. migrate              kalshi_007_historical_surface; `status` on the new binary
  4. first firing         sudo mt-run kalshi, streamed. This firing walks the
                          market archive into the catalog — hours. If the cap
                          stops the walk (cursor saved), the script fires again
                          and reads the rest of the report from that firing.
  5. report               Section 11's checks -> user/notes/<date>-267-cutover.md
  6. release the timer

The report parsing is pure (``parse_firing`` / ``evaluate``) and unit-tested
in ``test/unit/test_cutover_267.py`` against a journal excerpt.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from cutover_common import (
    NOTES_DIR,
    CutoverError,
    Firing,
    fire,
    hold_timer,
    install,
    migrate,
    preflight,
    production_status,
    read_journal,
    release_timer,
    restart_serve_if_active,
    say,
    unit_result,
)

from manta_trading.data.kalshi.constants import (
    HISTORICAL_CANDLE_MARKETS_PER_PASS,
    HISTORICAL_PHASE_MINUTES,
    HISTORICAL_SLOW_MARKET_SECONDS,
    KALSHI_AUTHENTICATED_RATE_LIMIT,
    KALSHI_MODE_AUTHENTICATED,
)

MIGRATION_ID = "kalshi_007_historical_surface"
#: Criterion 6: a firing after the archive walk stays under this.
PASS_BOUND = timedelta(minutes=45)
#: Decision 2's authenticated figures, derived from the same constants.
EXPECTED_BUDGET = KALSHI_AUTHENTICATED_RATE_LIMIT.requests_per_minute
EXPECTED_CAP = EXPECTED_BUDGET * HISTORICAL_PHASE_MINUTES

# The journal lines this slice emits (historical_sync.py and friends).
CLIENT_LINE = re.compile(r"kalshi client mode=(?P<mode>\S+) budget=(?P<budget>\d+)/min")
CAP_LINE = re.compile(r"kalshi historical cap=(?P<cap>\d+) \((?P<budget>\d+)/min")
ARCHIVE_DONE = re.compile(
    r"archive walk done pages=(?P<pages>\d+) markets=(?P<markets>\d+) "
    r"written=(?P<written>\d+)"
)
ARCHIVE_CAPPED = re.compile(r"cap reached during the archive walk pages=(?P<pages>\d+)")
FIRST_RUN = re.compile(
    r"historical first run: tape from live floor (?P<live>\S+) down to (?P<floor>\S+)"
)
WINDOW = re.compile(
    r"historical window (?P<start>\S+)→(?P<end>\S+) pages (?P<pages>\d+) "
    r"fetched (?P<fetched>\d+) written (?P<written>\d+) unknown (?P<unknown>\d+) "
    r"excluded (?P<excluded>\d+)"
)
SLOW_MARKET = re.compile(
    r"historical slow market (?P<ticker>\S+): (?P<seconds>[\d.]+)s"
)
SKIPPED = re.compile(r"historical: (?P<ticker>\S+) skipped — (?P<reason>.*)")
PASS_FINISHED = re.compile(
    r"kalshi pass finished outcome=(?P<outcome>\S+) duration=(?P<ms>\d+) ms "
    r"phases: (?P<phases>.*)"
)
HTTP_429 = "kalshi HTTP 429"
FIRST_ATTEMPT = "attempt 1/"


@dataclass
class Facts:
    """What one firing's journal says — parsed once, judged in ``evaluate``."""

    mode: str | None = None
    budget: int | None = None
    cap: int | None = None
    archive_done: dict[str, int] | None = None
    archive_capped_pages: int | None = None
    first_run: tuple[str, str] | None = None
    windows: list[dict[str, Any]] = field(default_factory=list)
    slow_markets: list[tuple[str, float]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    outcome: str | None = None
    phases: str = ""
    duration_ms: int | None = None
    http_429: int = 0
    http_429_escalated: int = 0


def parse_firing(firing: Firing) -> Facts:
    facts = Facts()
    if client := firing.first(CLIENT_LINE):
        facts.mode, facts.budget = client["mode"], int(client["budget"])
    if cap := firing.first(CAP_LINE):
        facts.cap = int(cap["cap"])
    if done := firing.first(ARCHIVE_DONE):
        facts.archive_done = {k: int(v) for k, v in done.groupdict().items()}
    if capped := firing.first(ARCHIVE_CAPPED):
        facts.archive_capped_pages = int(capped["pages"])
    if first := firing.first(FIRST_RUN):
        facts.first_run = (first["live"], first["floor"])
    previous: datetime | None = None
    for ts, message in firing.entries:
        if "historical phase started" in message:
            previous = ts
        if window := WINDOW.search(message):
            seconds = (ts - previous).total_seconds() if previous else 0.0
            pages = int(window["pages"])
            facts.windows.append(
                {
                    **window.groupdict(),
                    "seconds": seconds,
                    "per_page": seconds / pages if pages else 0.0,
                }
            )
            previous = ts
        if slow := SLOW_MARKET.search(message):
            facts.slow_markets.append((slow["ticker"], float(slow["seconds"])))
        if skipped := SKIPPED.search(message):
            facts.skipped.append(skipped["ticker"])
    if finished := firing.first(PASS_FINISHED):
        facts.outcome = finished["outcome"]
        facts.phases = finished["phases"]
        facts.duration_ms = int(finished["ms"])
    lines_429 = firing.find(HTTP_429)
    facts.http_429 = len(lines_429)
    facts.http_429_escalated = sum(1 for _, m in lines_429 if FIRST_ATTEMPT not in m)
    return facts


def evaluate(
    facts: Facts,
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    walk_firing: bool,
) -> list[tuple[bool, str]]:
    """Section 11's checks over one firing's facts and the ``status --json``
    payloads taken before and after it. ``walk_firing`` marks the firing
    that did the archive walk: its duration and catalog growth are reported,
    not bounded."""
    checks: list[tuple[bool, str]] = []

    def add(ok: bool, text: str) -> None:
        checks.append((ok, text))

    add(
        facts.mode == KALSHI_MODE_AUTHENTICATED and facts.budget == EXPECTED_BUDGET,
        f"client mode={facts.mode} budget={facts.budget}/min "
        f"(expected {KALSHI_MODE_AUTHENTICATED} {EXPECTED_BUDGET}/min — Decision 2)",
    )
    add(facts.cap == EXPECTED_CAP, f"cap={facts.cap} (expected {EXPECTED_CAP})")
    if facts.archive_done:
        d = facts.archive_done
        add(
            True,
            f"archive walk done: pages {d['pages']:,} · markets {d['markets']:,} · "
            f"written {d['written']:,}",
        )
    elif facts.archive_capped_pages is not None:
        add(
            True,
            f"archive walk capped after {facts.archive_capped_pages:,} pages; cursor "
            "saved — the remaining checks read from the next firing",
        )
    else:
        add(
            (after.get("historical") or {}).get("archive_walked") is True,
            "archive already walked before this firing "
            "(status.historical.archive_walked)",
        )
    historical_ok = (
        "historical=ok" in facts.phases or "historical=partial" in facts.phases
    )
    add(
        "catalog=ok" in facts.phases
        and "candles=ok" in facts.phases
        and "trades=ok" in facts.phases
        and historical_ok,
        f"phases: {facts.phases or '(no pass finished line)'}"
        + (f" — skipped: {', '.join(facts.skipped)}" if facts.skipped else ""),
    )
    hist_after = after.get("historical") or {}
    if facts.first_run:
        live, floor = facts.first_run
        add(
            hist_after.get("tape_to") == live and hist_after.get("floor") == floor,
            f"historical row seeded at the live floor {live} with the floor "
            f"target {floor}",
        )
    else:
        add(
            hist_after.get("tape_from") is not None,
            "historical row present (seeded on an earlier firing)",
        )
    tape_from, tape_to = hist_after.get("tape_from"), hist_after.get("tape_to")
    descended = "?"
    if tape_from and tape_to:
        hours = datetime.fromisoformat(tape_to) - datetime.fromisoformat(tape_from)
        descended = f"{hours.total_seconds() / 3600:.2f}"
    add(
        bool(facts.windows) and descended != "?" and float(descended) >= 1.0,
        f"watermark descended {descended} h over {len(facts.windows)} windows "
        f"({tape_to} → {tape_from})",
    )
    remaining_before = (before.get("candles") or {}).get("behind_cutoff_uncollected")
    remaining_after = (after.get("candles") or {}).get("behind_cutoff_uncollected")
    completed = None
    if remaining_before is not None and remaining_after is not None:
        completed = remaining_before - remaining_after
    add(
        completed is not None and 0 <= completed <= HISTORICAL_CANDLE_MARKETS_PER_PASS,
        f"behind cutoff, uncollected {remaining_before} → {remaining_after} "
        f"(completed {completed}; per-pass ceiling "
        f"{HISTORICAL_CANDLE_MARKETS_PER_PASS:,}; Criterion 5)"
        + (" — the walk's firing grows the set first" if walk_firing else ""),
    )
    add(
        facts.http_429_escalated == 0,
        f"HTTP 429s {facts.http_429}, none past attempt 1: "
        f"{facts.http_429_escalated == 0}",
    )
    minutes = (facts.duration_ms or 0) / 60_000
    if walk_firing:
        add(
            facts.duration_ms is not None,
            f"walk firing duration {minutes:.1f} min (reported, not bounded); "
            f"catalog markets {before.get('markets', '?')} → "
            f"{after.get('markets', '?')}",
        )
    else:
        add(
            facts.duration_ms is not None
            and facts.duration_ms < PASS_BOUND.total_seconds() * 1000,
            f"pass duration {minutes:.1f} min < "
            f"{PASS_BOUND.total_seconds() / 60:.0f} min (Criterion 6)",
        )
    if facts.windows:
        slowest = max(facts.windows, key=lambda w: w["seconds"])
        add(
            True,
            f"slowest window {slowest['seconds']:.0f} s for {slowest['pages']} pages "
            f"({slowest['per_page']:.2f} s/page) {slowest['start']}→{slowest['end']}",
        )
    else:
        add(False, "no `historical window` lines in this firing")
    if facts.slow_markets:
        ticker, seconds = max(facts.slow_markets, key=lambda pair: pair[1])
        add(
            False,
            f"slowest market {ticker} {seconds:.1f} s "
            f"(over {HISTORICAL_SLOW_MARKET_SECONDS} s)",
        )
    else:
        add(True, f"no market over {HISTORICAL_SLOW_MARKET_SECONDS} s")
    return checks


def render_report(
    ref: str,
    commit: str,
    version: str,
    firings: list[tuple[str, Facts, list[tuple[bool, str]], str]],
    status_after: dict[str, Any],
) -> tuple[str, bool]:
    all_ok = all(ok for _, _, checks, _ in firings for ok, _ in checks)
    today = datetime.now(UTC).strftime("%Y%m%d")
    lines = [
        "---",
        "docType: note",
        "project: trading-data",
        "slice: 267-slice.historical-backfill-phase",
        f"dateCreated: {today}",
        f"dateUpdated: {today}",
        f"status: {'complete' if all_ok else 'in_progress'}",
        "---",
        "",
        "# Slice 267 cutover — first supervised firing(s) on manta9000",
        "",
        f"Generated by `scripts/cutover_267_historical.py` at "
        f"{datetime.now(UTC).isoformat(timespec='seconds')}.",
        "",
        "## Host migrated",
        "",
        f"- installed ref `{ref}` = `{commit}`; `{version}`",
        f"- `{MIGRATION_ID}` applied; kalshi track 0 pending",
        "",
    ]
    for label, facts, checks, started in firings:
        lines += [f"## {label} (started {started})", ""]
        lines += [f"- {'✅' if ok else '❌'} {text}" for ok, text in checks]
        lines += [
            "",
            "| window | pages | seconds | s/page | fetched | written | unknown |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        lines += [
            f"| {w['start']}→{w['end']} | {w['pages']} | {w['seconds']:.0f} | "
            f"{w['per_page']:.2f} | {int(w['fetched']):,} | {int(w['written']):,} | "
            f"{int(w['unknown']):,} |"
            for w in facts.windows
        ]
        lines.append("")
    lines += [
        "## `mt-run data kalshi status --json` → `.historical` and `.trades` after",
        "",
        "```json",
        json.dumps(
            {
                "historical": status_after.get("historical"),
                "trades": status_after.get("trades"),
            },
            indent=2,
        ),
        "```",
        "",
        "## Handoff — the descent to the floor (not a task)",
        "",
        "The status line's tape range grows downward ~one cap of hours per hourly "
        "firing (~15 authenticated firings) and `behind cutoff, uncollected` reaches "
        "0 within the first nine. A watermark that has not moved across firings while "
        "`floor reached` is absent is the signal to act on (runbook 100, *Kalshi*).",
    ]
    return "\n".join(lines) + "\n", all_ok


def supervised_firing(
    label: str, *, walk_firing: bool
) -> tuple[tuple[str, Facts, list[tuple[bool, str]], str], Facts]:
    before = production_status()
    cursor, started = fire()
    result, exit_status = unit_result()
    firing = read_journal(cursor)
    facts = parse_firing(firing)
    after = production_status()
    checks = [
        (
            result == "success" and exit_status in {"0", "3"},
            f"unit Result={result} ExecMainStatus={exit_status}",
        )
    ]
    checks += evaluate(facts, before=before, after=after, walk_firing=walk_firing)
    return (label, facts, checks, started), facts


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in {"-h", "--help"}:
        print(__doc__)
        return 2
    ref = argv[1]
    say(f"preflight: ref {ref}")
    commit = preflight(ref)
    timer_was_active = False
    try:
        say("1/6 hold the timer")
        timer_was_active = hold_timer()
        say(f"2/6 install {ref}")
        version = install(ref, commit)
        say(f"3/6 migrate ({MIGRATION_ID})")
        migrate(MIGRATION_ID)
        status = production_status()
        print(f"    status on the new binary: historical={status.get('historical')}")
        restart_serve_if_active()
        say("4/6 first supervised firing (the archive walk — hours)")
        firings: list[tuple[str, Facts, list[tuple[bool, str]], str]] = []
        first, facts = supervised_firing(
            "First firing — the archive walk", walk_firing=True
        )
        firings.append(first)
        fired = 1
        while facts.archive_capped_pages is not None and fired < 4:
            fired += 1
            say(f"4/6 firing {fired}: the walk was capped — resuming it")
            more, facts = supervised_firing(
                f"Firing {fired} — walk resumed", walk_firing=True
            )
            firings.append(more)
        if (
            facts.archive_done is None
            and (production_status().get("historical") or {}).get("archive_walked")
            is not True
        ):
            raise CutoverError(
                "the archive walk did not finish in four firings; read the journal"
            )
        say(
            f"4/6 firing {fired + 1}: the first after the walk "
            "(Criterion 6 is measured here)"
        )
        measured, _ = supervised_firing(
            f"Firing {fired + 1} — first after the walk", walk_firing=False
        )
        firings.append(measured)
        say("5/6 report")
        report, all_ok = render_report(
            ref, commit, version, firings, production_status()
        )
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        path = NOTES_DIR / f"{datetime.now(UTC).strftime('%Y-%m-%d')}-267-cutover.md"
        path.write_text(report)
        print(report)
        print(f"    written to {path}")
    finally:
        say("6/6 release the timer")
        release_timer(timer_was_active)
    return 0 if all_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except CutoverError as exc:
        print(f"\ncutover stopped: {exc}", file=sys.stderr)
        sys.exit(1)
