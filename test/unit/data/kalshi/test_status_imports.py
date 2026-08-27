"""Criterion 12 (slice 264): ``status.py`` imports neither the client nor
the transport — asserted on the imported module graph in a fresh
interpreter, not on source text."""

from __future__ import annotations

import json
import subprocess
import sys

STATUS = "manta_trading.data.kalshi.status"
#: Criterion 12 names the client and the transport. (``httpx`` itself is not
#: on the list: ``constants.py`` has imported it for the timeout policy since
#: slice 261, and ``status`` reads constants.)
FORBIDDEN = (
    "manta_trading.data.kalshi.client",
    "manta_trading.data.kalshi.transport",
)
PROBE = f"""
import importlib, json, sys
importlib.import_module({STATUS!r})
print(json.dumps(sorted(m for m in sys.modules if m in {FORBIDDEN!r})))
"""


def test_status_module_graph_excludes_client_and_transport():
    completed = subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, check=True
    )
    assert json.loads(completed.stdout) == []
