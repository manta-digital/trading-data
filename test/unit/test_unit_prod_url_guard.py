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

from _prod_url_guard import assert_ratchet

# Frozen 2026-08-04. SHRINK ONLY — never add an entry.
ALLOWED_PROD_URL_READERS: frozenset[str] = frozenset(
    {
        "data/adjustment/test_adjusted.py",
        "market/test_timescale_daily_db.py",
        "market/test_timescale_minute_db.py",
    }
)


def test_unit_tier_never_adds_prod_db_url_readers() -> None:
    assert_ratchet(Path(__file__).parent, ALLOWED_PROD_URL_READERS)
