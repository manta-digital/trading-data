"""Only the Databento adapter modules import ``databento`` (slice 220).

A second provider replaces ``data/tick/databento/`` and nothing else, and
``build_estimate`` has no path to an SDK type.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[4] / "src" / "manta_trading"
ALLOWED = {
    SRC / "data" / "tick" / "databento" / "adapter.py",
    SRC / "data" / "tick" / "databento" / "dbn_file.py",
}
SDK_PACKAGES = ("databento", "databento_dbn")


def _imports_sdk(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        if any(n.split(".")[0] in SDK_PACKAGES for n in names):
            return True
    return False


def test_only_the_adapter_modules_import_the_sdk() -> None:
    importers = {p for p in SRC.rglob("*.py") if _imports_sdk(p)}
    assert importers == ALLOWED


def test_scan_sees_the_allowed_modules() -> None:
    """Guard the guard: the walk must actually find the two importers."""
    assert all(p.is_file() and _imports_sdk(p) for p in ALLOWED)
