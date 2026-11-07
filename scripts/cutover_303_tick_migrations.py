#!/usr/bin/env python3
"""Put the slice 302/303 release into production: tick bars and statistics.

    uv run python scripts/cutover_303_tick_migrations.py <release-tag>

The release carries two tick migrations: ``tick_009_ohlcv_1m`` (slice 302) and
``tick_010_statistics`` (slice 303); the tick track applies in order up to the
second. It also ships coarse Kalshi candles (``period_minutes``, issue 61) and
half-open Kalshi windows. Run as the operator from the dev checkout root with
the release checked out. Every step is check-then-act; after a failure, fix the
cause and re-run.

Steps
  1. preflight      ref exists here and on origin
  2. hold timers    stop each acquisition timer, then wait out its pass
  3. install        deploy/install-production.sh --ref <ref>
  4. migrate        tick to tick_010 (target verified)
  5. restart serve  mt-serve onto the new code
  6. verify         the live API publishes ``period_minutes`` on the Kalshi
                    candlesticks route; mt data tick status and mt data
                    overview exit 0
  7. release timers started again, as found (skipped after a failed migrate)

The narration goes to project-documents/user/notes/; exit 0 only when every
check passed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
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
    preflight,
    restart_serve_if_active,
    run,
    say,
    start_log,
    unit_active,
)
from upgrade_production import hold_timers, release_timers  # noqa: E402

TICK_MIGRATION = ("tick", "tick_010_statistics")

#: ``mt serve``'s default bind; mt-serve.service passes no --port.
SERVE_URL = "http://127.0.0.1:8100"
CANDLES_ROUTE = "/api/v1/kalshi/markets/{ticker}/candlesticks"
PERIOD_PARAM = "period_minutes"
#: mt-serve is active before it listens; the restart returns first.
SERVE_UP_ATTEMPTS = 30
SERVE_UP_INTERVAL_SECONDS = 2


def _fetch_openapi() -> bytes:
    """The live spec, waiting for the restarted server to start listening."""
    for _ in range(SERVE_UP_ATTEMPTS):
        try:
            with urllib.request.urlopen(
                SERVE_URL + "/openapi.json", timeout=30
            ) as response:
                body: bytes = response.read()
            return body
        except urllib.error.URLError:
            # Connection refused while the new process binds its port; any
            # other failure surfaces once the attempts run out.
            time.sleep(SERVE_UP_INTERVAL_SECONDS)
    raise CutoverError(f"{SERVE_URL} did not answer after the restart")


def verify_period_param() -> None:
    spec = json.loads(_fetch_openapi())
    params = spec["paths"][CANDLES_ROUTE]["get"]["parameters"]
    if PERIOD_PARAM not in [param["name"] for param in params]:
        raise CutoverError(f"{CANDLES_ROUTE} does not publish {PERIOD_PARAM!r}")
    print(f"    {CANDLES_ROUTE} publishes {PERIOD_PARAM}")


def verify_commands() -> None:
    for command in (["data", "tick", "status"], ["data", "overview"]):
        result = run(["mt-run", *command], sudo=True, check=False)
        log_text(result.stdout or "")
        if result.returncode != 0:
            log_text(result.stderr or "")
            raise CutoverError(f"mt {' '.join(command)} exited {result.returncode}")
        print(f"    mt {' '.join(command)} exits 0")


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
        migrate(*TICK_MIGRATION, keys=TICK_URL_KEYS)
        release = True

        say(f"5/7 restart {SERVE_UNIT}")
        restart_serve_if_active()
        if not unit_active(SERVE_UNIT):
            raise CutoverError(f"{SERVE_UNIT} is not active after the restart")

        say("6/7 verify")
        verify_period_param()
        verify_commands()
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
