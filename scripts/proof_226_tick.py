"""Slice 226 proof harness: one measurement step per run, one report per step.

Usage (from the checkout root)::

    uv run python scripts/proof_226_tick.py <step>

Each step writes ``project-documents/user/notes/<date>-226-proof-<step>.md``
or exits non-zero with a reason and no report. Steps (LLD 226 TD2): see
``STEPS``. Destructive statements run only against ``trading_tick_proof``
(``proof_226/guard.py``). Nothing here can buy data: every pass runs
``--estimate-only``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from proof_226 import (  # noqa: E402
    batch,
    contention,
    jobs,
    layouts,
    mapping,
    queries,
    rebuild,
    size,
    workers,
)
from proof_226.cli import CliStepError  # noqa: E402
from proof_226.common import ProofSetupError  # noqa: E402

Step = Callable[[], Path]

#: Every step name, in run order. ``None``: not implemented yet.
STEPS: dict[str, Step | None] = {
    "rebuild": rebuild.run,
    "mapping": mapping.run,
    "size": size.run,
    "jobs": jobs.run,
    "workers": workers.run,
    "batch": batch.run,
    "queries": queries.run,
    "layouts": layouts.run,
    "contention": contention.run,
    "final": None,
    "drop-proof": None,
    "archive-check": None,
}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in STEPS:
        print(f"usage: proof_226_tick.py <{'|'.join(STEPS)}>", file=sys.stderr)
        return 2
    step = STEPS[argv[0]]
    if step is None:
        print(f"step {argv[0]!r} is not implemented yet", file=sys.stderr)
        return 3
    try:
        report = step()
    # A refusal or a failed mt command, not a crash: say why and stop.
    except (ProofSetupError, CliStepError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: report written to {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
