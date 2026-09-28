"""Unit-tier prod-URL ratchet guard (2026-08-04 incident follow-up).

The unit tier is not exempt from the incident class: on 2026-08-04, unit-tier
fixtures in ``universe/test_tracking.py``, ``data/test_equity_universe.py``
and ``cli/commands/test_data_universes.py`` ran
``DELETE FROM universe_members`` against ``MT_TIMESCALE_DB_URL`` — that is
what emptied production ``universe_members`` (0 rows, original relfilenode)
while ``pytest test/unit`` reported 1855 passed. Those fixtures now run on
``migrated_db`` (ephemeral).

Same one-way ratchet as the integration tier (see
``test/_prod_url_guard.py``): new readers fail, stale allowlist entries fail.
The three remaining entries are read-only checks that genuinely need real
production history (AAPL bars/splits). Do not add entries.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _prod_url_guard import (
    PRIMARY_URL_VAR,
    TICK_URL_VARS,
    assert_ratchet,
    prod_url_readers,
)

# Frozen 2026-08-04. SHRINK ONLY — never add an entry.
ALLOWED_PROD_URL_READERS: frozenset[str] = frozenset(
    {
        "data/adjustment/test_adjusted.py",
        "market/test_timescale_daily_db.py",
        "market/test_timescale_minute_db.py",
    }
)


def test_unit_tier_never_adds_prod_db_url_readers() -> None:
    assert_ratchet(Path(__file__).parent, ALLOWED_PROD_URL_READERS, (PRIMARY_URL_VAR,))


def test_unit_tier_never_reads_tick_db_urls() -> None:
    """Slice 923 D9: the tick variables start, and stay, at zero readers."""
    assert_ratchet(Path(__file__).parent, frozenset(), TICK_URL_VARS)


@pytest.mark.parametrize("needle", TICK_URL_VARS)
def test_multiline_tick_url_read_is_detected(tmp_path: Path, needle: str) -> None:
    """The real-world shape: marker and variable on different lines."""
    (tmp_path / "reader.py").write_text(
        f'import os\n_URL = os.environ.get(\n    "{needle}",\n    "",\n)\n',
        encoding="utf-8",
    )
    assert prod_url_readers(tmp_path) == {"reader.py"}


def test_tick_url_mention_without_read_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "mention.py").write_text(
        f'"""Set {TICK_URL_VARS[0]} for the tick database."""\n', encoding="utf-8"
    )
    assert prod_url_readers(tmp_path) == set()
