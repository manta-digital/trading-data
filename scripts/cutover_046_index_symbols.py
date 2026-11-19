#!/usr/bin/env python3
"""Put the index-symbols fix into production (issue #46).

    uv run python scripts/cutover_046_index_symbols.py vX.Y.Z

The release carries minute migration ``067_index_instruments_indx_symbols``:
the 904 EODHD index instruments take their ``{code}.INDX`` symbol and the US
equity calendar. Bars are never moved; the equities that share a code (DJT,
MID, …) keep their rows. Run as the operator from the dev checkout root with
the release checked out. Every step is check-then-act; the migration is
idempotent, so after a failure fix the cause and re-run.

Steps
  1. preflight      ref exists here and on origin
  2. hold timers    stop each acquisition timer, then wait out its pass
  3. install        deploy/install-production.sh --ref <ref>
  4. migrate        minute to 067 (target verified)
  5. restart serve  mt-serve onto the new code
  6. verify         VIX.INDX and DJT.INDX are indices; DJT is still the
                    equity with its daily range; mt data overview exits 0
  7. release timers started again, as found (skipped after a failed migrate)

The narration goes to project-documents/user/notes/; exit 0 only when every
check passed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cutover_common import (  # noqa: E402
    NOTES_DIR,
    SERVE_UNIT,
    SERVE_URL,
    CutoverError,
    install,
    log_text,
    migrate,
    preflight,
    restart_serve_if_active,
    run,
    say,
    start_log,
    unit_active,
    wait_for_serve,
)
from upgrade_production import hold_timers, release_timers  # noqa: E402

MINUTE_MIGRATION = ("minute", "067_index_instruments_indx_symbols")

INDEX_SYMBOLS = ("VIX.INDX", "DJT.INDX")
#: An equity that shares its code with an index, and the first day of its
#: daily bars as served before the release (read 2026-11-19).
SHARED_EQUITY = ("DJT", "2021-09-30")


def _symbol(symbol: str) -> dict[str, Any]:
    path = "/api/v1/symbols/" + urllib.parse.quote(symbol, safe="")
    with urllib.request.urlopen(SERVE_URL + path, timeout=30) as response:
        detail: dict[str, Any] = json.loads(response.read())
    return detail


def verify_symbols() -> None:
    for symbol in INDEX_SYMBOLS:
        detail = _symbol(symbol)
        if detail["asset_class"] != "index":
            raise CutoverError(f"{symbol}: asset_class {detail['asset_class']!r}")
        print(f"    {symbol} -> index, {detail['name']!r}")
    code, first_day = SHARED_EQUITY
    detail = _symbol(code)
    served_first = detail["available"].get("1d", {}).get("start")
    if detail["asset_class"] != "equity" or served_first != first_day:
        raise CutoverError(
            f"{code}: asset_class {detail['asset_class']!r}, daily from"
            f" {served_first!r}; expected the equity from {first_day}"
        )
    print(f"    {code} -> equity, daily from {served_first} (unchanged)")


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
        migrate(*MINUTE_MIGRATION)
        release = True

        say(f"5/7 restart {SERVE_UNIT}")
        restart_serve_if_active()
        if not unit_active(SERVE_UNIT):
            raise CutoverError(f"{SERVE_UNIT} is not active after the restart")
        wait_for_serve()

        say("6/7 verify")
        verify_symbols()
        result = run(["mt-run", "data", "overview"], sudo=True, check=False)
        log_text(result.stdout or "")
        if result.returncode != 0:
            log_text(result.stderr or "")
            raise CutoverError(f"mt data overview exited {result.returncode}")
        print("    mt data overview renders")
    except (CutoverError, subprocess.CalledProcessError, OSError, KeyError) as exc:
        say(f"FAILED: {exc!r}")
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
