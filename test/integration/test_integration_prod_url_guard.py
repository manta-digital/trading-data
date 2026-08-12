"""Integration-tier prod-URL ratchet guard (2026-08-04 incident follow-up).

The load tier has banned all reads of the production DB variable since slice
167 (``test_load_tier_never_references_prod_db_url``) — and that guard is the
reason the load tier was safe in the session that truncated production. The
integration tier had no counterpart, and its destructive ``instruments_clean_db``
fixture read the production URL directly.

A verbatim copy of the load-tier ban is not yet possible: the files frozen in
``ALLOWED_PROD_URL_READERS`` read ``MT_TIMESCALE_DB_URL`` today, most of them
deliberately (read-only checks against real data). This guard is therefore a
**one-way ratchet** (see ``test/_prod_url_guard.py`` for the predicate):

* any file NOT in the allowlist that reads the variable fails the tier, and
* any allowlist entry that stops reading the variable (or is deleted) also
  fails, so it must be removed from the list — the list can only shrink.

When the list reaches zero entries this file collapses into the load-tier
form. Do not add entries; move the offending code onto ``ephemeral_db`` /
``MT_TIMESCALE_TEST_URL`` instead.
"""

from __future__ import annotations

from pathlib import Path

from _prod_url_guard import assert_ratchet

# Frozen 2026-08-04. SHRINK ONLY — never add an entry.
ALLOWED_PROD_URL_READERS: frozenset[str] = frozenset(
    {
        "data/acquisition/daemon/test_minute_daemon_integration.py",
        "test_auto_extend_daemon.py",
        "test_ca_update.py",
        "test_cagg_freshness.py",
        "test_daemon_concurrency.py",
        "test_daemon_list_drains.py",
        "test_daemon_run.py",
        "test_data_status.py",
        "test_data_status_view.py",
        "test_gaps_window_sql.py",
        "test_migration_028.py",
        "test_migration_043.py",
        "test_migrations_018_022.py",
        "test_migrations_023_024.py",
        "test_migrations_025_026.py",
        "test_migrations_029_036.py",
        # test_rechunk_driver.py ratcheted out by slice 170's code review
        # (F002): both its suites now build scratch hypertables inside an
        # ephemeral_db throwaway and read no production URL.
        "test_runner_ca_update.py",
        "test_runner_sigterm.py",
        "test_status_queries.py",
        "test_trading_calendar_integration.py",
    }
)


def test_integration_tier_never_adds_prod_db_url_readers() -> None:
    assert_ratchet(Path(__file__).parent, ALLOWED_PROD_URL_READERS)
