"""Restore metadata tables truncated by the 2026-08-04 incident.

**What happened.** ``test/integration/conftest.py``'s ``instruments_clean_db``
fixture read ``MT_TIMESCALE_DB_URL`` — the *production* URL — and ran
``TRUNCATE TABLE provider_symbol_mapping, instruments RESTART IDENTITY
CASCADE``, then deleted migrations 033-036 from ``schema_migrations`` so its
orchestrator would re-apply them. It never got that far. Every other tier
routes through ``ephemeral_db``/``MT_TIMESCALE_TEST_URL``; this one fixture did
not, and no enforcement test covered ``test/integration/`` the way
``test_load_tier_never_references_prod_db_url`` covers the load tier.

**What was lost, and what was not.** The CASCADE rewrote six tables:
``instruments``, ``provider_symbol_mapping``, ``data_gaps``,
``trading_sessions``, ``splits``, ``dividends``. Dropping migrations 033-036
from the ledger is why the minute cagg hierarchy
(``minute_15min_ohlcv``/``minute_1hour_ohlcv``/``minute_4hour_ohlcv``) and
``minute_coverage`` are absent. **No bar data was touched**: ``minute_ohlcv``
(4.4B rows) and ``daily_ohlcv`` retain their original relfilenodes and full
history. Everything lost is derivable from EODHD or from the bars themselves,
which is why this is a restore and not a data-loss event.

**This module never deletes.** Every step is an upsert, an
``IF NOT EXISTS`` DDL replay, or a read. It is safe to re-run, and safe to
interrupt — each step is independent and idempotent. The one thing it will not
do is guess: it verifies the database looks like the damaged one before
touching it, and refuses otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from manta_trading.logging import get_logger

if TYPE_CHECKING:
    import psycopg
    from psycopg_pool import ConnectionPool

_logger = get_logger(__name__)

INCIDENT_MIGRATIONS: tuple[str, ...] = (
    "033_create_minute_caggs",
    "034_create_daily_caggs",
    "035_cagg_refresh_policies",
    "036_copy_splits_dividends_from_marketdb",
)
"""The four ledger rows the fixture deleted.

Their absence is what makes the replay in :func:`replay_missing_migrations`
both necessary and safe: the runner skips anything already in the ledger, and
every one of these four is guarded by ``IF NOT EXISTS`` at the DDL level, so a
partially-present state converges rather than erroring.
"""

TRUNCATED_TABLES: tuple[str, ...] = (
    "instruments",
    "provider_symbol_mapping",
    "data_gaps",
    "trading_sessions",
    "splits",
    "dividends",
)
"""Tables the ``CASCADE`` rewrote, identified by relfilenode: all six landed in
one contiguous block (~315,522,0xx) while every survivor kept an original
node (~721,xxx). ``trading_sessions`` was repopulated by a later calendar
extend, so it is listed for completeness rather than because it needs work."""

PRESERVED_TABLES: tuple[str, ...] = (
    "minute_ohlcv",
    "daily_ohlcv",
    "acquisition_state",
    "schema_migrations",
)
"""Tables that must still hold data. Checked before any write: if one of these
is empty, this is not the damaged database and the restore must not proceed."""


class Step(StrEnum):
    """Restore steps, in dependency order.

    Ordered because each depends on the last: caggs need the migration ledger
    repaired, corporate actions and universes need ``instruments`` populated.
    """

    MIGRATIONS = "migrations"
    INSTRUMENTS = "instruments"
    CORPORATE_ACTIONS = "corporate_actions"
    UNIVERSES = "universes"


@dataclass(frozen=True)
class TableState:
    """One table's observed row count."""

    name: str
    rows: int
    exists: bool = True


@dataclass
class Assessment:
    """What the database currently looks like, before any restore runs."""

    preserved: list[TableState] = field(default_factory=list)
    truncated: list[TableState] = field(default_factory=list)
    missing_migrations: list[str] = field(default_factory=list)
    missing_caggs: list[str] = field(default_factory=list)

    @property
    def bars_intact(self) -> bool:
        """True when every preserved table still holds rows.

        The gate on the whole operation: this restore is only meaningful
        against the database the incident damaged, and running it anywhere else
        — an empty cold-start DB, say — would seed production reference data
        into the wrong place.
        """
        return all(state.rows > 0 for state in self.preserved)

    @property
    def needs_restore(self) -> bool:
        return bool(
            self.missing_migrations
            or self.missing_caggs
            or any(state.rows == 0 for state in self.truncated)
        )

    def describe(self) -> str:
        lines = ["Preserved (must be non-zero):"]
        lines += [
            f"  {s.name:26s} {s.rows:>14,}" + ("" if s.rows else "   <-- EMPTY")
            for s in self.preserved
        ]
        lines.append("Truncated by the incident:")
        lines += [
            f"  {s.name:26s} {s.rows:>14,}" + ("   <-- empty" if not s.rows else "")
            for s in self.truncated
        ]
        if self.missing_migrations:
            lines.append("Migrations absent from the ledger:")
            lines += [f"  {m}" for m in self.missing_migrations]
        if self.missing_caggs:
            lines.append("Continuous aggregates absent:")
            lines += [f"  {v}" for v in self.missing_caggs]
        return "\n".join(lines)


def _count(conn: psycopg.Connection[Any], table: str) -> TableState:
    """Row count for ``table``, or ``exists=False`` when it is not there."""
    row = conn.execute("SELECT to_regclass(%s)", (f"public.{table}",)).fetchone()
    if row is None or row[0] is None:
        return TableState(name=table, rows=0, exists=False)
    count_row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
    return TableState(name=table, rows=int(count_row[0]) if count_row else 0)


def assess(conn: psycopg.Connection[Any]) -> Assessment:
    """Read-only survey of the damage. Issues no writes.

    Always run this first — :func:`restore` calls it and refuses to proceed on
    a database whose preserved tables are empty.
    """
    from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS

    applied = {
        r[0] for r in conn.execute("SELECT migration_id FROM schema_migrations")
    }
    defined = [m["id"] for m in MINUTE_MIGRATIONS]

    present_caggs = {
        r[0]
        for r in conn.execute(
            "SELECT user_view_name FROM _timescaledb_catalog.continuous_agg"
        )
    }
    expected_caggs = (
        "minute_5min_ohlcv",
        "minute_15min_ohlcv",
        "minute_1hour_ohlcv",
        "minute_4hour_ohlcv",
        "minute_coverage",
        "daily_coverage",
    )

    return Assessment(
        preserved=[_count(conn, t) for t in PRESERVED_TABLES],
        truncated=[_count(conn, t) for t in TRUNCATED_TABLES],
        missing_migrations=[m for m in defined if m not in applied],
        missing_caggs=[v for v in expected_caggs if v not in present_caggs],
    )


class RestoreRefused(RuntimeError):
    """The database does not look like the one the incident damaged.

    Deliberately an exception rather than a warning: seeding production
    reference data into the wrong database is the failure mode that caused this
    incident, and a restore tool that could repeat it would be worse than none.
    """


def replay_missing_migrations(pool: ConnectionPool[Any]) -> list[str]:
    """Re-apply migrations absent from the ledger (step 1).

    Delegates to the ordinary migration runner rather than re-issuing DDL here:
    it already applies each migration in its own transaction, records the
    ledger row, and skips anything present. The four incident migrations are
    each ``CREATE ... IF NOT EXISTS``-guarded, so the surviving
    ``minute_5min_ohlcv`` and ``daily_coverage`` are left alone while the
    absent views are created.

    This is the step that rebuilds ``minute_15min_ohlcv``,
    ``minute_1hour_ohlcv``, ``minute_4hour_ohlcv``, and — through 046, if it too
    is missing — ``minute_coverage``.

    Returns:
        The migration ids applied, in order.
    """
    from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
    from manta_trading.market.schema.runner import apply_migrations

    applied = apply_migrations(pool, MINUTE_MIGRATIONS)
    _logger.info("replayed %d migration(s): %s", len(applied), applied)
    return list(applied)


def restore(
    pool: ConnectionPool[Any],
    *,
    steps: tuple[Step, ...] = tuple(Step),
    dry_run: bool = False,
) -> dict[str, object]:
    """Run the restore sequence against ``pool``.

    Args:
        pool:   Pool for the database to restore. The caller resolves the URL;
                this module never reads it from the environment, so a restore
                cannot be aimed at the wrong database by an unset variable —
                the exact mechanism behind the incident it repairs.
        steps:  Which steps to run, defaulting to all. Each is independent and
                idempotent, so a partial run can be resumed by re-running.
        dry_run: Assess and report without writing.

    Returns:
        A summary mapping with the assessment and each step's outcome.

    Raises:
        RestoreRefused: the preserved tables are empty, so this is not the
            damaged database.
    """
    with pool.connection() as conn:
        before = assess(conn)

    if not before.bars_intact:
        empty = [s.name for s in before.preserved if s.rows == 0]
        raise RestoreRefused(
            f"refusing to restore: {empty} are empty, so this is not the "
            "database the incident damaged. Check the connection URL."
        )

    summary: dict[str, object] = {
        "assessment": before.describe(),
        "dry_run": dry_run,
        "steps": {},
    }
    if dry_run:
        _logger.info("dry run — no writes issued\n%s", before.describe())
        return summary

    outcomes: dict[str, object] = {}
    if Step.MIGRATIONS in steps and before.missing_migrations:
        outcomes[Step.MIGRATIONS.value] = replay_missing_migrations(pool)

    summary["steps"] = outcomes
    with pool.connection() as conn:
        summary["after"] = assess(conn).describe()
    return summary
