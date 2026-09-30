"""Tick domain constants — the single source for every tick comparison value.

Schemas and tier sets, symbology types, delivery modes, the dataset code, the
decode byte budget, the download timeout, and env-var names are defined here
and only here (slice 220 design, Technical Decisions 4, 5, 7, 9). Slice 222
adds the storage vocabulary: the archive unit's lifecycle (``UnitState``,
``UNIT_STATES_WITH_FILE``), the schemas a request may archive
(``ARCHIVED_SCHEMAS``) and the trade hypertable's chunk interval.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from datetime import timedelta
from enum import StrEnum
from typing import Final

from manta_trading.market.schema.seed_cme_calendar import CME_EQUITY_CALENDAR_ID


class TickSchema(StrEnum):
    """Databento schema names — also the request parameter values."""

    TRADES = "trades"
    TBBO = "tbbo"
    MBP_1 = "mbp-1"
    DEFINITION = "definition"


#: The candidate tiers the preflight compares, cheapest first.
TICK_TIERS: tuple[TickSchema, ...] = (
    TickSchema.TRADES,
    TickSchema.TBBO,
    TickSchema.MBP_1,
)

#: What this initiative stores. ``mbp-1`` is estimate-only.
STORED_TIERS: frozenset[TickSchema] = frozenset({TickSchema.TRADES, TickSchema.TBBO})

#: Bought together with a tier, never a tier itself.
COMPANION_SCHEMAS: frozenset[TickSchema] = frozenset({TickSchema.DEFINITION})

#: What a ``tick_request`` may archive. ``mbp-1`` stays estimate-only
#: (slice 222 TD9).
ARCHIVED_SCHEMAS: frozenset[TickSchema] = STORED_TIERS | COMPANION_SCHEMAS

#: The one tuple the preflight iterates: every tier, then the companion.
ESTIMATE_SCHEMAS: tuple[TickSchema, ...] = (*TICK_TIERS, TickSchema.DEFINITION)


class SType(StrEnum):
    """Databento symbology types (``stype_in`` / ``stype_out`` values)."""

    RAW_SYMBOL = "raw_symbol"
    INSTRUMENT_ID = "instrument_id"
    PARENT = "parent"
    CONTINUOUS = "continuous"


class DeliveryMode(StrEnum):
    """How a unit's file arrives; 222's manifest discriminator."""

    BATCH_JOB = "batch_job"
    DIRECT_RANGE = "direct_range"


class UnitState(StrEnum):
    """An archive unit's lifecycle, in order (slice 222 TD4).

    The state is the furthest step reached and never moves backward. Failure
    is not a state: it is the unit's ``fetch_status`` (``FetchStatus``), the
    failure lifecycle of the next step, so a failed unit keeps its resume point.
    """

    REQUESTED = "requested"
    SUBMITTED = "submitted"
    DELIVERED = "delivered"
    DOWNLOADED = "downloaded"
    VERIFIED = "verified"
    INGESTED = "ingested"


#: States in which a unit has its file: path, size and SHA-256 are required.
UNIT_STATES_WITH_FILE: frozenset[UnitState] = frozenset(
    {UnitState.DOWNLOADED, UnitState.VERIFIED, UnitState.INGESTED}
)

TICK_TRADE_CHUNK_INTERVAL: timedelta = timedelta(days=7)
"""``tick_trade``'s chunk interval, rendered to integer nanoseconds.

Rule (journal 20260719): the table's wall-clock span divided by 1,000–2,000
target chunks. About 20 years of plausible span gives about 1,040 chunks at
7 days (slice 222 TD8). Slice 226 validates it from measurements and may
re-set it with a tick-track migration.
"""


class DatasetCondition(StrEnum):
    """Databento's per-day data condition (``get_dataset_condition``)."""

    AVAILABLE = "available"
    DEGRADED = "degraded"
    PENDING = "pending"
    MISSING = "missing"


#: The one weekday Databento's ``get_dataset_condition`` leaves out of its
#: answer for ``GLBX.MDP3``: it lists no condition for a Saturday (no trading
#: day touches it). Measured 2026-09-29 over 2024-08-30 → 2024-12-31: 106 of 124
#: days answered, the 18 absent all Saturdays, Sundays and holidays present.
CONDITION_ABSENT_WEEKDAY = calendar.SATURDAY


class BatchJobState(StrEnum):
    """Databento batch job lifecycle (``batch.get_job_details`` ``state``)."""

    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    EXPIRED = "expired"


#: CME Globex MDP 3.0 — the dataset every futures request in 220 targets.
CME_DATASET = "GLBX.MDP3"

TICK_DECODE_BATCH_BYTES = 32 * 1024 * 1024
"""Upper bound on one decoded batch, in bytes: one in-flight batch per worker.

``DbnFile.iter_batches`` derives its record count from this and the file's
record size, so the bound holds for every schema. This is a starting value;
slice 226 replaces it from measurements on purchased data and rewrites this
docstring.
"""

#: Per-operation timeout (connect, read, write, pool) for the adapter's own
#: batch-file download. 100 s matches the SDK's fixed ``(100, 100)`` timeout
#: on every other Databento request, so a stalled download fails on the same
#: scale as a stalled metadata or stream call instead of hanging a worker.
TICK_DOWNLOAD_TIMEOUT_SECONDS = 100.0

#: ``Settings.tick_spend_ceiling_usd``'s environment name, spelled once for
#: the preflight's verdict text and its tests.
TICK_SPEND_CEILING_ENV = "MT_TICK_SPEND_CEILING_USD"

#: Futures product → the trading calendar its sessions come from (slice 221
#: D8). GC joins with ``CME_METALS`` in slice 231; there is no default.
FUTURES_PRODUCT_CALENDAR: Final[Mapping[str, str]] = {"ES": CME_EQUITY_CALENDAR_ID}


def calendar_for_product(product: str) -> str:
    """The calendar id for a futures product.

    Raises:
        KeyError: ``product`` has no calendar; the message names the known ones.
    """
    try:
        return FUTURES_PRODUCT_CALENDAR[product]
    except KeyError:
        known = ", ".join(sorted(FUTURES_PRODUCT_CALENDAR))
        raise KeyError(
            f"futures product {product!r} has no trading calendar (known: {known})"
        ) from None


# -- Slice 223: the acquisition run context and archive (LLD 224 TD2, TD11) ---

#: Session-level advisory lock held by every manifest writer (``adopt``,
#: ``reset``, 224's ``pass``) for the whole run (TD2). Distinct from Kalshi's
#: ``SYNC_ADVISORY_LOCK_KEY`` (262_000_001); 220 is this initiative's number.
TICK_ACQUISITION_LOCK_KEY = 220_000_001

#: Seconds allowed to connect to the tick database before the preflight
#: refuses it as unreachable (TD2).
TICK_DB_CONNECT_TIMEOUT_SECONDS = 10

#: ``Settings.tick_spend_30d_ceiling_usd``'s environment name (TD7; stored in
#: 223, read by 224's spend guard).
TICK_SPEND_30D_CEILING_ENV = "MT_TICK_SPEND_30D_CEILING_USD"

#: ``Settings.tick_archive_dir``'s environment name (TD2 refusal, TD11 layout).
TICK_ARCHIVE_DIR_ENV = "MT_TICK_ARCHIVE_DIR"

#: Every tick setting's environment prefix; an unknown key under it is refused
#: by the preflight so a misspelt ceiling never reads as "no ceiling" (TD2).
TICK_ENV_PREFIX = "MT_TICK_"


# -- Slice 224: the acquisition pass (LLD 224 TD7, TD9) ------------------------

#: How long the await phase polls for jobs to finish (TD9); a conservative
#: start that slice 226 re-sets from measurement.
TICK_WAIT_BUDGET_SECONDS = 1800

#: Seconds between the await phase's polls (TD9); 226 re-sets it too.
TICK_POLL_INTERVAL_SECONDS = 15

#: Slack subtracted from a request's ``requested_at`` when searching the
#: provider's job list for an unrecorded submit: host/provider clock skew (TD9).
TICK_JOB_MATCH_SKEW = timedelta(minutes=5)

#: Age after which an unresolved submit is exhausted instead of retried (TD9).
TICK_SUBMIT_RESOLVE_AGE = timedelta(hours=1)

#: The rolling window the 30-day spend ceiling is summed over (TD7).
TICK_SPEND_WINDOW = timedelta(days=30)

# -- Slice 224: the provider's "undefined" values in a definition record (TD5) --

#: ``activation``/``expiration`` when the provider gives no timestamp
#: (DBN ``UNDEF_TIMESTAMP``, ``u64::MAX``).
UNDEF_TIMESTAMP_NS = 2**64 - 1
#: DBN ``UNDEF_PRICE`` (``i64::MAX``): an undefined price or quantity field.
UNDEF_INT64 = 2**63 - 1
#: An undefined ``i32`` field (``i32::MAX``), e.g. ``contract_multiplier``.
UNDEF_INT32 = 2**31 - 1

#: Definition source field → its "undefined" value; the writer stores ``NULL``
#: for these (222 TD3: ``tick_definition`` is a model table). An empty string
#: is undefined for every text field.
DEFINITION_UNDEFINED: Final[Mapping[str, int]] = {
    "activation": UNDEF_TIMESTAMP_NS,
    "expiration": UNDEF_TIMESTAMP_NS,
    "min_price_increment": UNDEF_INT64,
    "unit_of_measure_qty": UNDEF_INT64,
    "contract_multiplier": UNDEF_INT32,
}


# -- Slice 225: the ingest pass (LLD 225 TD2, TD4, TD5, TD8) -------------------

#: Session-level advisory lock every ingest holds for its whole run (TD4).
#: Distinct from acquisition's, so ingest and acquisition may run together.
TICK_INGEST_LOCK_KEY = 220_000_002

#: Units loaded at once, each on its own thread and connection (TD2). A
#: starting value; slice 226 re-sets it from measurement.
TICK_INGEST_WORKERS = 2

#: Tier → rank, higher supersedes lower (TD5). Derived from ``TICK_TIERS``
#: order, the one place the order is stated.
TICK_TIER_RANK: Final[Mapping[TickSchema, int]] = {
    tier: rank for rank, tier in enumerate(TICK_TIERS)
}


def tier_rank_sql(column: str) -> str:
    """SQL ranking ``column`` (a schema name) by ``TICK_TIERS`` order (TD5).

    ``array_position`` is 1-based where ``TICK_TIER_RANK`` is 0-based; only the
    order is compared. ``column`` is interpolated verbatim into the SQL text: a
    column reference or a bind placeholder (``%(schema)s``) written in code,
    never input.
    """
    tiers = ", ".join(f"'{tier.value}'" for tier in TICK_TIERS)
    return f"array_position(ARRAY[{tiers}]::text[], {column})"


#: A worker's ``lock_timeout`` (TD8): a unit row or trade chunk held longer
#: than this aborts the run as ``storage_abort`` instead of hanging. A modest
#: starting value; 226 re-sets it.
TICK_INGEST_LOCK_TIMEOUT_SECONDS = 30

#: TCP keepalives on a worker's connection (TD8): a silently lost database is
#: noticed after idle + interval × count seconds (about two minutes here),
#: without a statement timeout that would cut off a large COPY. Modest
#: starting values; 226 re-sets them.
TICK_DB_KEEPALIVES_IDLE_SECONDS = 60
TICK_DB_KEEPALIVES_INTERVAL_SECONDS = 10
TICK_DB_KEEPALIVES_COUNT = 6
