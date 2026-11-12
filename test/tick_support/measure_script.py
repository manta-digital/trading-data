"""``scripts/measure_tick_bars.py``, loaded by path (slice 308).

The unit test and the load test both use it; loading by path, as the cutover
tests do, leaves ``sys.path`` alone.
"""

from __future__ import annotations

import importlib.util
import sys
from functools import cache
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "measure_tick_bars.py"


@cache
def measure_script() -> ModuleType:
    """The script module, loaded once (its dataclasses stay one class)."""
    spec = importlib.util.spec_from_file_location(SCRIPT.stem, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module here
    spec.loader.exec_module(module)
    return module
