"""Integration-tier prod-URL ratchet guard (2026-08-04 incident follow-up).

The load tier has banned all reads of the production DB variable since slice
167 (``test_load_tier_never_references_prod_db_url``) — and that guard is the
reason the load tier was safe in the session that truncated production. The
integration tier had no counterpart, and its destructive ``instruments_clean_db``
fixture read the production URL directly.

A verbatim copy of the load-tier ban is not yet possible: the files frozen in
``ALLOWED_PROD_URL_READERS`` read ``MT_TIMESCALE_DB_URL`` today, most of them
deliberately (read-only checks against real data). This guard is therefore a
**one-way ratchet**:

* any file NOT in the allowlist that reads the variable fails the tier, and
* any allowlist entry that stops reading the variable (or is deleted) also
  fails, so it must be removed from the list — the list can only shrink.

When the list reaches zero entries this file collapses into the load-tier
form. Do not add entries; move the offending code onto ``ephemeral_db`` /
``MT_TIMESCALE_TEST_URL`` instead.
"""

from __future__ import annotations

from pathlib import Path

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
        "test_rechunk_driver.py",
        "test_runner_ca_update.py",
        "test_runner_sigterm.py",
        "test_status_queries.py",
        "test_trading_calendar_integration.py",
    }
)


def _prod_url_readers() -> set[str]:
    """Files in this tier with a line that reads the production variable.

    Same predicate as the load-tier guard: the needle is concatenated so this
    file's own source cannot trip the check, and only lines that also touch
    the environment are flagged (docstring mentions are allowed).
    """
    prod_url_var = "MT_TIMESCALE" + "_DB_URL"
    env_read_markers = ("environ", "getenv")
    tier = Path(__file__).parent
    readers: set[str] = set()
    for path in tier.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if prod_url_var in line and any(m in line for m in env_read_markers):
                readers.add(path.relative_to(tier).as_posix())
                break
    return readers


def test_integration_tier_never_adds_prod_db_url_readers() -> None:
    readers = _prod_url_readers()

    new = sorted(readers - ALLOWED_PROD_URL_READERS)
    assert not new, (
        f"New integration-tier file(s) read MT_TIMESCALE_DB_URL: {new}. "
        "Use MT_TIMESCALE_TEST_URL and the ephemeral_db/migrated_db fixtures "
        "instead — the allowlist in this file is shrink-only."
    )

    stale = sorted(ALLOWED_PROD_URL_READERS - readers)
    assert not stale, (
        f"Allowlist entries no longer read MT_TIMESCALE_DB_URL: {stale}. "
        "Ratchet them out: delete the entries from ALLOWED_PROD_URL_READERS "
        "so they can never silently regress."
    )
