---
docType: slice-tasks
slice: acquisition-state-schema-and-orchestrator-core
project: trading
parent: user/slices/121-slice.acquisition-state-schema-and-orchestrator-core.md
dependencies: [100, 900]
dateCreated: 20260407
dateUpdated: 20260411
status: complete
---

# Tasks: Acquisition State Schema and Orchestrator Core

## Context

Foundation slice for Initiative 120. Creates the `acquisition_state` table on .144, the shared orchestrator core (fetch → validate → write → checkpoint), an event emission scaffold, and a read-only `mt data state` CLI command. No provider implementations, no daemon, no changes to existing acquisition code.

See parent slice design for full rationale and interface contracts.

## Task Order Rationale

Schema first (the contract everything else writes to), then enums and DAO (Python access to that contract), then event scaffold (needed by orchestrator), then orchestrator core (composes the above), then CLI (proves end-to-end reachability), then integration tests. Each implementation task is immediately followed by its test task.

---

## 1. Migration: `acquisition_state` Table

- [x] **1.1** Create `database/migrations/770_create_acquisition_state.sql`
  - Table: `acquisition_state`, regular PostgreSQL table (not hypertable)
  - Columns per slice design data model section
  - PK: `(symbol, granularity, provider)`
  - `updated_at timestamptz NOT NULL DEFAULT now()`
  - `retry_count integer NOT NULL DEFAULT 0`
  - Add inline SQL comments listing the StrEnum values that `granularity` and `status` accept (cross-reference the Python enum)
  - Idempotent: `CREATE TABLE IF NOT EXISTS`
  - Effort: 1

- [x] **1.2** Create `database/migrations/770_rollback_acquisition_state.sql`
  - `DROP TABLE IF EXISTS acquisition_state;`
  - Effort: 1

- [x] **1.3** Create `database/migrations/770_validate_migration.sql`
  - Verify table exists, PK exists, all expected columns present with expected types
  - Follow the pattern in `database/migrations/760_validate_migration.sql`
  - Effort: 1

- [x] **1.4** Update `database/migrations/README.md` with the 770 entry
  - Effort: 1

- [x] **1.5** **Test** — Apply, validate, rollback, re-apply against a local test TimescaleDB instance
  - All three SQL files run without error
  - Validation script reports all checks pass
  - Rollback leaves no `acquisition_state` table behind
  - Re-application is clean
  - Effort: 1

---

## 2. Enums and Module Skeleton

- [x] **2.1** Create directory `src/manta_trading/data/acquisition/` with `__init__.py`
  - Effort: 1

- [x] **2.2** Define `Granularity` StrEnum in `src/manta_trading/data/acquisition/state.py`
  - Values: `DAILY = "daily"`, `MINUTE = "minute"`, `TICK = "tick"`
  - First check the codebase for an existing granularity enum; if one exists in a sensible shared location, import it instead and skip creating a new one. Document the choice in the file header.
  - Effort: 1

- [x] **2.3** Define `AcquisitionStatus` StrEnum in the same module
  - Values: `PENDING = "pending"`, `IN_PROGRESS = "in_progress"`, `OK = "ok"`, `FAILED = "failed"`, `UNFILLABLE = "unfillable"`
  - Effort: 1

- [x] **2.4** **Test** — `tests/data/acquisition/test_enums.py`
  - Confirm enum values match the strings stored in SQL (the migration's inline comments are the source of truth)
  - Confirm both enums are `StrEnum` subclasses (string equality works)
  - Effort: 1

---

## 3. Acquisition State Repository (DAO)

- [x] **3.1** Define `AcquisitionStateRow` dataclass in `state.py`
  - Fields mirror the table columns; use `datetime | None` for nullable timestamps
  - Effort: 1

- [x] **3.2** Implement `AcquisitionStateRepository` in `state.py`
  - Constructor takes a psycopg3 `ConnectionPool` (match the pattern used in `MarketDB` / `TimescaleMinuteDataDB`)
  - Method `upsert(row: AcquisitionStateRow) -> None` — `INSERT ... ON CONFLICT (symbol, granularity, provider) DO UPDATE SET ...` with `updated_at = now()`
  - Method `get(symbol, granularity, provider) -> AcquisitionStateRow | None`
  - Method `list(*, symbol=None, granularity=None, provider=None, status=None) -> list[AcquisitionStateRow]` — all filters optional, AND-combined
  - All SQL parameterized; never string-format values into queries
  - Effort: 2

- [x] **3.3** **Test** — `tests/data/acquisition/test_state.py` (integration, requires test DB)
  - Skip cleanly if test DB unavailable (existing pattern)
  - Test: insert via upsert, read back via `get`, fields round-trip correctly (including `None` values)
  - Test: upsert second time updates existing row, does not create duplicate
  - Test: `list()` with no filters returns all seeded rows
  - Test: `list(status=AcquisitionStatus.FAILED)` returns only matching rows
  - Test: combined filters narrow correctly
  - Test: `get` returns `None` for missing PK
  - Effort: 2

---

## 4. Event Emission Scaffold

- [x] **4.1** Create `src/manta_trading/data/acquisition/events.py`
  - Define `AcquisitionEventType` StrEnum with at least: `CHUNK_OK`, `CHUNK_FAILED`, `RUN_STARTED`, `RUN_FINISHED`
  - Effort: 1

- [x] **4.2** Define `AcquisitionEvent` dataclass
  - Fields: `event_type: AcquisitionEventType`, `run_id: UUID`, `symbol: str`, `granularity: Granularity`, `provider: str`, `timestamp: datetime`, `rows_written: int | None`, `time_range_start: datetime | None`, `time_range_end: datetime | None`, `duration_ms: int | None`, `error: str | None`
  - Effort: 1

- [x] **4.3** Define `EventSink` Protocol with one method: `emit(event: AcquisitionEvent) -> None`
  - Effort: 1

- [x] **4.4** Implement `NullEventSink` — `emit` is a no-op
  - Effort: 1

- [x] **4.5** Implement `JsonlEventSink`
  - Constructor takes a file path; opens append mode lazily on first emit
  - Each emit writes one JSON object terminated by `\n`
  - Datetimes serialized as ISO-8601, UUIDs as strings, enums as their string values
  - `close()` method to flush/close the file handle
  - Effort: 2

- [x] **4.6** **Test** — `tests/data/acquisition/test_events.py`
  - `NullEventSink` accepts events without error
  - `JsonlEventSink` writes one well-formed JSON line per event (parseable back via `json.loads`)
  - All field types serialize correctly (datetime, UUID, enum)
  - Multiple events append without overwriting
  - Effort: 2

---

## 5. Orchestrator Core

- [x] **5.1** Create `src/manta_trading/data/acquisition/orchestrator.py`
  - Effort: 1

- [x] **5.2** Define supporting types
  - `WorkItem` dataclass: `symbol`, `granularity`, `provider`, `time_range_start`, `time_range_end`
  - `ChunkResult` dataclass: `last_written_ts: datetime | None`, `rows_written: int`
  - `AcquisitionResult` dataclass: `chunks_attempted: int`, `chunks_written: int`, `chunks_failed: int`, `final_status: AcquisitionStatus`, `last_error: str | None`
  - Effort: 1

- [x] **5.3** Define provider/writer protocols
  - `ChunkProvider` Protocol: `async def fetch_chunks(work_item: WorkItem) -> AsyncIterator[FetchedChunk]` — provider yields chunks one at a time
  - `FetchedChunk` dataclass: `rows: Any` (provider-specific), `chunk_start: datetime`, `chunk_end: datetime`
  - `ChunkWriter` Protocol: `def write(chunk: FetchedChunk) -> ChunkResult` — sync; returns the actual last timestamp written (may be earlier than `chunk_end` if provider returned partial data)
  - Effort: 2

- [x] **5.4** Implement `run_acquisition_unit` async function
  - Signature: `async def run_acquisition_unit(work_item, provider, writer, state_repo, event_sink, run_id) -> AcquisitionResult`
  - Step 1: Upsert state row to `IN_PROGRESS` with `last_attempt_ts = now()`, `run_id`
  - Step 1b: Emit `RUN_STARTED` event
  - Step 2: For each chunk yielded by `provider.fetch_chunks(work_item)`:
    - Call `writer.write(chunk)` (use `asyncio.to_thread` to run the sync writer)
    - On success: upsert state with `last_success_ts = chunk_result.last_written_ts`, `status=OK`, `retry_count=0`, emit `CHUNK_OK`
    - On exception: capture error, increment `retry_count`, upsert state with `status=FAILED`, `error_message=str(exc)`, emit `CHUNK_FAILED`, **break** (do not attempt further chunks in this unit)
  - Step 3: Emit `RUN_FINISHED` event with final result
  - Step 4: Return `AcquisitionResult`
  - **Critical:** the orchestrator does not retry on its own — that is the daemon's responsibility (slices 123, 125)
  - **Critical:** never catch exceptions silently — every failure path emits an event and updates state
  - Effort: 3

- [x] **5.5** **Test** — `tests/data/acquisition/test_orchestrator.py` (unit, fakes only)
  - Build `FakeProvider`, `FakeWriter`, `FakeEventSink`, in-memory state repo (or use real DAO with test DB if simpler)
  - Test: happy path — provider yields 3 chunks, all write successfully; final state `OK`, `last_success_ts` equals last chunk's `last_written_ts`, 3 `CHUNK_OK` events emitted, `RUN_FINISHED` emitted
  - Test: fetch failure on chunk 2 — chunks 1 written and committed, state shows `last_success_ts` from chunk 1 (NOT chunk 0 or original watermark), final status `FAILED`, retry_count incremented, `CHUNK_FAILED` emitted
  - Test: writer failure on chunk 2 — same as above but exception originates from writer
  - Test: `last_written_ts` < `chunk_end` (partial response) — watermark reflects actual written ts, not requested end
  - Test: `RUN_STARTED` and `RUN_FINISHED` always emitted, even on failure
  - Test: subsequent run after failure — watermark from failed run is preserved (resume point exists)
  - Effort: 3

---

## 6. CLI: `mt data state`

- [x] **6.1** Inspect `src/manta_trading/cli/commands/data.py` to understand the existing Typer structure and the `mt data` command group
  - No code changes; this is orientation
  - Effort: 1

- [x] **6.2** Add `state` subcommand to `data.py`
  - `mt data state [--symbol SYM] [--granularity G] [--provider P] [--status S]`
  - Granularity and status options accept the StrEnum values; validate by attempting to construct the enum and raising a Typer-friendly error on bad input
  - Build an `AcquisitionStateRepository` from the configured connection pool (follow how other `mt data` commands acquire DB handles)
  - Call `repo.list(...)` with the filters
  - Render results via the existing Rich output module (`src/manta_trading/cli/output.py`)
  - Columns: symbol, granularity, provider, status, last_success_ts, last_attempt_ts, retry_count, error_message (truncated to ~40 chars)
  - Empty result: print a clear "no acquisition state recorded" message; exit 0
  - Effort: 2

- [x] **6.3** **Test** — manual smoke test (document in slice walkthrough)
  - Empty table → friendly message
  - Seed two rows via REPL/fixture → both render
  - Each filter narrows correctly
  - Bad enum value → friendly Typer error, not a stack trace
  - Effort: 1

---

## 7. End-to-End Integration Verification

- [x] **7.1** Run the full slice walkthrough from the slice design's "Verification Walkthrough" section against a test DB
  - Migration apply → validate → CLI empty → seed → CLI populated → filtered queries → orchestrator pytest run → existing commands still work → rollback
  - Effort: 1

- [x] **7.2** Confirm no regressions in existing acquisition commands
  - `mt data daily coverage AAPL` (or equivalent) still works against unchanged code paths
  - Effort: 1

---

## 8. Wrap-up

- [x] **8.1** Run full test suite: `pytest` — all tests pass, including the new ones
  - Effort: 1

- [x] **8.2** Lint / type check per project standards
  - Effort: 1

- [x] **8.3** Verify file size budgets — each new module ≤ ~300 lines, functions ≤ ~50 lines
  - Effort: 1

- [x] **8.4** Self-review against slice design success criteria — every bullet checked off
  - Effort: 1

- [x] **8.5** Commit on slice branch with semantic message: `feat: add acquisition state schema and orchestrator core`
  - Effort: 1

---

## Notes for the Implementer

- **No magic strings.** Status and granularity values come from the StrEnums defined in `state.py`. The SQL migration uses string literals only because PostgreSQL has no enum import; the inline comments must list the accepted values and reference the Python enum.
- **Async fetch, sync store.** The orchestrator is `async`; the writer protocol is sync. Use `asyncio.to_thread` when calling the writer so the event loop is not blocked.
- **No silent fallbacks.** If state cannot be written, raise. If an event cannot be emitted, log and continue (event emission is best-effort scaffolding in this slice; the data write is the source of truth).
- **Resume property is the slice's reason for existing.** The orchestrator test that proves "watermark advances chunk-by-chunk and survives mid-stream failure" is the most important test in this slice. If it does not pass, the slice does not pass.
- **Out of scope reminders:** no provider implementations, no daemon, no changes to `marketservice.py` or `HistoricalMinuteService`, no retry loops, no event database table.
