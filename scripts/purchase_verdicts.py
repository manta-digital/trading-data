"""The purchase cutover's own judgements (slice 307 D1, D2, D5, D6).

Kept apart from ``cutover_304_*`` so each of those scripts stays near 300 lines.
This module imports from ``cutover_common`` and ``cutover_304_checks`` and from
nothing that imports it. Failures raise ``CutoverError`` with a message of their
own, so a test (and the operator) can tell the causes apart.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cutover_304_checks import classify_unit, product_of_symbols  # noqa: E402
from cutover_common import CutoverError  # noqa: E402

from manta_trading.cli.commands.tick_exit import EXIT_OK, EXIT_PARTIAL  # noqa: E402
from manta_trading.data.quality.fetch_status import FetchStatus  # noqa: E402
from manta_trading.data.tick.constants import TickSchema  # noqa: E402
from manta_trading.data.tick.statistics_constants import (  # noqa: E402
    STATISTICS_PUBLICATION_SESSIONS,
)
from manta_trading.data.tick.tick_status import TickSessionStatus  # noqa: E402
from manta_trading.data.tick.universe import TICK_UNIVERSE  # noqa: E402

ACQUISITION_FAILED = "acquisition failed"  # D6's label for a retry-exhausted request
PUBLICATION_AFTER_WANT_END = "publication after want end"  # D1, D6
NO_ERROR_TEXT = "(the manifest holds no error text)"
ISSUE_CLOSED = "CLOSED"  # the ``state`` ``gh issue view`` prints for a closed issue
# ``status`` exits 3 for a verdict (a roll line), not a failure; any other
# non-zero code is one.
STATUS_ACCEPTED_EXITS = (EXIT_OK, EXIT_PARTIAL)


def check_gate_issue(number: int, view: Callable[[int], str]) -> None:
    """Stop unless issue ``number`` is closed; every doubt is a stop.

    ``view`` runs ``gh issue view <number> --json state`` and returns its text.
    """
    try:
        text = view(number)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise CutoverError(f"gate issue #{number}: gh failed: {exc}") from exc
    try:
        state = json.loads(text)["state"]
    except json.JSONDecodeError as exc:
        raise CutoverError(f"gate issue #{number}: gh printed no JSON: {exc}") from exc
    except (KeyError, TypeError) as exc:
        raise CutoverError(f"gate issue #{number}: gh output has no state") from exc
    if state != ISSUE_CLOSED:
        raise CutoverError(
            f"gate issue #{number} is {state}: the invoice comparison is not done"
        )


def latest_want_end() -> datetime:
    """The greatest history-want end in ``TICK_UNIVERSE``, as 00:00 UTC."""
    ends: list[date] = []
    for entry in TICK_UNIVERSE:
        for want in entry.history:
            if want.end is None:
                raise CutoverError(
                    f"{entry.product} {want.schema.value} want has no end date"
                )
            ends.append(want.end)
    if not ends:
        raise CutoverError("the tick universe has no history wants")
    return datetime.combine(max(ends), datetime.min.time(), tzinfo=UTC)


def _edge_of(product: dict[str, Any]) -> datetime:
    name = product.get("product", "?")
    raw = product.get("edge_available_end")
    if raw is None:
        raise CutoverError(f"dataset edge: {name} reports no available end")
    try:
        edge = datetime.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise CutoverError(f"dataset edge: {raw!r} is not an instant") from exc
    if edge.tzinfo is None:
        raise CutoverError(f"dataset edge: {raw!r} is not a UTC instant")
    return edge.astimezone(UTC)


def check_dataset_edge(
    status_result: subprocess.CompletedProcess[str], minimum: datetime
) -> None:
    """Stop unless the dataset edge ``status --json`` reports reaches ``minimum``.

    The edge is one value per dataset, printed on every product; all must reach
    it. Equal passes.
    """
    if status_result.returncode not in STATUS_ACCEPTED_EXITS:
        detail = (status_result.stderr or "").strip()
        raise CutoverError(
            f"dataset edge: status exited {status_result.returncode}: {detail}"
        )
    try:
        products = json.loads(status_result.stdout)["products"]
    except json.JSONDecodeError as exc:
        raise CutoverError(f"dataset edge: status printed no JSON: {exc}") from exc
    except (KeyError, TypeError) as exc:
        raise CutoverError("dataset edge: status output has no products") from exc
    if not products:
        raise CutoverError("dataset edge: status lists no products")
    edge = min(_edge_of(product) for product in products)
    if edge < minimum:
        raise CutoverError(
            f"dataset edge {edge.isoformat()} is before the latest want end"
            f" {minimum.isoformat()}: the provider has not published it yet"
        )


# -- D5: failed requests ----------------------------------------------------------


@dataclass(frozen=True)
class ExhaustedRequest:
    """A request the provider will not serve: final, reported, never retried."""

    request_id: int
    product: str
    schema: str
    start: str
    end: str
    error: str

    def text(self) -> str:
        span = f"{self.start}..{self.end}"
        return (
            f"request {self.request_id} {self.product} {self.schema} {span}:"
            f" {ACQUISITION_FAILED}: {self.error}"
        )


def unit_is_retry_exhausted(unit: Mapping[str, Any]) -> bool:
    """A retry-exhausted unit holds its day: it is final (D5)."""
    return unit["fetch_status"] == FetchStatus.RETRY_EXHAUSTED.value


def retry_exhausted_requests(
    request_rows: Iterable[Mapping[str, Any]], unit_rows: Iterable[Mapping[str, Any]]
) -> list[ExhaustedRequest]:
    """Requests with a retry-exhausted unit, with the provider's error text.

    ``debug requests`` rows carry no state or error: both live on the units.
    """
    errors: dict[int, list[str]] = {}
    for unit in unit_rows:
        if unit_is_retry_exhausted(unit):
            reasons = errors.setdefault(unit["request_id"], [])
            reason = unit["failure_reason"] or NO_ERROR_TEXT
            if reason not in reasons:
                reasons.append(reason)
    return [
        ExhaustedRequest(
            row["request_id"],
            product_of_symbols(row["symbols"]),
            row["schema"],
            str(row["range_start"]),
            str(row["range_end"]),
            "; ".join(errors[row["request_id"]]),
        )
        for row in request_rows
        if row["request_id"] in errors
    ]


def retryable_failures(unit_rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """One failure per request still failed-retryable; exhausted ones give none."""
    ids = sorted(
        {
            unit["request_id"]
            for unit in unit_rows
            if unit["fetch_status"] == FetchStatus.FAILED_RETRYABLE.value
        }
    )
    return [
        f"request {rid} is still failed-retryable: re-run the cutover" for rid in ids
    ]


# -- D6 item 3: the sessions that are not complete --------------------------------

# Buckets the exit rule accounts for only by a manifest reason or a script label.
_NEEDS_REASON = {
    TickSessionStatus.FAILED.value,
    TickSessionStatus.RETRY_EXHAUSTED.value,
}
_NEVER_ACCOUNTED = {
    TickSessionStatus.IN_FLIGHT.value,
    TickSessionStatus.AWAITING_INGEST.value,
}
_NEEDS_PUBLICATION = {
    TickSessionStatus.MISSING.value,
    TickSessionStatus.PENDING.value,
    TickSessionStatus.EDGE_UNKNOWN.value,
}
_LISTED_NEVER = {
    TickSessionStatus.COMPLETE.value,
    TickSessionStatus.PROVIDER_HOLE.value,
}


@dataclass
class SessionReport:
    lines: list[str]
    failures: list[str]


def _reasons(
    session: Mapping[str, Any], units_by_id: Mapping[int, Mapping[str, Any]]
) -> list[str]:
    """Each failed unit's manifest reason verbatim, with its D6 class."""
    found = []
    for unit_id in session["unit_ids"]:
        unit = units_by_id.get(unit_id)
        if unit is not None and unit["failure_reason"]:
            found.append(f"[{classify_unit(unit).value}] {unit['failure_reason']}")
    return found


def _label(
    session: Mapping[str, Any],
    product: str,
    schema: str,
    publication_tail: Sequence[str],
    exhausted: Sequence[ExhaustedRequest],
) -> str | None:
    day = str(session["session_date"])
    for request in exhausted:
        inside = request.start <= day < request.end
        if (request.product, request.schema) == (product, schema) and inside:
            return ACQUISITION_FAILED
    if schema == TickSchema.STATISTICS.value and day in publication_tail:
        return PUBLICATION_AFTER_WANT_END
    return None


def _unaccounted(bucket: str, label: str | None, reasons: Sequence[str]) -> bool:
    if bucket in _NEVER_ACCOUNTED:
        return True
    if bucket in _NEEDS_REASON:
        return not reasons and label != ACQUISITION_FAILED
    return bucket in _NEEDS_PUBLICATION and label != PUBLICATION_AFTER_WANT_END


def not_caught_up_report(
    family: tuple[str, str, bool | None],
    coverage: Mapping[str, Any],
    unit_rows: Iterable[Mapping[str, Any]],
    exhausted: Sequence[ExhaustedRequest],
) -> SessionReport:
    """D6 item 3 for one family ``(product, schema, caught_up)``.

    Lists every judged session that is neither complete nor a provider hole,
    with the ``coverage`` bucket verbatim; a session the exit rule cannot
    account for is a failure. A family that is caught up lists nothing. The
    statistics sessions whose publication straddles the want end are the last
    ``STATISTICS_PUBLICATION_SESSIONS`` judged ones (a session is published
    during the next session, which the want end cuts off).
    """
    product, schema, caught_up = family
    report = SessionReport([], [])
    if caught_up is not False:
        return report
    units_by_id = {unit["unit_id"]: unit for unit in unit_rows}
    for entry in coverage["products"]:
        sessions = sorted(entry["sessions"], key=lambda s: str(s["session_date"]))
        tail = [str(s["session_date"]) for s in sessions][
            -STATISTICS_PUBLICATION_SESSIONS:
        ]
        for session in sessions:
            bucket = session["status"]
            if bucket in _LISTED_NEVER:
                continue
            reasons = _reasons(session, units_by_id)
            label = _label(session, product, schema, tail, exhausted)
            named = f"{product} {schema} {session['session_date']} {bucket}"
            detail = [*([label] if label else []), *reasons]
            report.lines.append(f"{named}: {'; '.join(detail) or 'no reason recorded'}")
            if _unaccounted(bucket, label, reasons):
                report.failures.append(f"{named} is not accounted for")
    return report
