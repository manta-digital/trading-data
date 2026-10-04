"""``contention``: the tick loop against the Kalshi pass (LLD 226 TD7).

Wires ``contention_run.Run`` to the host: samples from ``/proc``, the Kalshi
timer and unit read through ``systemctl show`` (read only; no unit is ever
started or stopped here), and a tick loop that resets the proof database and
runs ``mt data tick ingest`` again and again. Stopping the loop sends SIGINT
to the ingest, whose pass settles its workers; an interrupted unit's
transaction rolls back. Production is read only: ``pg_stat_database`` in the
sampler and ``pass_runs`` for the Kalshi durations.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from dotenv import dotenv_values

from manta_trading.data.acquisition.pass_runs import PassKind
from proof_226 import contention_sampler as sampler
from proof_226.cli import MT, TICK_DB_URL_ENV, TICK_MAINTENANCE_URL_ENV
from proof_226.common import (
    ENV_FILE,
    ProofSetupError,
    ProofUrls,
    Report,
    load_proof_urls,
)
from proof_226.contention_report import write_report
from proof_226.contention_run import Run
from proof_226.ingest_runs import reset

logger = logging.getLogger(__name__)

KALSHI_TIMER = "mt-kalshi-pass.timer"
KALSHI_SERVICE = "mt-kalshi-pass.service"
PRODUCTION_URL_ENV = "MT_TIMESCALE_DB_URL"
#: How long a stopped ingest gets to settle its workers before SIGKILL.
STOP_GRACE_SECONDS = 120
WEEK = timedelta(days=7)


def _systemctl_show(unit: str, prop: str) -> str:
    return subprocess.run(
        ["systemctl", "show", unit, "-P", prop, "--timestamp=unix"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


class HostProbe:
    def __init__(self, prod: psycopg.Connection[Any], tick: psycopg.Connection[Any]):
        self._prod, self._tick = prod, tick
        self._last = sampler.read(time.monotonic(), prod, tick)

    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def sample(self) -> sampler.Sample:
        reading = sampler.read(time.monotonic(), self._prod, self._tick)
        sample, self._last = sampler.rates(self._last, reading), reading
        return sample

    def next_elapse(self) -> datetime | None:
        raw = _systemctl_show(KALSHI_TIMER, "NextElapseUSecRealtime")
        if not raw.startswith("@"):
            return None
        return datetime.fromtimestamp(int(raw[1:]), UTC)

    def kalshi_active(self) -> bool:
        return _systemctl_show(KALSHI_SERVICE, "ActiveState") in (
            "active",
            "activating",
        )


class TickLoop:
    """Reset and ingest the proof database until stopped (a thread)."""

    def __init__(self, urls: ProofUrls) -> None:
        self._urls = urls
        self._stop = threading.Event()
        #: Orders ``stop()`` against the next ``Popen``: either stop sees the
        #: new process and interrupts it, or the loop sees the stop and exits.
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen[str] | None = None
        self.iterations = 0
        #: Ingests that failed on their own, and a loop that died: either makes
        #: the run's "overlapped" samples untrustworthy, so the report says so.
        #: Read only after ``stop()``, whose ``join`` publishes the thread's writes.
        self.failures: list[str] = []

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        try:
            self._iterate()
        except Exception as exc:  # thread boundary: record it, never die silently
            logger.exception("tick loop died")
            self.failures.append(f"tick loop died: {exc!r}")

    def _iterate(self) -> None:
        env = {
            **os.environ,
            TICK_DB_URL_ENV: self._urls.db_url,
            TICK_MAINTENANCE_URL_ENV: self._urls.maintenance_url,
        }
        while not self._stop.is_set():
            reset(self._urls)
            with self._lock:
                if self._stop.is_set():
                    return
                proc = self._proc = subprocess.Popen(
                    [str(MT), "data", "tick", "ingest", "--json"],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            _, err = proc.communicate()
            if proc.returncode != 0 and not self._stop.is_set():
                last = err.strip().splitlines()[-1:] or ["no stderr"]
                self.failures.append(f"ingest exited {proc.returncode}: {last[0]}")
            else:
                self.iterations += 1

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:  # did not settle: end it outright
                proc.kill()
                proc.wait()
        if self._thread is not None:
            self._thread.join()


def kalshi_durations(
    prod: psycopg.Connection[Any], since: datetime
) -> list[tuple[datetime, float]]:
    rows = prod.execute(
        "SELECT started_at, extract(epoch FROM ended_at - started_at)"
        " FROM pass_runs WHERE pass = %s AND started_at >= %s"
        " AND ended_at IS NOT NULL ORDER BY started_at",
        (PassKind.KALSHI.value, since),
    ).fetchall()
    return [(start, float(seconds)) for start, seconds in rows]


def run() -> Path:
    urls = load_proof_urls()
    prod_url = dotenv_values(ENV_FILE).get(PRODUCTION_URL_ENV)
    if not prod_url:
        raise ProofSetupError(f"{PRODUCTION_URL_ENV} is not set")
    try:
        prod = sampler.production_connection(prod_url)
    except psycopg.OperationalError as exc:  # TD7: production unreachable → no load
        raise ProofSetupError(f"production unreachable: {exc}") from exc
    report = Report(
        "contention", "Proof: contention against the Kalshi pass (slice 226)"
    )
    loop = TickLoop(urls)
    with prod, psycopg.connect(urls.db_url, autocommit=True) as tick:
        week = kalshi_durations(prod, datetime.now(UTC) - WEEK)
        outcome = Run(HostProbe(prod, tick), loop).run()
        runs = kalshi_durations(prod, datetime.now(UTC) - timedelta(hours=4))
    write_report(report, outcome, week, runs, loop.iterations, loop.failures)
    path = report.write()
    if outcome.exit_code:
        raise ProofSetupError(f"contention stopped: {outcome.lines}; report {path}")
    if loop.failures:
        raise ProofSetupError(f"tick loop failed: {loop.failures}; report {path}")
    return path
