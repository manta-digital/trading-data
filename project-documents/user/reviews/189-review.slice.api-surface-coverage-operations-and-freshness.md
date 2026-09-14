---
docType: review
layer: project
reviewType: slice
slice: api-surface-coverage-operations-and-freshness
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/189-slice.api-surface-coverage-operations-and-freshness.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260913
dateUpdated: 20260913
reviewedSha: ca0cd6b92fa2bfc18d693be3097c0eecfb9c2afc
findings:
  - id: F001
    severity: concern
    category: consistency
    summary: "`/credits` publishes a 504 schema its own failure table says it can never return"
    location: "project-documents/user/slices/189-slice.api-surface-coverage-operations-and-freshness.md:251"
  - id: F002
    severity: concern
    category: test-coverage
    summary: "No load test planned for `/credits` despite it being a network I/O path, contrary to the project's load-test-tier rule"
    location: "project-documents/user/slices/189-slice.api-surface-coverage-operations-and-freshness.md:385"
  - id: F003
    severity: pass
    category: architecture-alignment
    summary: "Reuse of `build_overview`/`gather` honors the architecture's Thin Wrapper principle"
    location: "project-documents/user/slices/189-slice.api-surface-coverage-operations-and-freshness.md:158-182"
  - id: F004
    severity: pass
    category: correctness
    summary: "Dependency direction and interface references verified against current code"
    location: "project-documents/user/slices/189-slice.api-surface-coverage-operations-and-freshness.md:90-100"
---

# Review: slice — slice 189

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] `/credits` publishes a 504 schema its own failure table says it can never return

The API Specification section states both routes declare `504` via `GATEWAY_TIMEOUT_RESPONSE` (line 251). But `GATEWAY_TIMEOUT_RESPONSE`'s own description (`src/manta_trading/api_server/models/responses.py:280-291`) is specifically "The database cancelled the query at the server's statement timeout... narrow the requested range" — and D2's own cost table (lines 140-146) puts `/credits` on a completely different axis: source is "Outbound HTTPS to EODHD," bound is `EODHD_ACCOUNT_TIMEOUT_SECONDS`, not `statement_timeout`. D6's failure-mode table (lines 225-236) enumerates exactly one outcome for "EODHD unreachable": `200` with `credits: null` and a redacted `error` string — no `504` case appears anywhere for this route, and the route makes no DB call at all (confirmed in Data Flow, lines 338-342, and Testing Strategy line 385: "`/api/v1/credits` gets no load bound — its latency is a third party's"). Declaring `GATEWAY_TIMEOUT_RESPONSE` on `/credits` publishes a `504` entry in `openapi.json` with a misleading description ("narrow the requested range" — the route takes no parameters at all) for a status code the route's own design says it will never emit. Slice 190 and trading-ui are stated consumers of this exact `openapi.json` (line 107, SC9); this would ship an inaccurate published contract. Failure scenario: a client integrating against the generated schema builds retry/backoff logic around a `504` on `/credits` that the implementation never sends, or reads the auto-generated docs and concludes a narrower request could avoid a timeout that has nothing to do with request shape.

### [CONCERN] No load test planned for `/credits` despite it being a network I/O path, contrary to the project's load-test-tier rule

The project's Python rules (`python.md`) state: "Load-test tier (`tests/load/`): any code on the simulation, network, concurrency, or environment-layer paths **requires** at least one load test exercising a realistic configuration. Load tests assert on latency, throughput, or resource bounds — not just functional correctness." `/api/v1/credits` makes an outbound synchronous `httpx.get` call dispatched via `loop.run_in_executor(None, ...)` — the bare `None` executor argument means it shares the single default thread-pool executor with every other route in the process (confirmed: `bars.py`, `status.py`, `symbols.py`, `gaps.py` all use the same `run_in_executor(None, ...)` pattern against one process-wide pool). The design explicitly opts out — "gets no load bound — its latency is a third party's" — addressing only the *latency* half of the rule and not the *resource-bound* half: a load test here would legitimately probe whether concurrent or slow (up to `EODHD_ACCOUNT_TIMEOUT_SECONDS=5.0s`) `/credits` calls can starve the shared executor that `/overview`, `/bars`, and every other route also depend on. Failure scenario: several clients poll `/credits` around the same time EODHD is slow to respond; each occupies a shared worker thread for up to 5s, and with the default executor sized `min(32, cpu+4)`, a burst of slow credit calls measurably delays unrelated `/bars`/`/overview` requests queued behind them — exactly the class of condition the load tier exists to catch and that unit/integration tests cannot.

### [PASS] Reuse of `build_overview`/`gather` honors the architecture's Thin Wrapper principle

180-arch's "Design Principle: Thin Wrapper" requires the API to call existing methods rather than contain business logic, adding new capability to the data-access layer first. D3's `gather_db_facts` extraction is a verified ~20-line split of an existing, already-proven function (confirmed present at `src/manta_trading/cli/commands/overview.py:173-239`), with no new derivation logic in `api_server/`. The `api_server → cli` import direction cited as precedent (`routes/status.py` importing `HealthStatus`) is real, not asserted speculatively.

### [PASS] Dependency direction and interface references verified against current code

Every symbol in the "Interfaces required" table was checked against the codebase and exists with the signature described: `build_overview(facts, *, pid_alive=...)`, `PassRunRepository.open_runs`/`.latest_ended`, `read_source_freshness`, `fetch_credit_usage`/`CreditUsage`, `pid_is_alive`, `get_db`, and the `PassKind`/`PassRunOutcome` enum members named in the API spec. D4's abandonment rationale accurately reflects `_running_row`'s `abandoned=mine and not pid_alive(run.pid)` logic. No hallucinated interfaces found.
