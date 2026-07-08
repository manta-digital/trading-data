---
docType: review
layer: project
reviewType: slice
slice: daily-provider-interface-and-alphavantage-daily-provider
project: squadron
verdict: PASS
sourceDocument: project-documents/user/slices/122-slice.daily-provider-interface-and-alphavantage-daily-provider.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260411
dateUpdated: 20260411
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Vertical-first sequencing is correct"
  - id: F002
    severity: pass
    category: interface-design
    summary: "Provider interface abstraction introduced as specified"
  - id: F003
    severity: pass
    category: dependency-management
    summary: "marketservice.py replacement aligns with \"rewrite what they didn't touch\" principle"
  - id: F004
    severity: pass
    category: code-quality
    summary: "Reuse of shared types avoids duplication"
  - id: F005
    severity: pass
    category: extensibility
    summary: "Shared rate limiter design enables future daemon coexistence"
  - id: F006
    severity: pass
    category: state-management
    summary: "Resumability via acquisition_state matches architecture"
  - id: F007
    severity: pass
    category: architecture-alignment
    summary: "CLI/daemon code sharing enforced via composition"
  - id: F008
    severity: pass
    category: observability
    summary: "Event emission via JsonlEventSink satisfies observability requirement"
  - id: F009
    severity: pass
    category: error-handling
    summary: "Error taxonomy handled at provider layer"
  - id: F010
    severity: pass
    category: performance-considerations
    summary: "Sequential batch loop defers concurrency appropriately"
  - id: F011
    severity: pass
    category: testing
    summary: "Integration test verifies resumability property"
  - id: F012
    severity: pass
    category: code-organization
    summary: "File layout respects module boundaries"
---

# Review: slice — slice 122

**Verdict:** PASS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] Vertical-first sequencing is correct

The slice implements daily OHLCV acquisition (the "simplest case" per the architecture's design goal: "vertical-first: equities daily, then equities minute"). The document explicitly acknowledges that minute data complexity (pagination, daemon coexistence) is deferred to slices 124/125.

### [PASS] Provider interface abstraction introduced as specified

The slice introduces `IDailyDataProvider` as a proper Protocol class, modeled on the existing `IMinuteDataProvider`. This matches the architecture's requirement: "Provider interface layer — `IDailyDataProvider` (new, modeled on existing `IMinuteDataProvider`)." The interface includes `fetch_daily_ohlcv`, `validate_response`, and `get_rate_limits` — the full contract surface needed for daemon introspection.

### [PASS] marketservice.py replacement aligns with "rewrite what they didn't touch" principle

The slice replaces the legacy `marketservice.py` daily orchestrator with `DailyAcquisitionOrchestrator` while explicitly leaving the file on disk until a later slice removes it. This matches the architecture's guidance: "This module is the daily acquisition orchestrator and is legacy. It should be replaced, not patched." The careful call-site-only rewiring avoids tangling the file removal with the orchestrator migration.

### [PASS] Reuse of shared types avoids duplication

`ValidationResult` and `RateLimitInfo` are reused from `manta_trading.data.historical_minute.provider` per the architecture's "reuse what 900/100 built" principle. The slice notes the option to lift them to `manta_trading.data.acquisition.types` if module placement becomes awkward — this is an acceptable evolution path, not a violation.

### [PASS] Shared rate limiter design enables future daemon coexistence

`AlphaVantageDailyProvider` accepts an injected `RateLimiter` so the daily and minute providers can share a single rate budget when both run in the same process. This directly supports the architecture's "Shared rate limit across daemons" consideration and enables slice 123/125 without refactoring the interface.

### [PASS] Resumability via acquisition_state matches architecture

The slice implements per-symbol watermarks and explicit state transitions (`pending → in_progress → ok/failed`) via the `AcquisitionStateRepository`. This correctly implements the architecture's "Progress is persisted, not inferred" principle. The primary key on `(symbol, granularity, provider)` and UPSERT semantics are consistent with the architecture's described schema.

### [PASS] CLI/daemon code sharing enforced via composition

Both `mt data daily update` CLI commands and the future daemon (slice 123) compose the same `DailyAcquisitionOrchestrator`. The architecture states: "CLI and daemon share the same orchestrator and state." The slice correctly achieves this by having CLI commands directly construct and invoke the orchestrator rather than spawning subprocesses.

### [PASS] Event emission via JsonlEventSink satisfies observability requirement

The slice emits `RUN_STARTED`, `CHUNK_OK`, `CHUNK_FAILED`, `RUN_FINISHED` via `JsonlEventSink`, with a fallback to `NullEventSink` when the event file is unwritable. This implements the architecture's "Observable and communicable" goal and pre-empts Initiative 180's event infrastructure needs.

### [PASS] Error taxonomy handled at provider layer

`validate_response` checks `Error Message`, `Note` (rate limit), `Information`, and empty time series — consistent with the architecture's "Provider error taxonomy" consideration. The architecture states: "The retry strategy should distinguish transient errors from permanent errors." By surfacing these at the provider layer, the orchestrator can propagate error context for retry decisions.

### [PASS] Sequential batch loop defers concurrency appropriately

The batch loop runs sequentially, with the `RateLimiter` handling pacing at 30 req/min. The slice explicitly defers concurrent symbol fetches: "a sequential loop of 500 symbols takes ~17 minutes. That is fine for the CLI case." This matches the architecture's guidance on concurrency while leaving room for the daemon slice to revisit.

### [PASS] Integration test verifies resumability property

`test_cli_update_all_resume` simulates a mid-run failure, verifies state rows after restart, and confirms that successfully-fetched data is not rewritten (via `INSERT ... ON CONFLICT DO NOTHING` semantics). This directly tests the architecture's most important capability: "A run that fails at symbol N leaves acquisition_state with N-1 rows marked ok... The next invocation skips the ok rows."

### [PASS] File layout respects module boundaries

New files live under `src/manta_trading/data/acquisition/daily/` with clear separation: `provider.py`, `providers/alphavantage.py`, `writer.py`, `orchestrator.py`, `freshness.py`. The existing `src/manta_trading/data/historical_minute/*` is untouched. This respects the architecture's intent to build a clean provider interface layer without co-mingling with the minute provider infrastructure.
