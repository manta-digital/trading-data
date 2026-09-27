"""Tick domain constants — the single source for every tick comparison value.

Schemas and tier sets, symbology types, delivery modes, the dataset code, the
decode byte budget, the download timeout, and env-var names are defined here
and only here (slice 220 design, Technical Decisions 4, 5, 7, 9).
"""

from __future__ import annotations

from enum import StrEnum


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
