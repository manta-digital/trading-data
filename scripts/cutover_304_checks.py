"""Judgements and evidence helpers for the slice 304 cutover (D3, D6, D7).

Mostly pure functions; ``scripts/cutover_304_first_purchase.py`` calls them from
its numbered steps. The one subprocess call is ``run_session_check``.
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cutover_common import (  # noqa: E402
    CutoverError,
    env_value,
    verify_same_database,
)

from manta_trading.data.tick.constants import (  # noqa: E402
    TickSchema,
    UnitState,
    calendar_for_product,
)
from manta_trading.data.tick.ingest_checks import IngestCheck  # noqa: E402
from manta_trading.data.tick.universe import TICK_UNIVERSE  # noqa: E402

PLAN_KEY = "MT_TICK_DATABENTO_PLAN"
PER_PASS_KEY = "MT_TICK_SPEND_CEILING_USD"
CAP_30D_KEY = "MT_TICK_SPEND_30D_CEILING_USD"
CEILING_STEP_USD = Decimal(5)  # ceilings are the estimate rounded up to this step
CEILING_SCRIPT = (
    Path(__file__).resolve().parents[1] / "deploy/set-tick-spend-ceilings.sh"
)
NO_CEILING_HINT = "the PM sets the ceilings with the command the --estimate run prints"
TICK_DB_URL_KEY = "MT_TICK_DB_URL"
VERDICT_ALLOWED = "allowed"
VERDICT_NOTHING = "nothing_to_buy"
ARCHIVE_ROOT = Path("/data/tick-archive")
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


def _round_up(value: Decimal) -> Decimal:
    steps = (value / CEILING_STEP_USD).to_integral_value(rounding=ROUND_CEILING)
    return steps * CEILING_STEP_USD


def ceiling_values(planned: Decimal, trailing: Decimal) -> tuple[Decimal, Decimal]:
    """``(per_pass, cap_30d)``: the planned figure and trailing-plus-planned, each
    rounded up to the next ``CEILING_STEP_USD`` (a multiple is not raised)."""
    return _round_up(planned), _round_up(planned + trailing)


def ceiling_command(planned: Decimal, trailing: Decimal) -> str:
    """The one root command the PM runs, with values derived from the estimate."""
    per_pass, cap_30d = ceiling_values(planned, trailing)
    return f"sudo {CEILING_SCRIPT} --per-pass {per_pass:f} --cap-30d {cap_30d:f}"


def _ceiling(text: str, key: str) -> Decimal:
    try:
        value = env_value(text, key)
    except CutoverError:
        value = ""  # absent is reported below with the command, same as empty
    if not value:
        raise CutoverError(f"{key} is not set in the env file; {NO_CEILING_HINT}")
    return Decimal(value)


def check_plan_unset(text: str) -> None:
    """Refuse a configured Databento plan: every request must be usage-billed."""
    try:
        plan = env_value(text, PLAN_KEY)
    except CutoverError:
        plan = ""  # absent is the wanted state: every request is usage-billed
    if plan:
        raise CutoverError(f"{PLAN_KEY} is set; this purchase assumes usage billing")


def check_env_text(text: str) -> Decimal:
    """Refuse a configured plan or a missing ceiling; return the per-pass ceiling."""
    check_plan_unset(text)
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


RequestKey = tuple[str, str, str]  # (schema, start, end) as the estimate prints


def resubmit_origins(
    request_rows: Iterable[Mapping[str, Any]],
) -> dict[RequestKey, datetime]:
    """When each jobless (resubmittable) request was first made, by its range.

    The estimate names no request id, so a resubmit is matched to its
    ``debug requests`` row by schema and range. Several rows can share a range
    (ES and GC); the earliest wins, so a match never looks newer than it is.
    """
    origins: dict[RequestKey, datetime] = {}
    for row in request_rows:
        if row["provider_job_id"]:
            continue
        key = (row["schema"], str(row["range_start"]), str(row["range_end"]))
        try:
            made = datetime.fromisoformat(row["requested_at"])
        except (TypeError, ValueError) as exc:
            raise CutoverError(
                f"request {row['request_id']}: requested_at"
                f" {row['requested_at']!r} is not a timestamp"
            ) from exc
        origins[key] = min(made, origins.get(key, made))
    return origins


def _check_resubmit(
    label: str,
    key: RequestKey,
    since: datetime | None,
    origins: Mapping[RequestKey, datetime],
) -> None:
    """A resubmit is ours only when its original was made at or after ``since``."""
    if since is None:
        raise CutoverError(f"estimate: request {label} is a resubmit")
    made = origins.get(key)
    if made is None:
        raise CutoverError(
            f"estimate: request {label} is a resubmit with no recorded original"
        )
    if made < since:
        raise CutoverError(
            f"estimate: request {label} is a resubmit of one made {made.isoformat()},"
            f" before this cutover started {since.isoformat()}"
        )


def _check_scope(
    summary: Mapping[str, Any],
    since: datetime | None,
    origins: Mapping[RequestKey, datetime],
) -> None:
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
            key = (req["schema"], req["start"], req["end"])
            _check_resubmit(label, key, since, origins)


def judge_estimate(
    summary: Mapping[str, Any],
    since: datetime | None = None,
    origins: Mapping[RequestKey, datetime] | None = None,
    *,
    check_ceilings: bool = True,
) -> Estimate:
    """Raise ``CutoverError`` naming the cause unless the plan may be bought.

    ``since`` is the cutover's start instant (``None`` when none is recorded, as
    in ``--estimate``); ``origins`` says when each resubmitted request was made.
    ``check_ceilings=False`` (``--estimate``) judges scope only: its purpose is
    to print the ceiling command, so short or unset ceilings are not a stop.
    """
    _check_scope(summary, since, origins or {})
    if summary["verdict"] == VERDICT_NOTHING:
        return Estimate.NOTHING_TO_BUY
    if not check_ceilings:
        return Estimate.BUY
    planned = _decimal(summary, "planned_usd")
    per_pass = _decimal(summary, "per_pass_ceiling_usd")
    cap_30d = _decimal(summary, "cap_30d_usd")
    trailing = _decimal(summary, "trailing_30d_usd")
    command = ceiling_command(planned, trailing)
    if planned > per_pass:
        raise CutoverError(
            f"estimate: planned {planned} exceeds the per-pass {per_pass};"
            f" the PM runs: {command}"
        )
    if planned + trailing > cap_30d:
        raise CutoverError(
            f"estimate: planned {planned} + trailing {trailing}"
            f" exceeds the 30-day {cap_30d}; the PM runs: {command}"
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


def calendar_for(product: str) -> str:
    """The product's calendar id; a product with none is a stop, never a default."""
    try:
        return calendar_for_product(product)
    except KeyError as exc:
        raise CutoverError(exc.args[0]) from exc


def product_of_symbols(symbols: str) -> str:
    """The universe product whose symbols a request row carries.

    ``debug requests --json`` renders the symbols list as one comma-joined
    string (``tick_debug_cmds._cell``), so that is the shape consumed here.
    """
    parsed = tuple(sorted(s.strip() for s in symbols.split(",")))
    for entry in TICK_UNIVERSE:
        if parsed == tuple(sorted(entry.symbols)):
            return entry.product
    raise CutoverError(f"request symbols {symbols!r} match no universe product")


def bar_job_dirs(request_rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Path]]:
    """Archive job directories of the ``ohlcv-1m`` requests, by product."""
    by_product: dict[str, set[Path]] = {}
    for row in request_rows:
        if row["schema"] == TickSchema.OHLCV_1M.value and row["provider_job_id"]:
            product = product_of_symbols(row["symbols"])
            by_product.setdefault(product, set()).add(
                ARCHIVE_ROOT / row["provider_job_id"]
            )
    return {product: sorted(dirs) for product, dirs in sorted(by_product.items())}


def run_session_check(job_dirs: Sequence[Path], calendar_id: str) -> SessionCheck:
    """Exit 1 is a finding to narrate; exit 2 stops the cutover."""
    cmd = [sys.executable, str(VERIFY_SESSIONS_SCRIPT), "--calendar", calendar_id]
    result = subprocess.run([*cmd, *map(str, job_dirs)], check=False)
    if result.returncode == SESSIONS_CLEAN:
        return SessionCheck.PASS
    if result.returncode == SESSIONS_OUTSIDE:
        return SessionCheck.FINDING
    raise CutoverError(f"verify_cme_sessions.py stopped with exit {result.returncode}")
