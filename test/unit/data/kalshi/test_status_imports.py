"""Criterion 12 (slice 264): ``status.py`` imports neither the client nor
the transport — asserted on the imported module graph in a fresh
interpreter, not on source text."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

#: Slice 264's ``status``, slice 265's ``trade_status``, slice 267's
#: ``historical_status`` — all read the database only.
STATUS_MODULES = (
    "manta_trading.data.kalshi.status",
    "manta_trading.data.kalshi.trade_status",
    "manta_trading.data.kalshi.historical_status",
)
#: Criterion 12 names the client and the transport. (``httpx`` itself is not
#: on the list: ``constants.py`` has imported it for the timeout policy since
#: slice 261, and ``status`` reads constants.)
FORBIDDEN = (
    "manta_trading.data.kalshi.client",
    "manta_trading.data.kalshi.transport",
)
PROBE = """
import importlib, json, sys
importlib.import_module({module!r})
print(json.dumps(sorted(m for m in sys.modules if m in {forbidden!r})))
"""


@pytest.mark.parametrize("module", STATUS_MODULES)
def test_status_module_graph_excludes_client_and_transport(module: str):
    probe = PROBE.format(module=module, forbidden=FORBIDDEN)
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert json.loads(completed.stdout) == []
