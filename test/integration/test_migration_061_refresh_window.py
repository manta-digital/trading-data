"""Migration 061 converges the minute caggs' policies from the old values.

A cold start already renders the current constants through 035/045, so the
interesting case is production's: policies created at the old 3-day refresh
window and 7-day compress_after. This test puts a migrated throwaway database
back into that state, re-runs 061's SQL, and reads the catalog.
"""

from __future__ import annotations

from datetime import timedelta

import psycopg

from manta_trading.constants import (
    MINUTE_CAGG_COMPRESS_AFTER,
    MINUTE_CAGG_REFRESH_START_OFFSET,
)
from manta_trading.market.schema.migrations.minute import (
    _MINUTE_CAGG_VIEWS,
    MINUTE_MIGRATIONS,
)

_MIGRATION_061_ID = "061_minute_cagg_weekly_refresh_window"
_OLD_START_OFFSET = timedelta(days=3)
_OLD_COMPRESS_AFTER = timedelta(days=7)

_POLICY_SQL = """
    SELECT hypertable_name, proc_name, job_id, config
    FROM timescaledb_information.jobs
    WHERE hypertable_name = ANY(%s)
      AND proc_name IN ('policy_refresh_continuous_aggregate', 'policy_compression')
"""


def _policies(conn: psycopg.Connection) -> dict[tuple[str, str], tuple[int, dict]]:
    rows = conn.execute(_POLICY_SQL, (list(_MINUTE_CAGG_VIEWS),)).fetchall()
    return {(view, proc): (job_id, config) for view, proc, job_id, config in rows}


def _interval(conn: psycopg.Connection, text: str) -> timedelta:
    row = conn.execute("SELECT %s::interval", (text,)).fetchone()
    assert row is not None
    return row[0]


def _regress_to_old_values(conn: psycopg.Connection) -> None:
    """Put every minute cagg policy back at its pre-061 value."""
    for (_view, proc), (job_id, config) in _policies(conn).items():
        if proc == "policy_compression":
            conn.execute(
                "SELECT alter_job(%s, config => jsonb_set(%s::jsonb, "
                "'{compress_after}', to_jsonb(%s::interval::text)))",
                (job_id, psycopg.types.json.Jsonb(config), _OLD_COMPRESS_AFTER),
            )
            continue
        conn.execute(
            "SELECT alter_job(%s, config => jsonb_set(%s::jsonb, "
            "'{start_offset}', to_jsonb(%s::interval::text)))",
            (job_id, psycopg.types.json.Jsonb(config), _OLD_START_OFFSET),
        )


def test_061_converges_old_policies(migrated_db: str) -> None:
    sql = next(m for m in MINUTE_MIGRATIONS if m["id"] == _MIGRATION_061_ID)["sql"]
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        _regress_to_old_values(conn)
        regressed = _policies(conn)
        for view in _MINUTE_CAGG_VIEWS:
            _, compress = regressed[(view, "policy_compression")]
            assert _interval(conn, compress["compress_after"]) == _OLD_COMPRESS_AFTER

        conn.execute(sql)
        policies = _policies(conn)

        assert len(policies) == 2 * len(_MINUTE_CAGG_VIEWS)
        for view in _MINUTE_CAGG_VIEWS:
            _, refresh = policies[(view, "policy_refresh_continuous_aggregate")]
            _, compress = policies[(view, "policy_compression")]
            assert (
                _interval(conn, refresh["start_offset"])
                == MINUTE_CAGG_REFRESH_START_OFFSET
            ), view
            assert (
                _interval(conn, compress["compress_after"])
                == MINUTE_CAGG_COMPRESS_AFTER
            ), view


def test_061_is_a_no_op_at_current_values(migrated_db: str) -> None:
    sql = next(m for m in MINUTE_MIGRATIONS if m["id"] == _MIGRATION_061_ID)["sql"]
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        before = _policies(conn)
        conn.execute(sql)
        assert _policies(conn) == before
