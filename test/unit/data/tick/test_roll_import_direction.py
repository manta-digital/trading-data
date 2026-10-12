"""Slice 229's import direction (Architecture, "Import direction").

No roll module imports ``tick_status_build`` (which imports the roll path, so
the reverse would be a cycle), and the pure modules import only the standard
library and other pure modules: the resolver and its tests need no database.
Direct imports only: package ``__init__`` files pull in psycopg transitively
for every module, so a transitive check could not tell pure from impure.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

TICK = Path(__file__).resolve().parents[4] / "src" / "manta_trading" / "data" / "tick"
ROLL_MODULES = sorted(TICK.glob("roll_*.py")) + [TICK / "active_contract.py"]
PURE = ("roll_rules.py", "roll_types.py", "roll_resolve.py")
#: The project modules a pure roll module may import: none does I/O.
PURE_DEPENDENCIES = {
    "manta_trading.data.base.session_index",
    "manta_trading.data.tick.constants",
    "manta_trading.data.tick.ingest_records",
    "manta_trading.data.tick.roll_resolve",
    "manta_trading.data.tick.roll_rules",
    "manta_trading.data.tick.roll_types",
    "manta_trading.data.tick.tick_status",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def test_the_scan_sees_every_roll_module() -> None:
    names = {p.name for p in ROLL_MODULES}
    assert {*PURE, "roll_inputs.py", "active_contract.py"} <= names


def test_no_roll_module_imports_tick_status_build() -> None:
    offenders = [
        p.name
        for p in ROLL_MODULES
        if "manta_trading.data.tick.tick_status_build" in _imports(p)
    ]
    assert offenders == []


def test_pure_modules_import_only_stdlib_and_pure_modules() -> None:
    impure = {
        name: sorted(
            module
            for module in _imports(TICK / name)
            if module.split(".")[0] not in sys.stdlib_module_names
            and module != "__future__"
            and module not in PURE_DEPENDENCIES
        )
        for name in PURE
    }
    assert impure == {name: [] for name in PURE}
