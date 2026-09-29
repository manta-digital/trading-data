"""Schema migration definitions for the ``tick`` track (slices 923, 222).

The track targets the tick database, never the primary ``trading`` database;
``TRACK_REGISTRY`` routes it there. Its ledger lives in the tick database and
holds only this track's ids.

It starts with the shared ``001_schema_migrations`` bootstrap, reused from the
minute track rather than copied. Every later id is prefixed ``tick_NNN_*``
(slice 222 onward) so it can never collide with a primary-track id.

Slice 222 adds the five storage tables. SQL is idempotent (IF NOT EXISTS,
``if_not_exists => TRUE``, constraints named inside the CREATE). Every enum
CHECK is rendered from its enum, never hand-listed (222 TD9). No migration
grants anything: ``tick_migrate``'s default privileges give ``tick_app`` DML,
and ``scripts/provision_tick_roles.sql`` enumerates the audited write surface.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from enum import StrEnum

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import (
    ARCHIVED_SCHEMAS,
    UNIT_STATES_WITH_FILE,
    DeliveryMode,
    SType,
    UnitState,
)
from manta_trading.data.tick.storage_columns import (
    TICK_TRADE_BBO_COLUMNS,
)
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import BOOTSTRAP_MIGRATION_ID

_NS_PER_MICROSECOND = 1_000


def _in_list(members: Iterable[StrEnum]) -> str:
    """Render enum members as a quoted SQL list, sorted by value.

    The Kalshi track's idiom, copied: the tick track must not import from
    ``data/kalshi`` (architecture 220).
    """
    return ", ".join(f"'{m.value}'" for m in sorted(members, key=lambda m: m.value))


def _check_in(column: str, members: Iterable[StrEnum]) -> str:
    return f"CHECK ({column} IN ({_in_list(members)}))"


def _interval_ns(span: timedelta) -> int:
    """A timedelta as exact integer nanoseconds (no float arithmetic)."""
    return span // timedelta(microseconds=1) * _NS_PER_MICROSECOND


def _bbo_check() -> str:
    """Every BBO column is NULL exactly when ``bid_px_00`` is (222 TD3)."""
    anchor, *others = TICK_TRADE_BBO_COLUMNS
    pairs = " AND ".join(f"(({c} IS NULL) = ({anchor} IS NULL))" for c in others)
    return f"CHECK ({pairs})"


TICK_MIGRATIONS: list[dict[str, str]] = [
    next(m for m in MINUTE_MIGRATIONS if m["id"] == BOOTSTRAP_MIGRATION_ID),
    {
        "id": "tick_001_extensions",
        "description": "Install TimescaleDB and btree_gist in the tick database",
        # btree_gist backs tick_definition's validity-window exclusion
        # (222 TD5). It is a trusted extension, so the database owner
        # (tick_migrate) creates it with no added attribute.
        "sql": """
            CREATE EXTENSION IF NOT EXISTS timescaledb;
            CREATE EXTENSION IF NOT EXISTS btree_gist;
        """,
    },
    {
        "id": "tick_002_manifest",
        "description": "Create the tick manifest: tick_request and tick_archive_unit",
        # TD4 (the manifest is two tables, request and unit, and failure is a
        # column rather than a state): job-grain facts once on tick_request;
        # the unit is one UTC day of a request and carries the whole
        # lifecycle. TD9 (every CHECK is rendered from its enum).
        "sql": f"""
            CREATE TABLE IF NOT EXISTS tick_request (
                request_id            BIGINT GENERATED ALWAYS AS IDENTITY
                                      PRIMARY KEY,
                dataset               TEXT        NOT NULL,
                schema                TEXT        NOT NULL
                    CONSTRAINT tick_request_schema_check
                    {_check_in("schema", ARCHIVED_SCHEMAS)},
                symbols               TEXT[]      NOT NULL
                    CONSTRAINT tick_request_symbols_check
                    CHECK (cardinality(symbols) > 0),
                stype_in              TEXT        NOT NULL
                    CONSTRAINT tick_request_stype_in_check
                    {_check_in("stype_in", SType)},
                range_start           DATE        NOT NULL,
                range_end             DATE        NOT NULL,
                delivery_mode         TEXT        NOT NULL
                    CONSTRAINT tick_request_delivery_mode_check
                    {_check_in("delivery_mode", DeliveryMode)},
                is_adopted            BOOLEAN     NOT NULL,
                provider_job_id       TEXT
                    CONSTRAINT tick_request_provider_job_id_key UNIQUE,
                estimated_cost_usd    NUMERIC     NOT NULL
                    CONSTRAINT tick_request_estimated_cost_check
                    CHECK (estimated_cost_usd >= 0),
                actual_cost_usd       NUMERIC
                    CONSTRAINT tick_request_actual_cost_check
                    CHECK (actual_cost_usd >= 0),
                provider_record_count BIGINT,
                billed_size_bytes     BIGINT,
                requested_at          TIMESTAMPTZ NOT NULL,
                committed_at          TIMESTAMPTZ,
                download_deadline     TIMESTAMPTZ,
                CONSTRAINT tick_request_range_check
                    CHECK (range_end > range_start)
            );
            CREATE TABLE IF NOT EXISTS tick_archive_unit (
                unit_id               BIGINT GENERATED ALWAYS AS IDENTITY
                                      PRIMARY KEY,
                request_id            BIGINT      NOT NULL
                    CONSTRAINT tick_archive_unit_request_fkey
                    REFERENCES tick_request (request_id),
                unit_date             DATE        NOT NULL,
                state                 TEXT        NOT NULL
                    CONSTRAINT tick_archive_unit_state_check
                    {_check_in("state", UnitState)},
                state_changed_at      TIMESTAMPTZ NOT NULL,
                fetch_status          TEXT        NOT NULL
                    CONSTRAINT tick_archive_unit_fetch_status_check
                    {_check_in("fetch_status", FetchStatus)},
                failure_reason        TEXT,
                attempt_count         INTEGER     NOT NULL
                    CONSTRAINT tick_archive_unit_attempt_count_check
                    CHECK (attempt_count >= 0),
                last_attempt_at       TIMESTAMPTZ,
                file_path             TEXT,
                file_size_bytes       BIGINT,
                file_sha256           TEXT,
                provider_record_count BIGINT,
                decoded_record_count  BIGINT,
                superseded_by_unit_id BIGINT
                    CONSTRAINT tick_archive_unit_superseded_by_fkey
                    REFERENCES tick_archive_unit (unit_id),
                repurchase_of_unit_id BIGINT
                    CONSTRAINT tick_archive_unit_repurchase_of_key UNIQUE
                    CONSTRAINT tick_archive_unit_repurchase_of_fkey
                    REFERENCES tick_archive_unit (unit_id),
                CONSTRAINT tick_archive_unit_request_day_key
                    UNIQUE (request_id, unit_date),
                CONSTRAINT tick_archive_unit_failure_reason_check
                    CHECK ((fetch_status = '{FetchStatus.UNKNOWN.value}')
                           = (failure_reason IS NULL)),
                CONSTRAINT tick_archive_unit_superseded_by_check
                    CHECK (superseded_by_unit_id <> unit_id),
                CONSTRAINT tick_archive_unit_file_check
                    CHECK (state NOT IN ({_in_list(UNIT_STATES_WITH_FILE)})
                           OR (file_path IS NOT NULL
                               AND file_size_bytes IS NOT NULL
                               AND file_sha256 IS NOT NULL))
            );
        """,
    },
]
