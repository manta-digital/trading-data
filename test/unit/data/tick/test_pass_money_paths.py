"""Where the acquisition pass can spend (slice 224, Money rules).

The only paid call is ``submit_batch``, and only the purchase phase makes it;
nothing in the pass calls ``fetch_range`` (PM direction: batch jobs only).
Checked over the source of every tick module above the provider boundary, so a
new caller of a paid method fails here.
"""

from __future__ import annotations

import ast
from pathlib import Path

from manta_trading.data.tick import purchase_phase

TICK_DIR = Path(purchase_phase.__file__).parent
#: The protocol and adapter define the paid methods; nothing above calls them.
BOUNDARY = {"provider.py"}


def _callers(method: str) -> set[str]:
    """Module files that call ``<something>.<method>(...)`` or pass it on."""
    found = set()
    for path in sorted(TICK_DIR.glob("*.py")):
        if path.name in BOUNDARY:
            continue
        tree = ast.parse(path.read_text())
        if any(
            isinstance(node, ast.Attribute) and node.attr == method
            for node in ast.walk(tree)
        ):
            found.add(path.name)
    return found


def test_nothing_in_the_pass_calls_fetch_range() -> None:
    assert _callers("fetch_range") == set()


def test_only_the_purchase_phase_calls_submit_batch() -> None:
    assert _callers("submit_batch") == {"purchase_phase.py"}
