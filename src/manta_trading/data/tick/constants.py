"""Tick domain constants — the single source for every tick comparison value.

Schemas and tier sets, symbology types, delivery modes, the dataset code, the
decode byte budget, the download timeout, and env-var names are defined here
and only here (slice 220 design, Technical Decisions 4, 5, 7, 9). Slice 222
adds the storage vocabulary: the archive unit's lifecycle (``UnitState``,
``UNIT_STATES_WITH_FILE``), the schemas a request may archive
(``ARCHIVED_SCHEMAS``) and the trade hypertable's chunk interval.
"""

from __future__ import annotations

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
