---
docType: review
layer: project
reviewType: slice
slice: acquisition-state-schema-and-orchestrator-core
project: squadron
verdict: PASS
sourceDocument: project-documents/user/slices/121-slice.acquisition-state-schema-and-orchestrator-core.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260407
dateUpdated: 20260407
findings:
  - id: F001
    severity: pass
    category: architectural-alignment
    summary: "Acquisition state table schema matches architecture"
  - id: F002
    severity: pass
    category: architectural-alignment
    summary: "Orchestrator core design implements architecture's critical property"
    location: "Orchestrator Core Design" section
  - id: F003
    severity: pass
    category: architectural-alignment
    summary: "Async fetch, sync store pattern correctly specified"
    location: "Orchestrator Core Design" section
  - id: F004
    severity: pass
    category: architectural-alignment
    summary: "CLI is the baseline, daemon is the target principle honored"
  - id: F005
    severity: pass
    category: scope-management
    summary: "Correctly scoped as foundation without modifying legacy code"
  - id: F006
    severity: pass
    category: scope-management
    summary: "Event emission scaffold provided at appropriate time"
  - id: F007
    severity: pass
    category: dependency-management
    summary: "Dependencies correctly identified"
  - id: F008
    severity: pass
    category: architectural-alignment
    summary: "Interface stability noted as contract"
    location: "Cross-Slice Dependencies and Interfaces" section
  - id: F009
    severity: pass
    category: scope-management
    summary: "Cross-host transaction pattern correctly deferred"
    location: "Out of Scope" section
  - id: F010
    severity: note
    category: documentation
    summary: "Interfaces array in frontmatter is empty"
    location: frontmatter
  - id: F011
    severity: note
    category: documentation
    summary: "Module placement marked as subject to refinement"
    location: "File Layout" section
---

# Review: slice — slice 121

**Verdict:** PASS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] Acquisition state table schema matches architecture

The `acquisition_state` table schema in slice 121 aligns precisely with the architecture's envisioned state. The primary key `(symbol, granularity, provider)`, the watermark column (`last_success_ts`), `last_attempt_ts`, `status`, `error_message`, `retry_count`, and `run_id` all match. The document correctly explains why this is a regular PostgreSQL table (not a hypertable): it's current-state data, not time-series.

### [PASS] Orchestrator core design implements architecture's critical property

The document explicitly states and implements **"checkpoint unit equals write unit"** — each chunk is fetched, validated, written, and checkpointed before the next begins. This directly addresses the architecture's identified problem with `HistoricalMinuteService` ("gathers all chunks then writes once") and is a key correctness requirement for resumability.

### [PASS] Async fetch, sync store pattern correctly specified

The design correctly specifies the provider as async (`provider.fetch(chunk)` is awaited) and the writer as a sync protocol called inside `asyncio.to_thread`. This matches the architecture's "async fetch, sync store" principle and avoids the async-in-sync antipattern.

### [PASS] CLI is the baseline, daemon is the target principle honored

The document explicitly states that CLI and daemon share the same orchestrator core: "CLI will call the orchestrator at most once per invocation" while slices 123/125 "call it in a loop." This matches the architecture's principle that both CLI and daemon "update acquisition state" using "the same fetch→validate→write→update-watermark code path."

### [PASS] Correctly scoped as foundation without modifying legacy code

The slice explicitly excludes modifications to `marketservice.py`, `HistoricalMinuteService`, and existing `mt data daily/minute update` commands. It introduces new infrastructure alongside existing code, which aligns with the architecture's "rewrite what they didn't touch" principle and the vertical-first approach.

### [PASS] Event emission scaffold provided at appropriate time

The document acknowledges that Initiative 180 may formalize events but correctly argues that providing a JSONL event sink in this slice prevents later slices from inventing ad-hoc solutions or skipping event emission entirely. The no-op sink for tests is a sound testing practice.

### [PASS] Dependencies correctly identified

The slice depends on slice 100 (storage — psycopg3 patterns, migration runner) and slice 900 (foundation — Typer, Rich, Settings). The downstream consumers (slices 122–125) are documented as "Provides for" with interface contracts (`run_acquisition_unit`, state schema, event dataclass).

### [PASS] Interface stability noted as contract

The document explicitly identifies the orchestrator signature, state schema, and event dataclass as the slice 121 contract, noting that later changes would be "a design signal worth surfacing." This is good architectural governance for a foundation slice.

### [PASS] Cross-host transaction pattern correctly deferred

The document notes that "cross-host transactional guarantees... are documented, not enforced in this slice — there are no real writes yet." The architecture explicitly describes this pattern for daily data (write to .95, then state update on .144) with the idempotent-write safety net. Deferring enforcement until there are actual writes is correct.

### [NOTE] Interfaces array in frontmatter is empty

The `interfaces: []` field is empty, though the "Cross-Slice Dependencies and Interfaces" section thoroughly documents the integration points. This is not an architectural violation but could be populated for consistency with other slice documents if the project uses this field programmatically.

### [NOTE] Module placement marked as subject to refinement

The document acknowledges that module placement (`data/acquisition/` vs `data/historical_minute/`) will be decided during task planning. This is acceptable for a slice design document, but the chosen location should be confirmed before implementation begins to avoid fragmentation.
