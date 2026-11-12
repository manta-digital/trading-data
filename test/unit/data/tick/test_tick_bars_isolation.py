"""``tick_bars`` imports without the database stack (308 D1, success criterion 8)."""

from __future__ import annotations

import json
import subprocess
import sys

_PROBE = (
    "import json, sys;"
    "import manta_trading.data.tick.tick_bars;"
    "print(json.dumps({m: m in sys.modules for m in"
    " ('psycopg', 'manta_trading.data.tick.constants')}))"
)


def test_tick_bars_loads_neither_psycopg_nor_tick_constants() -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", _PROBE],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    loaded = json.loads(result.stdout)
    assert loaded == {"psycopg": False, "manta_trading.data.tick.constants": False}
