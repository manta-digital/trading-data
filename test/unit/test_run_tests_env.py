"""``scripts/run_tests.py`` hands a tier no production DB URL (slice 923 D9)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from _prod_url_guard import PROD_URL_VARS

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_tests.py"
_MARKET_URL_VAR = "MT_MARKET_DB_URL"


def _load_run_tests() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_tests", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("tier", ["unit", "integration", "load"])
def test_build_env_drops_every_prod_url(
    monkeypatch: pytest.MonkeyPatch, tier: str
) -> None:
    for name in (*PROD_URL_VARS, _MARKET_URL_VAR):
        monkeypatch.setenv(name, "postgresql://prod.invalid/db")

    env = _load_run_tests().build_env(tier, {})

    for name in (*PROD_URL_VARS, _MARKET_URL_VAR):
        assert name not in env
