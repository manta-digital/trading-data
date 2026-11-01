#!/usr/bin/env python3
"""Put the Kalshi title search (#63) into production.

    uv run python scripts/cutover_063_kalshi_search.py <ref>

The release carries kalshi migration ``kalshi_009_search``: ``pg_trgm`` and six
GIN indexes over series, events and open markets. Run as the operator from the
dev checkout root with the release checked out. Every step is check-then-act;
after a failure, fix the cause and re-run.

Steps
  1. preflight      ref exists here and on origin
  2. hold timers    stop each acquisition timer, then wait out its pass
  3. install        deploy/install-production.sh --ref <ref>
  4. migrate        kalshi track to kalshi_009 (target verified); builds the
                    indexes, which blocks catalog writes until they finish
  5. restart serve  mt-serve onto the new code
  6. verify         /api/v1/kalshi/search?q=<word> returns a series, an event
                    and a market, each with its parents
  7. release        timers started again, as found (skipped after a failed migrate)

The narration goes to project-documents/user/notes/; exit 0 only when every
check passed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cutover_common import (  # noqa: E402
    NOTES_DIR,
    SERVE_UNIT,
    CutoverError,
    install,
    migrate,
    preflight,
    restart_serve_if_active,
    say,
    start_log,
    unit_active,
)
from upgrade_production import hold_timers, release_timers  # noqa: E402

KALSHI_MIGRATION = ("kalshi", "kalshi_009_search")

#: ``mt serve``'s default bind; mt-serve.service passes no --port.
SERVE_URL = "http://127.0.0.1:8100"
#: A word every Kalshi catalog carries at all three levels.
SEARCH_WORD = "bitcoin"
#: A search that takes longer than this on the real catalog has lost its index.
SLOW_SECONDS = 2.0


def verify_search() -> None:
    url = f"{SERVE_URL}/api/v1/kalshi/search?" + urllib.parse.urlencode(
        {"q": SEARCH_WORD}
    )
    started = time.monotonic()
    with urllib.request.urlopen(url, timeout=30) as response:
        body = json.loads(response.read())
    elapsed = time.monotonic() - started
    for group, parent in (
        ("series", "ticker"),
        ("events", "series_ticker"),
        ("markets", "event_ticker"),
    ):
        if not body[group] or not body[group][0][parent]:
            raise CutoverError(f"search {SEARCH_WORD!r}: no usable {group} hit")
    if elapsed > SLOW_SECONDS:
        raise CutoverError(f"search {SEARCH_WORD!r} took {elapsed:.1f}s")
    print(
        f"    search {SEARCH_WORD!r} -> {len(body['series'])} series, "
        f"{len(body['events'])} events, {len(body['markets'])} markets "
        f"in {elapsed:.2f}s"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    ref = argv[1]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log = start_log(NOTES_DIR / f"cutover-{ref}-{stamp}.log")
    print(f"narration -> {log}")

    held: list[str] = []
    release = True
    try:
        say(f"1/7 preflight — {ref}")
        commit = preflight(ref)

        say("2/7 hold the acquisition timers")
        held = hold_timers()

        say(f"3/7 install {ref}")
        version = install(ref, commit)
        if ref.lstrip("v") not in version:
            raise CutoverError(f"installed mt reports {version!r}, expected {ref}")

        say("4/7 migrate")
        release = False  # a half-migrated host keeps its timers stopped
        migrate(*KALSHI_MIGRATION)
        release = True

        say(f"5/7 restart {SERVE_UNIT}")
        restart_serve_if_active()
        if not unit_active(SERVE_UNIT):
            raise CutoverError(f"{SERVE_UNIT} is not active after the restart")

        say("6/7 verify")
        verify_search()
    except (CutoverError, subprocess.CalledProcessError, OSError) as exc:
        say(f"FAILED: {exc}")
        if not release:
            say(f"timers left stopped after a failed migrate: {held}")
        return 1
    finally:
        if release:
            say("7/7 release the timers")
            release_timers(held)

    say(f"PASS — production is at {ref} ({commit[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
