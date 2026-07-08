---
docType: slice-design
slice: acquisition-state-schema-and-orchestrator-core
project: trading
parent: user/architecture/120-slices.data-acquisition.md
dependencies: [100, 900]
interfaces: []
dateCreated: 20260407
dateUpdated: 20260411
status: complete
---

# Slice Design: Acquisition State Schema and Orchestrator Core

## Overview

Foundation slice for Initiative 120. Establishes the two pieces every later acquisition slice depends on:

1. **`acquisition_state` table** on .144 — single source of truth for per-symbol watermarks, run status, and error context. Resumability is built on this table; without it, no later slice can claim "resumes from where it left off."
2. **Shared orchestrator core** — a small, provider-agnostic Python module implementing the fetch → validate → write → checkpoint loop as a reusable building block. Daily and minute orchestrators (slices 122 and 124) compose this core; daemons (123 and 125) just call it in a loop.

Also in scope: a structured-event emission scaffold (so later slices have somewhere to send events without designing the format under pressure) and a read-only `mt data state` CLI command that proves the schema is reachable end-to-end.

**Out of scope:** any provider changes, any daemon process, any modification to existing acquisition code paths (`marketservice.py`, `HistoricalMinuteService`). This slice introduces new infrastructure alongside the existing code without disturbing it.

## Value

**Architectural:** Establishes the contract that every subsequent 120-band slice plugs into. The orchestrator interface is the single seam between "how to fetch from a provider" (slice 122/124) and "how to run that continuously" (slice 123/125). Getting it right here means slices 122–125 don't relitigate the loop shape.

**Developer-facing:** Provides a small, well-tested core that future provider work can lean on. A new provider integration becomes "implement the provider interface and pass it to the orchestrator," not "build a new orchestration loop."

**Operator-facing:** `mt data state` becomes the canonical "what does the system think it has?" query. Even before any new acquisition runs, the command answers questions about the schema and confirms the table is reachable.

## Technical Scope

### In Scope

- SQL migration creating the `acquisition_state` table on the .144 TimescaleDB instance
- Rollback and validation scripts following the existing migration pattern (see `database/migrations/760_*`)
- New Python module(s) housing the orchestrator core and supporting types
- A `Granularity` StrEnum (or reuse if one exists) and a small set of status constants as a StrEnum — never magic strings
- Acquisition state DAO (`AcquisitionStateRepository` or similar) — pure read/write against the new table, no orchestration logic
- Orchestrator core: a single class or function implementing the shared loop, parameterized by callables/protocols for fetch, validate, write, and event emission
- Event emission scaffold: a minimal `AcquisitionEvent` dataclass and a writer interface with two implementations — append-only JSONL file and a no-op (for tests). No database table for events in this slice; Initiative 180 may formalize.
- `mt data state` Typer command that lists rows from `acquisition_state` with filters (symbol, granularity, provider, status) and renders them via the existing Rich output module
- Unit tests covering: orchestrator happy path, fetch failure → state updated with error, checkpoint write per chunk, resume-from-watermark behavior
- One integration test against a real test database confirming the migration applies cleanly and the DAO reads/writes correctly

### Out of Scope

- Any real provider implementation (slice 122 introduces `IDailyDataProvider`)
- Any daemon process or long-running loop (slices 123, 125)
- Any modification of `marketservice.py` or `HistoricalMinuteService`
- Any change to the existing `mt data daily update` / `mt data minute update` commands
- Event schema standardization (Initiative 180)
- Cross-host transactional guarantees (the architecture's "idempotent write + state update" pattern is documented, not enforced in this slice — there are no real writes yet)

## Data Model

### `acquisition_state` table

Lives on the .144 TimescaleDB host alongside the minute data. It is a regular PostgreSQL table, **not** a hypertable — it is small (one row per symbol-granularity-provider triplet) and updated in place.

| Column            | Type           | Notes                                                                |
|-------------------|----------------|----------------------------------------------------------------------|
| `symbol`          | `text`         | PK component                                                         |
| `granularity`     | `text`         | PK component; values from `Granularity` StrEnum (`daily`, `minute`, `tick`) |
| `provider`        | `text`         | PK component; values from existing provider registry enum           |
| `last_success_ts` | `timestamptz`  | Watermark — last successfully fetched bar/event timestamp. Nullable. |
| `last_attempt_ts` | `timestamptz`  | Last time the orchestrator tried this row, success or fail. Nullable.|
| `status`          | `text`         | `pending` / `in_progress` / `ok` / `failed` / `unfillable` (StrEnum) |
| `error_message`   | `text`         | Last error context. Nullable.                                        |
| `retry_count`     | `integer`      | Resets to 0 on success. Default 0.                                   |
| `run_id`          | `uuid`         | The run that last touched this row. Nullable until first write.      |
| `updated_at`      | `timestamptz`  | `default now()`, set on every UPSERT                                 |

**Primary key:** `(symbol, granularity, provider)` — exactly one row per triplet, enforced by the DB. All writes use UPSERT (`INSERT ... ON CONFLICT (symbol, granularity, provider) DO UPDATE`).

**Indexes:** PK is sufficient for slice 121. Slice 123/125 may add a `(status, last_attempt_ts)` index for daemon work-queue scans; deferred until then.

**Why not a hypertable:** This is current-state data, not time-series. There is no benefit to time partitioning, and being a regular table means standard UPSERT semantics work without TimescaleDB-specific concerns.

## Orchestrator Core Design

The core is a single coroutine (or class with one entry point) shaped roughly like:

```
run_acquisition_unit(
    work_item,            # what to fetch (symbol, granularity, provider, time range)
    provider,             # implements fetch(...)
    writer,               # implements write(rows) -> last_ts_written
    state_repo,           # AcquisitionStateRepository
    event_sink,           # implements emit(event)
    run_id,
) -> AcquisitionResult
```

Per call it:
1. Marks the row `in_progress`, sets `last_attempt_ts` and `run_id`.
2. Iterates fetch chunks (a chunk is provider-defined: a daily fetch may be one chunk; a minute fetch is one month). For each chunk:
   - Calls `provider.fetch(chunk)`.
   - Validates the response (delegates to provider's validator).
   - Calls `writer.write(rows)` and gets back the last actually-written timestamp.
   - UPSERTs `acquisition_state.last_success_ts = that_timestamp`, `status='ok'`, `retry_count=0`, emits a `chunk_ok` event.
3. On any chunk failure: emits `chunk_failed` event, increments `retry_count`, sets `status='failed'`, records `error_message`, returns. The caller (CLI or daemon) decides whether to retry the work item — the core does not loop on errors itself.
4. Returns an `AcquisitionResult` summarizing chunks attempted/written/failed.

**Critical property — checkpoint unit equals write unit.** A crash between chunks loses at most one in-flight chunk; completed chunks stay completed. The current `HistoricalMinuteService` violates this by gathering all chunks then writing once; the new core does not.

**Provider/writer are protocols, not concrete classes.** The core has no AlphaVantage or psycopg dependencies. Slice 122 wires the daily provider and `MarketDB` as implementations. Slice 124 does the same for minute.

**No async-in-sync.** The core is `async def`. Provider fetches are awaited; the writer protocol is sync (matches the architecture's "async fetch, sync store" rule) and called inside `asyncio.to_thread` if it would block meaningfully. Slice 122 will validate this against `MarketDB` in practice.

## CLI Surface

One new command in the existing `mt data` group:

```
mt data state [--symbol SYM] [--granularity G] [--provider P] [--status S]
```

Reads `acquisition_state` and renders a Rich table: symbol, granularity, provider, status, last_success_ts, last_attempt_ts, retry_count, error_message (truncated). With no filters, lists all rows (paginated if large). Read-only — no flags that mutate state in this slice.

This command exists in slice 121 specifically to prove the round trip: migration applied → DAO reachable → CLI renders rows. It is the smallest possible end-user-visible deliverable.

## File Layout

Tentative — subject to refinement during implementation. New files only; nothing renamed or removed.

- `database/migrations/770_create_acquisition_state.sql`
- `database/migrations/770_rollback_acquisition_state.sql`
- `database/migrations/770_validate_migration.sql`
- `src/manta_trading/data/acquisition/__init__.py`
- `src/manta_trading/data/acquisition/state.py` — `AcquisitionStateRepository`, status enum, granularity enum (or import existing)
- `src/manta_trading/data/acquisition/orchestrator.py` — the core coroutine, `WorkItem`, `AcquisitionResult`, provider/writer protocols
- `src/manta_trading/data/acquisition/events.py` — `AcquisitionEvent`, `EventSink` protocol, `JsonlEventSink`, `NullEventSink`
- `src/manta_trading/cli/commands/data.py` — extend with `state` subcommand (existing file)
- `tests/data/acquisition/test_state.py`
- `tests/data/acquisition/test_orchestrator.py`
- `tests/data/acquisition/test_events.py`

The exact module placement (`data/acquisition/` vs nesting under `data/historical_minute/`) will be decided during task planning. The slice 124 minute orchestrator will live alongside, so a sibling `data/acquisition/` directory is the current preference.

## Cross-Slice Dependencies and Interfaces

**Depends on:**
- Slice 100 (storage) — uses the same psycopg3 + connection pool patterns; the migration runner already exists.
- Slice 900 (foundation) — Typer, Rich output, Settings, structured logging.

**Provides for:**
- Slice 122 — `IDailyDataProvider` callers will plug into the orchestrator; daily writer wraps `MarketDB`.
- Slice 124 — minute orchestrator wraps `TimescaleMinuteDataDB` writer and the fixed `AlphaVantageMinuteProvider`.
- Slices 123, 125 — daemons import `run_acquisition_unit` and call it in their main loop.
- Initiative 140 — eventually consumes `acquisition_state` rows and the event log for quality reporting.
- Initiative 180 — may replace the JSONL event sink with a structured store.

**Interface stability:** the orchestrator signature, the state schema, and the event dataclass are the slice 121 contract. Later slices should not need to change them; if they do, that is a design signal worth surfacing.

## Success Criteria

- Migration `770_create_acquisition_state.sql` applies cleanly to a fresh test DB and is reversible by the rollback script.
- `acquisition_state` PK constraint enforced; UPSERT works as designed.
- `AcquisitionStateRepository` has unit tests covering insert, upsert, read-by-PK, and filtered list.
- Orchestrator core has unit tests for: happy path with multi-chunk fetch, fetch failure mid-stream, write failure mid-stream, watermark advances chunk-by-chunk, status/retry_count transitions correct.
- Event sink tests confirm JSONL output is well-formed and the null sink swallows correctly.
- `mt data state` runs against a test DB containing seeded rows and renders them correctly. With no rows, prints an empty-state message (no crash, no silent return).
- No magic strings: status values and granularity values come from StrEnums referenced from both the Python code and (via comments) the SQL migration.
- No existing acquisition behavior changes — `mt data daily update` and `mt data minute update` still work exactly as before, against unchanged code paths.

## Verification Walkthrough

Verified during Phase 6 implementation. Commands and expected output are accurate as of 2026-04-11.

**Setup**
1. `git checkout 121-slice.acquisition-state-schema-and-orchestrator-core`
2. Confirm the test TimescaleDB on .144 (or local test instance) is reachable and `MT_TIMESCALE_DB_URL` is set. Verify: `echo $MT_TIMESCALE_DB_URL`

**Migration**
3. Apply the migration:
   ```bash
   psql $MT_TIMESCALE_DB_URL -f database/migrations/770_create_acquisition_state.sql
   ```
   Expected: no errors; completes silently (table created with `IF NOT EXISTS`).

4. Validate:
   ```bash
   psql $MT_TIMESCALE_DB_URL -f database/migrations/770_validate_migration.sql
   ```
   Expected: 6 query blocks each returning at least one row confirming table, PK, columns, and defaults.

5. Inspect the schema:
   ```bash
   psql $MT_TIMESCALE_DB_URL -c '\d acquisition_state'
   ```
   Expected output includes columns: `symbol`, `granularity`, `provider`, `last_success_ts`, `last_attempt_ts`, `run_id`, `status`, `error_message`, `retry_count`, `updated_at`. PK constraint listed as `acquisition_state_pkey`.

**CLI — empty state**
6. `mt data state`
   Expected output:
   ```
   No acquisition state recorded.
   ```
   Exit code 0.

7. `mt data state --granularity bad_value`
   Expected output:
   ```
   Error: Invalid granularity 'bad_value'. Valid values: daily, minute, tick
   ```
   Exit code 1 (no stack trace).

**CLI — populated state**
8. Seed two rows from a Python REPL (requires `MT_TIMESCALE_DB_URL` set):
   ```python
   from psycopg_pool import ConnectionPool
   from manta_trading.data.acquisition.state import *
   pool = ConnectionPool(os.environ["MT_TIMESCALE_DB_URL"], min_size=1, max_size=2)
   repo = AcquisitionStateRepository(pool)
   repo.upsert(AcquisitionStateRow("AAPL", Granularity.DAILY, "alphavantage", AcquisitionStatus.OK))
   repo.upsert(AcquisitionStateRow("MSFT", Granularity.MINUTE, "alphavantage", AcquisitionStatus.FAILED, error_message="rate limit", retry_count=2))
   pool.close()
   ```

9. `mt data state` — expect a Rich table with both rows (AAPL and MSFT).

10. `mt data state --status failed` — expect only the MSFT row.

11. `mt data state --symbol AAPL` — expect only the AAPL row.

12. `mt data state --json` — expect JSON array with all rows; each object has `symbol`, `granularity`, `provider`, `status`, `last_success_ts`, `last_attempt_ts`, `retry_count`, `error_message` keys.

**Orchestrator (test harness)**
13. Run the full acquisition test suite:
    ```bash
    pytest test/unit/data/acquisition/ -v
    ```
    Expected: 63 passed, 6 skipped (integration tests skipped when `MT_TIMESCALE_DB_URL` not set).

14. Confirm the resume test passes specifically:
    ```bash
    pytest test/unit/data/acquisition/test_orchestrator.py::TestOrchestratorFetchFailure::test_chunks_before_failure_are_committed -v
    ```
    Expected: PASSED — watermark at chunk 0's end after chunk 1 fetch failure.

15. Confirm the resume property across runs:
    ```bash
    pytest test/unit/data/acquisition/test_orchestrator.py::TestOrchestratorResumeFromWatermark -v
    ```
    Expected: PASSED.

**Cleanup / no regression**
16. Confirm existing daily commands still work (requires `MT_MARKET_DB_URL` and credentials):
    ```bash
    mt data daily coverage
    ```
    Expected: summary table, no errors.

17. Confirm existing minute commands:
    ```bash
    mt data minute coverage
    ```
    Expected: fleet summary or symbol-specific output, no errors.

18. Rollback the migration:
    ```bash
    psql $MT_TIMESCALE_DB_URL -f database/migrations/770_rollback_acquisition_state.sql
    ```
    Expected: completes silently. Confirm table gone:
    ```bash
    psql $MT_TIMESCALE_DB_URL -c '\d acquisition_state'
    ```
    Expected: `Did not find any relation named "acquisition_state"`.

19. Re-apply for downstream slice work:
    ```bash
    psql $MT_TIMESCALE_DB_URL -f database/migrations/770_create_acquisition_state.sql
    ```

**Notes from implementation**
- The `async for` loop in the orchestrator must iterate via `__anext__()` directly to catch fetch-side exceptions (raised by the async generator during `yield`). A plain `async for` would propagate fetch exceptions unhandled; the explicit iterator loop allows them to be caught and turned into `CHUNK_FAILED` events.
- `mt data minute status` does not exist today; `mt data minute coverage` is the correct command.
- DB environment variable is `MT_TIMESCALE_DB_URL` (not `MT_MINUTE_DB_URL` as noted in the original draft).

## Notes

- The orchestrator core is intentionally small. If it grows past ~150 lines or starts taking on retry/scheduling concerns, that is a sign work belongs in the daemon slices (123/125), not the core.
- The event sink lives here rather than waiting for Initiative 180 because slices 122–125 will produce events from day one; without a sink, they would either invent one ad hoc or skip emission entirely. A JSONL file is the smallest viable target.
- The `mt data state` command is deliberately read-only. A future slice may add `mt data state reset SYMBOL` or similar, but mutation tools belong with the daemons that have a stake in the state.
- This slice does not include any retry logic. Retry policy is a daemon concern (slices 123, 125), and the CLI will call the orchestrator at most once per invocation.
