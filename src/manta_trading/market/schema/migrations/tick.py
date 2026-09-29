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
    TICK_TRADE_CHUNK_INTERVAL,
    UNIT_STATES_WITH_FILE,
    DeliveryMode,
    SType,
    UnitState,
)
from manta_trading.data.tick.storage_columns import (
    TICK_TRADE_BBO_COLUMNS,
    TICK_TRADE_KEY,
)
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import BOOTSTRAP_MIGRATION_ID

_NS_PER_MICROSECOND = 1_000


def render_enum_list(members: Iterable[StrEnum]) -> str:
    """Render enum members as a quoted SQL list, sorted by value.

    The Kalshi track's idiom, copied: the tick track must not import from
    ``data/kalshi`` (architecture 220).
    """
    return ", ".join(f"'{m.value}'" for m in sorted(members, key=lambda m: m.value))


def render_enum_check(column: str, members: Iterable[StrEnum]) -> str:
    return f"CHECK ({column} IN ({render_enum_list(members)}))"


def interval_to_ns(span: timedelta) -> int:
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
                    {render_enum_check("schema", ARCHIVED_SCHEMAS)},
                symbols               TEXT[]      NOT NULL
                    CONSTRAINT tick_request_symbols_check
                    CHECK (cardinality(symbols) > 0),
                stype_in              TEXT        NOT NULL
                    CONSTRAINT tick_request_stype_in_check
                    {render_enum_check("stype_in", SType)},
                range_start           DATE        NOT NULL,
                range_end             DATE        NOT NULL,
                delivery_mode         TEXT        NOT NULL
                    CONSTRAINT tick_request_delivery_mode_check
                    {render_enum_check("delivery_mode", DeliveryMode)},
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
                    {render_enum_check("state", UnitState)},
                state_changed_at      TIMESTAMPTZ NOT NULL,
                fetch_status          TEXT        NOT NULL
                    CONSTRAINT tick_archive_unit_fetch_status_check
                    {render_enum_check("fetch_status", FetchStatus)},
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
                    CHECK (state NOT IN ({render_enum_list(UNIT_STATES_WITH_FILE)})
                           OR (file_path IS NOT NULL
                               AND file_size_bytes IS NOT NULL
                               AND file_sha256 IS NOT NULL))
            );
        """,
    },
    {
        "id": "tick_003_definitions",
        "description": "Create tick_definition with server-enforced validity windows",
        # TD5 (definitions carry a server-enforced validity window): ids are
        # reused and symbols recycle, so windows for one instrument_id may
        # never overlap. Columns follow TICK_DEFINITION_COLUMNS; a provider
        # "undefined" value is stored as NULL (TD3), so only the window,
        # receive time and provenance are NOT NULL.
        "sql": """
            CREATE TABLE IF NOT EXISTS tick_definition (
                instrument_id       BIGINT  NOT NULL,
                activation_ns       BIGINT  NOT NULL,
                expiration_ns       BIGINT  NOT NULL,
                raw_symbol          TEXT,
                asset               TEXT,
                exchange            TEXT,
                instrument_class    TEXT,
                security_type       TEXT,
                cfi                 TEXT,
                currency            TEXT,
                min_price_increment BIGINT,
                display_factor      BIGINT,
                unit_of_measure     TEXT,
                unit_of_measure_qty BIGINT,
                contract_multiplier INTEGER,
                ts_recv_ns          BIGINT  NOT NULL,
                unit_id             BIGINT  NOT NULL
                    CONSTRAINT tick_definition_unit_fkey
                    REFERENCES tick_archive_unit (unit_id),
                CONSTRAINT tick_definition_pkey
                    PRIMARY KEY (instrument_id, activation_ns),
                CONSTRAINT tick_definition_window_check
                    CHECK (expiration_ns >= activation_ns),
                CONSTRAINT tick_definition_window_excl EXCLUDE USING gist (
                    instrument_id WITH =,
                    int8range(activation_ns, expiration_ns, '[]') WITH &&
                )
            );
        """,
    },
    {
        "id": "tick_004_trades",
        "description": "Create the tick_trade hypertable on integer nanosecond time",
        # TD1 (the key is the provider's triple plus a delivery-order
        # ordinal), TD2 (BIGINT nanoseconds; integer-time hypertable), TD6
        # (unit_id on every row, with no foreign key) and TD8 (7-day chunks,
        # no compression, no space dimension, no index beyond the key).
        # Columns follow TICK_TRADE_COLUMNS, then the derived columns; types
        # follow TD3, and sentinels are stored as delivered.
        "sql": f"""
            CREATE TABLE IF NOT EXISTS tick_trade (
                ts_event         BIGINT   NOT NULL,
                ts_recv          BIGINT   NOT NULL,
                instrument_id    BIGINT   NOT NULL,
                publisher_id     INTEGER  NOT NULL,
                sequence         BIGINT   NOT NULL,
                price            BIGINT   NOT NULL,
                size             BIGINT   NOT NULL,
                action           TEXT     NOT NULL,
                side             TEXT     NOT NULL,
                flags            SMALLINT NOT NULL,
                depth            SMALLINT NOT NULL,
                ts_in_delta      INTEGER  NOT NULL,
                bid_px_00        BIGINT,
                ask_px_00        BIGINT,
                bid_sz_00        BIGINT,
                ask_sz_00        BIGINT,
                bid_ct_00        BIGINT,
                ask_ct_00        BIGINT,
                sequence_ordinal SMALLINT NOT NULL
                    CONSTRAINT tick_trade_sequence_ordinal_check
                    CHECK (sequence_ordinal >= 0),
                unit_id          BIGINT   NOT NULL,
                CONSTRAINT tick_trade_pkey PRIMARY KEY ({", ".join(TICK_TRADE_KEY)}),
                CONSTRAINT tick_trade_bbo_check {_bbo_check()}
            );
            SELECT create_hypertable(
                'tick_trade',
                'ts_event',
                chunk_time_interval    => {interval_to_ns(TICK_TRADE_CHUNK_INTERVAL)},
                create_default_indexes => FALSE,
                if_not_exists          => TRUE
            );
        """,
    },
    {
        "id": "tick_005_ingest_ledger",
        "description": "Create tick_ingest_ledger: one row per unit and session",
        # TD7 (the ledger carries calendar_id, not the tier): the calendar
        # set grows, so no CHECK; the tier is reached through unit → request.
        # A zero-record instrument-session is a complete row with NULL times.
        "sql": """
            CREATE TABLE IF NOT EXISTS tick_ingest_ledger (
                unit_id        BIGINT NOT NULL
                    CONSTRAINT tick_ingest_ledger_unit_fkey
                    REFERENCES tick_archive_unit (unit_id),
                instrument_id  BIGINT NOT NULL,
                calendar_id    TEXT   NOT NULL,
                session_date   DATE   NOT NULL,
                record_count   BIGINT NOT NULL
                    CONSTRAINT tick_ingest_ledger_record_count_check
                    CHECK (record_count >= 0),
                volume         BIGINT NOT NULL
                    CONSTRAINT tick_ingest_ledger_volume_check
                    CHECK (volume >= 0),
                first_event_ns BIGINT,
                last_event_ns  BIGINT,
                CONSTRAINT tick_ingest_ledger_pkey
                    PRIMARY KEY (unit_id, instrument_id, session_date),
                CONSTRAINT tick_ingest_ledger_first_event_check
                    CHECK ((first_event_ns IS NULL) = (record_count = 0)),
                CONSTRAINT tick_ingest_ledger_last_event_check
                    CHECK ((last_event_ns IS NULL) = (record_count = 0)),
                CONSTRAINT tick_ingest_ledger_event_order_check
                    CHECK (first_event_ns <= last_event_ns)
            );
        """,
    },
]
