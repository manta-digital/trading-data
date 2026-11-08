"""Judgements and evidence helpers for the slice 304 cutover (D3, D6, D7).

Mostly pure functions; ``scripts/cutover_304_first_purchase.py`` calls them from
its numbered steps. The one subprocess call is ``run_session_check``.
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cutover_common import (  # noqa: E402
    CutoverError,
    env_value,
    verify_same_database,
)

from manta_trading.data.tick.constants import TickSchema, UnitState  # noqa: E402
from manta_trading.data.tick.ingest_checks import IngestCheck  # noqa: E402
from manta_trading.data.tick.universe import TICK_UNIVERSE  # noqa: E402

PLAN_KEY = "MT_TICK_DATABENTO_PLAN"
PER_PASS_KEY = "MT_TICK_SPEND_CEILING_USD"
CAP_30D_KEY = "MT_TICK_SPEND_30D_CEILING_USD"
CEILING_PER_PASS_USD = "7"
CEILING_CAP_30D_USD = "16"
CEILING_SCRIPT = (
    Path(__file__).resolve().parents[1] / "deploy/set-tick-spend-ceilings.sh"
)
CEILING_COMMAND = (
    f"sudo {CEILING_SCRIPT} --per-pass {CEILING_PER_PASS_USD}"
    f" --cap-30d {CEILING_CAP_30D_USD}"
)
TICK_DB_URL_KEY = "MT_TICK_DB_URL"
VERDICT_ALLOWED = "allowed"
VERDICT_NOTHING = "nothing_to_buy"
ARCHIVE_ROOT = Path("/data/tick-archive")
SESSION_CALENDAR = "CME_EQUITY"
VERIFY_SESSIONS_SCRIPT = Path(__file__).resolve().parent / "verify_cme_sessions.py"
# Exit codes of verify_cme_sessions.py.
SESSIONS_CLEAN, SESSIONS_OUTSIDE, SESSIONS_STOPPED = 0, 1, 2
UNCLASSIFIED = "unclassified"
UNDEFINED_WINDOW_TEXT = "has no validity window"


class Estimate(StrEnum):
    BUY = "buy"
    NOTHING_TO_BUY = "nothing_to_buy"


class FindingClass(StrEnum):
    """D6's classes of a unit that did not ingest."""

    AWAITING_DEFINITIONS = "awaiting definitions"
    STAT_REFERENCE = "stat_reference"
    SESSION_BOUNDARY = "session_boundary"
    UNDEFINED_WINDOW = "undefined definition window"
    UNCLASSIFIED = UNCLASSIFIED


class SessionCheck(StrEnum):
    PASS = "pass"
    FINDING = "finding"


def wanted_keys() -> frozenset[str]:
    """``product/schema`` for every history want, as the plan's tallies key them."""
    return frozenset(
        f"{entry.product}/{want.schema.value}"
        for entry in TICK_UNIVERSE
        for want in entry.history
    )


def _ceiling(text: str, key: str) -> Decimal:
    try:
        value = env_value(text, key)
    except CutoverError:
        value = ""  # absent is reported below with the command, same as empty
    if not value:
        raise CutoverError(
            f"{key} is not set in the env file; the PM runs: {CEILING_COMMAND}"
        )
    return Decimal(value)


def check_env_text(text: str) -> Decimal:
    """Refuse a configured plan or a missing ceiling; return the per-pass ceiling."""
    try:
        plan = env_value(text, PLAN_KEY)
    except CutoverError:
        plan = ""  # absent is the wanted state: every request is usage-billed
    if plan:
        raise CutoverError(f"{PLAN_KEY} is set; this purchase assumes usage billing")
    per_pass = _ceiling(text, PER_PASS_KEY)
    _ceiling(text, CAP_30D_KEY)
    return per_pass


def check_checkout_database(checkout_env: str, production_env: str) -> str:
    """The reconcile script reads the checkout's tick URL; it must be production's."""
    return verify_same_database(checkout_env, production_env, TICK_DB_URL_KEY)


def _decimal(summary: Mapping[str, Any], key: str) -> Decimal:
    value = summary[key]
    if value is None:
        raise CutoverError(f"estimate: {key} is unset")
    return Decimal(value)


def _check_scope(summary: Mapping[str, Any]) -> None:
    wanted = wanted_keys()
    wanted_schemas = {key.split("/", 1)[1] for key in wanted}
    for key in summary["wanted_days"]:
        if key not in wanted:
            raise CutoverError(f"estimate: days wanted for {key}, outside D1's wants")
    for req in summary["requests"]:
        label = f"{req['schema']} {req['start']}..{req['end']}"
        if req["schema"] not in wanted_schemas:
            raise CutoverError(f"estimate: request {label} is outside D1's wants")
        if req["resubmit"]:
            raise CutoverError(f"estimate: request {label} is a resubmit")


def judge_estimate(summary: Mapping[str, Any]) -> Estimate:
    """Raise ``CutoverError`` naming the cause unless the plan may be bought."""
    _check_scope(summary)
    if summary["verdict"] == VERDICT_NOTHING:
        return Estimate.NOTHING_TO_BUY
    planned = _decimal(summary, "planned_usd")
    per_pass = _decimal(summary, "per_pass_ceiling_usd")
    cap_30d = _decimal(summary, "cap_30d_usd")
    trailing = _decimal(summary, "trailing_30d_usd")
    if planned > per_pass:
        raise CutoverError(
            f"estimate: planned {planned} exceeds the per-pass {per_pass}"
        )
    if planned + trailing > cap_30d:
        raise CutoverError(
            f"estimate: planned {planned} + trailing {trailing}"
            f" exceeds the 30-day {cap_30d}"
        )
    if summary["verdict"] != VERDICT_ALLOWED:
        raise CutoverError(
            f"estimate: verdict {summary['verdict']}: {summary['reasons']}"
        )
    return Estimate.BUY


def classify_unit(row: Mapping[str, Any]) -> FindingClass:
    """D6 class of a unit that is not ingested; ``UNCLASSIFIED`` is never hidden."""
    reason = row["failure_reason"] or ""
    if reason.startswith(IngestCheck.STAT_REFERENCE):
        return FindingClass.STAT_REFERENCE
    if reason.startswith(IngestCheck.SESSION_BOUNDARY):
        return FindingClass.SESSION_BOUNDARY
    if UNDEFINED_WINDOW_TEXT in reason:
        return FindingClass.UNDEFINED_WINDOW
    if not reason and row["state"] == UnitState.VERIFIED:
        return FindingClass.AWAITING_DEFINITIONS
    return FindingClass.UNCLASSIFIED


def classify_unfinished(rows: Iterable[Mapping[str, Any]]) -> Counter[FindingClass]:
    """Count of each D6 class over the units that are not ingested."""
    return Counter(
        classify_unit(row) for row in rows if row["state"] != UnitState.INGESTED
    )


def bar_job_dirs(request_rows: Sequence[Mapping[str, Any]]) -> list[Path]:
    """Archive job directories of the ``ohlcv-1m`` requests (job id = directory)."""
    return sorted(
        {
            ARCHIVE_ROOT / row["provider_job_id"]
            for row in request_rows
            if row["schema"] == TickSchema.OHLCV_1M.value and row["provider_job_id"]
        }
    )


def run_session_check(job_dirs: Sequence[Path]) -> SessionCheck:
    """Exit 1 is a finding to narrate; exit 2 stops the cutover."""
    cmd = [sys.executable, str(VERIFY_SESSIONS_SCRIPT), "--calendar", SESSION_CALENDAR]
    result = subprocess.run([*cmd, *map(str, job_dirs)], check=False)
    if result.returncode == SESSIONS_CLEAN:
        return SessionCheck.PASS
    if result.returncode == SESSIONS_OUTSIDE:
        return SessionCheck.FINDING
    raise CutoverError(f"verify_cme_sessions.py stopped with exit {result.returncode}")
