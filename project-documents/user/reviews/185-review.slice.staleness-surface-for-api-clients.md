---
docType: review
layer: project
reviewType: slice
slice: staleness-surface-for-api-clients
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/185-slice.staleness-surface-for-api-clients.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260803
dateUpdated: 20260803
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "Failure modes for new I/O paths not enumerated"
    location: unverified
  - id: F002
    severity: concern
    category: scope-creep
    summary: "Scope overlap with slice 186 on `bars.py` and `responses.py`"
    location: input#Technical Scope
  - id: F003
    severity: concern
    category: under-specification
    summary: "`status.py` route handler structure not specified"
    location: input#Data Flow
  - id: F004
    severity: concern
    category: nfr
    summary: "No explicit latency NFR restatement for the new bars path"
    location: input#Data Flow
  - id: F005
    severity: note
    category: architectural-alignment
    summary: "`get_db` reuse and `bars.py` dependency change is well-justified"
    location: input#D8
  - id: F006
    severity: note
    category: architectural-alignment
    summary: "D4 (no auto-extend on API path) is a correct architectural boundary"
    location: input#D4
  - id: F007
    severity: note
    category: architectural-alignment
    summary: "Thin-wrapper principle adherence is strong"
    location: input#Component Structure
  - id: F008
    severity: pass
    category: architectural-alignment
    summary: "Dependencies and integration points match consuming/providing slices"
    location: input#Cross-Slice Dependencies and Interfaces
---

# Review: slice — slice 185

**Verdict:** CONCERNS
**Model:** minimax/minimax-m3

## Findings

### [CONCERN] Failure modes for new I/O paths not enumerated

The slice introduces three new I/O paths (well, two — health gains a new `check_coverage_freshness` call, status is entirely new, bars gains a new `assert_cagg_fresh` call). The review criteria require explicit handling strategy for hang/timeout/peer-disconnect-mid-send on each new I/O path, not "TBD" or implicit. The slice does not discuss what happens when:
- `status_queries.fetch_status_rows_with_freshness` raises a DB exception (e.g. connection drop in the pool, timeout).
- `assert_cagg_fresh` blocks longer than expected inside `run_in_executor` (the slice notes ~2.1s cold-cache probe but does not state a timeout or what happens if it exceeds it).
- `check_coverage_freshness` raises while `db == "ok"` has already been determined, leaving the response shape in an undefined state (the schema requires `coverage: "ok" | "stale" | None`, but no strategy is specified for exception).
- The `asyncio.gather` in bars (D7) encounters an exception in one of the two tasks — does the bars fetch get cancelled? Returned without `is_stale`?

The slice inherits 167/168 behavior, but the architecture's review criteria require the *slice doc itself* to enumerate failure modes for new I/O paths, not defer to upstream.

### [CONCERN] Scope overlap with slice 186 on `bars.py` and `responses.py`

The slice explicitly states it defers "Bars range-cap policy, `openapi.json` version fix, pool timeout tuning — all slice 186" yet also touches `bars.py` (D7, D8) and `responses.py` (extending `BarsResponse`). Slice 186's scope as described here is implicit — it is mentioned as "all slice 186" but the file boundaries between 185 and 186 are not enumerated in the architecture or slice plan referenced. The slice should explicitly state which fields/sections of `bars.py` it owns vs. which it defers, to avoid merge conflicts and clarify ownership. As written, a future reviewer of 186 cannot tell from this doc whether 186 is "tune the existing `get_bars` connection pool" (clearly 186) or "add the range-cap logic to `bars.py`" (potentially overlapping with 185's D7 changes to the same handler).

### [CONCERN] `status.py` route handler structure not specified

The Data Flow section for `GET /api/v1/status` shows the call chain but does not specify whether the route handler is `async def` or `def`. By contrast, D6 explicitly notes that `health()` remains a sync handler because FastAPI runs sync handlers in a thread pool, and D7 shows bars is `async def` with `run_in_executor` wrapping. The status route is the most complex of the three (two blocking DB calls, mapping logic) and yet has no equivalent statement. This is inconsistent with the precision given to the other two routes and leaves an ambiguity the implementer will have to resolve without architectural guidance — a gap given the rest of the doc's care.

### [CONCERN] No explicit latency NFR restatement for the new bars path

The architecture doc (180-arch) does not appear to restate specific latency NFRs in the excerpt provided, but the slice introduces a new probe running concurrently with the bars fetch via `asyncio.gather`. The doc claims "does not serialize behind the data fetch" but does not state a concrete latency target or measurement expectation. The risk section acknowledges up to ~2.1s cold-cache cost but defers any latency assertion to "if the implementer judges it warranted." If 180-arch defines any latency NFR for `/api/v1/bars` (even implicitly via "single-user local network tool"), this slice's change to that path should restate the NFR with the specific target — per review criteria.

### [NOTE] `get_db` reuse and `bars.py` dependency change is well-justified

D8's decision to add `db: Annotated[psycopg.Connection, Depends(get_db)]` to `get_bars` is correctly grounded in the architecture's stated principle of reusing the existing pooled-connection dependency ("The API shares the existing `Settings` and database configuration — no new connection config"). The note that `get_minute_db`/`get_daily_db` are direct instances, not pool-backed, is a clean observation of the existing state, and the resolution (add a `get_db` dep alongside) is the minimum-surface change.

### [NOTE] D4 (no auto-extend on API path) is a correct architectural boundary

The decision to not wire `maybe_extend_trading_sessions(..., bypass_gate=True)` into `GET /api/v1/status` is well-reasoned: the architecture doc explicitly excludes write paths from the API ("The API serves stored data... The API does not contain business logic"), and triggering a write side effect from a read endpoint violates that boundary. D4 is the most consequential architectural decision in the slice and is correctly defended.

### [NOTE] Thin-wrapper principle adherence is strong

The slice's stated principle — "No SQL lives in this route — consistent with the 'thin wrapper, no new business logic' principle in the 180-arch doc" — is enforced by the architecture: the `status_queries` and `cagg_freshness` modules already do the work, and the slice is purely call-site wiring. This is exactly the layering the architecture prescribes (new query capability in the data access layer first, then exposed via endpoint).

### [PASS] Dependencies and integration points match consuming/providing slices

The slice correctly identifies 167 and 168 as prerequisites (their freshness machinery is the actual implementation), 184 as the existing API surface to extend, and 187 as the downstream consumer that benefits from this slice's `is_stale`/`status` groundwork. The interfaces listed (`FreshnessVerdict`, `CoverageFreshness`, `StalenessSignal`, `COVERAGE_VIEWS`, `GRANULARITY_SOURCE`, `CAGG_BASE_GRANULARITY`, `get_db`) are all sourced from slices that are complete, and the reuse pattern (no new logic, only new call sites) matches the architecture's thin-wrapper intent.
