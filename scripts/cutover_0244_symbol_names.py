#!/usr/bin/env python3
"""Put v0.24.4 into production: gzip, symbol names, tick plan inclusion.

    uv run python scripts/cutover_0244_symbol_names.py v0.24.4

The release carries two migrations: minute ``065_instruments_name`` (issue
#60) and tick ``tick_008_plan_inclusion`` (slice 300). After both apply, the
names are written from EODHD's symbol lists (``instruments rebuild
--only-names``: no insert, no orphan delete). Run as the operator from the dev
checkout root with the release checked out. Every step is check-then-act;
after a failure, fix the cause and re-run.

Steps
  1. preflight      ref exists here and on origin
  2. hold timers    stop each acquisition timer, then wait out its pass
  3. install        deploy/install-production.sh --ref <ref>
  4. migrate        minute to 065, tick to tick_008 (targets verified)
  5. restart serve  mt-serve onto the new code
  6. names          instruments rebuild --only-names through mt-run
  7. verify         search=apple returns AAPL with its name, gzipped;
                    mt data overview exits 0
  8. release timers started again, as found (skipped after a failed migrate)

The narration goes to project-documents/user/notes/; exit 0 only when every
check passed.
"""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cutover_common import (  # noqa: E402
    NOTES_DIR,
    SERVE_UNIT,
    TICK_URL_KEYS,
    CutoverError,
    install,
    log_text,
    migrate,
    out,
    preflight,
    restart_serve_if_active,
    run,
    say,
    start_log,
    unit_active,
)
from upgrade_production import hold_timers, release_timers  # noqa: E402

MINUTE_MIGRATION = ("minute", "065_instruments_name")
TICK_MIGRATION = ("tick", "tick_008_plan_inclusion")

#: ``mt serve``'s default bind; mt-serve.service passes no --port.
SERVE_URL = "http://127.0.0.1:8100"
SEARCH_PATH = "/api/v1/symbols?search=apple"
EXPECTED = ("AAPL", "Apple Inc.")


def refresh_names() -> dict[str, int]:
    raw = out(
        ["mt-run", "data", "instruments", "rebuild", "--only-names", "--json"],
        sudo=True,
    )
    log_text(raw)
    # Log lines (timestamp first) may share the stream and may hold braces;
    # the summary is the JSON object starting at column 0.
    lines = raw.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("{")), None)
    if start is None:
        raise CutoverError(f"no JSON summary in the name refresh output: {raw[-500:]}")
    summary: dict[str, int] = json.loads("\n".join(lines[start:]))
    if summary["named"] == 0:
        raise CutoverError(f"no instrument has a name after the refresh: {summary}")
    return summary


def verify_search() -> None:
    request = urllib.request.Request(
        SERVE_URL + SEARCH_PATH, headers={"Accept-Encoding": "gzip"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        encoding = response.headers.get("Content-Encoding")
        body = response.read()
    if encoding != "gzip":
        raise CutoverError(f"{SEARCH_PATH}: Content-Encoding {encoding!r}, not gzip")
    rows = json.loads(gzip.decompress(body))["symbols"]
    found = [(row["symbol"], row["name"]) for row in rows]
    if EXPECTED not in found:
        raise CutoverError(f"{SEARCH_PATH}: {EXPECTED} not in {found[:5]}")
    print(f"    {SEARCH_PATH} -> {found[:3]} (gzip)")


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
        say(f"1/8 preflight — {ref}")
        commit = preflight(ref)

        say("2/8 hold the acquisition timers")
        held = hold_timers()

        say(f"3/8 install {ref}")
        version = install(ref, commit)
        if ref.lstrip("v") not in version:
            raise CutoverError(f"installed mt reports {version!r}, expected {ref}")

        say("4/8 migrate")
        release = False  # a half-migrated host keeps its timers stopped
        migrate(*MINUTE_MIGRATION)
        migrate(*TICK_MIGRATION, keys=TICK_URL_KEYS)
        release = True

        say(f"5/8 restart {SERVE_UNIT}")
        restart_serve_if_active()
        if not unit_active(SERVE_UNIT):
            raise CutoverError(f"{SERVE_UNIT} is not active after the restart")

        say("6/8 instrument names from EODHD")
        summary = refresh_names()
        print(f"    {summary}")

        say("7/8 verify")
        verify_search()
        result = run(["mt-run", "data", "overview"], sudo=True, check=False)
        log_text(result.stdout or "")
        if result.returncode != 0:
            log_text(result.stderr or "")
            raise CutoverError(f"mt data overview exited {result.returncode}")
        print("    mt data overview renders")
    except (CutoverError, subprocess.CalledProcessError, OSError) as exc:
        say(f"FAILED: {exc}")
        if not release:
            say(f"timers left stopped after a failed migrate: {held}")
        return 1
    finally:
        if release:
            say("8/8 release the timers")
            release_timers(held)

    say(f"PASS — production is at {ref} ({commit[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
