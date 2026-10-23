"""Shared fixtures for the command tests."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def no_backup_rows() -> Iterator[None]:
    """``mt data health`` tests run without the slice-924 backup rows.

    Their settings are mocks, so ``backup_roots`` would read as unset and fail
    every run — correct behaviour, but not what those tests judge. Tests of the
    backup rows override this fixture with one that does not patch.
    """
    with patch(
        "manta_trading.cli.commands.health_backup.gather_backup", return_value=[]
    ):
        yield
