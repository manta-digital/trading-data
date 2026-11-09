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
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cutover_common import CutoverError  # noqa: E402

from manta_trading.cli.commands.tick_exit import EXIT_OK, EXIT_PARTIAL  # noqa: E402
from manta_trading.data.tick.universe import TICK_UNIVERSE  # noqa: E402

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
