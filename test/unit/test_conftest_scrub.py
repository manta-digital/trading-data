"""The runtime prod-URL scrub removes the tick variables too (slice 923 D9)."""

from __future__ import annotations

import os

import pytest
from _prod_url_guard import PRIMARY_URL_VAR, PROD_URL_VARS, TICK_URL_VARS
from conftest import scrub_prod_urls

_OPT_IN = "MT_ALLOW_PROD_READS"
_FAKE_URL = "postgresql://prod.invalid/db"


@pytest.fixture(autouse=True)
def _all_prod_urls_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_OPT_IN, raising=False)
    for name in PROD_URL_VARS:
        monkeypatch.setenv(name, _FAKE_URL)


def test_scrub_removes_all_three() -> None:
    assert sorted(scrub_prod_urls()) == sorted(PROD_URL_VARS)
    assert len(PROD_URL_VARS) == 3
    for name in PROD_URL_VARS:
        assert name not in os.environ


def test_opt_in_keeps_only_the_primary_read_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_OPT_IN, "1")
    assert sorted(scrub_prod_urls()) == sorted(TICK_URL_VARS)
    assert PRIMARY_URL_VAR in os.environ
    for name in TICK_URL_VARS:
        assert name not in os.environ
