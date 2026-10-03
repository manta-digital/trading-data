"""Run the shipped ``mt`` CLI against the proof database (LLD 226 TD2).

The child gets this process's environment with the two tick URLs replaced by
the proof database's, so ``mt`` (which reads ``.env`` itself) talks to
``trading_tick_proof``. Environment variables outrank ``.env`` in the
settings loader, so the production tick URLs in ``.env`` are never used.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from proof_226.common import ProofUrls

MT = Path(sys.executable).parent / "mt"
TICK_DB_URL_ENV = "MT_TICK_DB_URL"
TICK_MAINTENANCE_URL_ENV = "MT_TICK_MAINTENANCE_URL"


class CliStepError(RuntimeError):
    """An ``mt`` command exited with a code the step does not accept."""


@dataclass(frozen=True)
class CliRun:
    args: tuple[str, ...]
    exit_code: int
    seconds: float
    payload: Any


def run_mt(urls: ProofUrls, *args: str, accept: tuple[int, ...] = (0,)) -> CliRun:
    """``mt <args> --json`` on the proof database; the parsed JSON and timing."""
    env = {
        **os.environ,
        TICK_DB_URL_ENV: urls.db_url,
        TICK_MAINTENANCE_URL_ENV: urls.maintenance_url,
    }
    command = [str(MT), *args, "--json"]
    started = time.monotonic()
    done = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    seconds = time.monotonic() - started
    if done.returncode not in accept:
        raise CliStepError(
            f"mt {' '.join(args)} exited {done.returncode}:\n"
            f"{done.stdout[-4000:]}\n{done.stderr[-4000:]}"
        )
    return CliRun(tuple(args), done.returncode, seconds, json.loads(done.stdout))


def phase(payload: dict[str, Any], name: str) -> dict[str, Any]:
    """One phase's entry of a pass/ingest ``--json`` payload; absent raises."""
    for entry in payload["phases"]:
        if entry["name"] == name:
            return dict(entry)
    raise CliStepError(f"no {name!r} phase in the pass payload")
