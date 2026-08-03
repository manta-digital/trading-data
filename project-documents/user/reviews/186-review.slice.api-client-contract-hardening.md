---
docType: review
layer: project
reviewType: slice
slice: api-client-contract-hardening
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/186-slice.api-client-contract-hardening.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260803
dateUpdated: 20260803
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "D6 unified error body advances the arch's stated consistency goal"
    location: 186-slice.api-client-contract-hardening.md#D6 — One error-body shape for every error this codebase raises
  - id: F002
    severity: pass
    category: uncategorized
    summary: "No-pagination stance preserved"
    location: 186-slice.api-client-contract-hardening.md#D4 — Bars range cap: pre-query admission check, `422`, no pagination
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Auth/CORS posture matches arch, recorded as a decision rather than omission"
    location: 186-slice.api-client-contract-hardening.md#D8 — Auth and CORS posture: recorded decision, no change
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Breaking contract changes are explicitly flagged"
    location: 186-slice.api-client-contract-hardening.md#Risks
  - id: F005
    severity: note
    category: uncategorized
    summary: "Code-location path drift between arch and slice (inherited from 184/185)"
    location: 186-slice.api-client-contract-hardening.md (Technical Scope) vs 180-arch.data-serving.md#Code Location
  - id: F006
    severity: note
    category: uncategorized
    summary: "New `MT_API_*` settings partially exceed \"no new connection config\" boundary"
    location: 186-slice.api-client-contract-hardening.md#D9 — The two policy ceilings are operator-settable
  - id: F007
    severity: concern
    category: scope-creep
    summary: "D4 range cap contradicts the architecture's \"trust callers\" stance"
    location: 186-slice.api-client-contract-hardening.md#D4 — Bars range cap: pre-query admission check, `422`, no pagination
  - id: F008
    severity: concern
    category: scope-creep
    summary: "D5 contract change contradicts the architecture's documented 404 semantics"
    location: 186-slice.api-client-contract-hardening.md#D5 — `404` means "unknown symbol"; an empty window is `200`
  - id: F009
    severity: concern
    category: layer-boundary
    summary: "D5 instrument-existence lookup in the route violates the thin-wrapper design principle"
    location: 186-slice.api-client-contract-hardening.md#D5 — `404` means "unknown symbol"; an empty window is `200`
  - id: F010
    severity: concern
    category: error-handling
    summary: "New DB I/O path introduced by D5 has no enumerated failure modes or handling strategy"
    location: 186-slice.api-client-contract-hardening.md#D5 — `404` means "unknown symbol"; an empty window is `200`
  - id: F011
    severity: concern
    category: layer-boundary
    summary: "D1 session-settings plumbing touches a layer boundary the arch declares out of scope"
    location: 186-slice.api-client-contract-hardening.md#D1 — Session settings reach all three pools, via a plumbed argument
---

# Review: slice — slice 186

**Verdict:** CONCERNS
**Model:** minimax/minimax-m3

## Findings

### [PASS] D6 unified error body advances the arch's stated consistency goal

The architecture states "All error responses use a consistent shape: `{"error": "<message>"}`" (180-arch.data-serving.md#Error Handling). The slice identifies that three shapes are in fact in circulation and moves them all to that one shape, with FastAPI's `RequestValidationError` as a documented exception. This is fixing an implementation gap against the arch's stated intent, not violating it. The exception is justified and the deliberate asymmetry is recorded.

### [PASS] No-pagination stance preserved

The architecture is explicit ("No server-side pagination. The API trusts callers to request bounded ranges" — 180-arch.data-serving.md#Range Policy). The slice explicitly considers and rejects pagination as an alternative, citing the same rationale. The chosen mechanism (admission cap, not pagination) leaves the no-pagination architectural decision intact.

### [PASS] Auth/CORS posture matches arch, recorded as a decision rather than omission

The architecture states no auth, permissive CORS, LAN-only, read-only. The slice repeats the posture verbatim, adds the binding/network reasoning ("binds 0.0.0.0:8100 on a LAN host and is not internet-exposed"), and names the reversal triggers (writes, internet exposure, multi-user). This tightens the arch's stated posture by making its rationale and triggers explicit — a positive change, not a drift.

### [PASS] Breaking contract changes are explicitly flagged

The slice's Risks section names both breaking changes (D5, D6) with mitigation in the same release, regenerated schema, CHANGELOG entry, and README note. This is the right shape for a contract-changing slice; the warnings are loud and centralized.

### [NOTE] Code-location path drift between arch and slice (inherited from 184/185)

The architecture specifies `src/manta_trading/api/...` but the slice (and its landed dependencies 184/185) consistently uses `src/manta_trading/api_server/...`. The slice inherits this drift; the architecture document has not been updated since 2026-05-13. The slice plan entry the document references presumably records the rename, but the parent architecture does not — meaning the drift will persist unless 180 is refreshed. Worth noting so this slice does not become the canonical source of an unrecorded rename.

### [NOTE] New `MT_API_*` settings partially exceed "no new connection config" boundary

The arch says "The API shares the existing Settings and database configuration — no new connection config" (180-arch.data-serving.md#Technical Stack). `MT_API_MAX_BARS_PER_REQUEST` is application-level policy and fine. `MT_API_STATEMENT_TIMEOUT` is applied per-connection to every backend in every API-owned pool (D1) — that is connection configuration in the strict sense. The slice's justification (operator tuning) is sound, but this is a real expansion of the arch's "no new connection config" constraint and should be reflected in 180 if it is to stand.

### [CONCERN] D4 range cap contradicts the architecture's "trust callers" stance

The arch doc is explicit: "No server-side pagination. The API trusts callers to request bounded ranges. A full year of 1-minute data for one symbol is ~98k bars (~6 MB JSON, ~2.5 MB msgpack) — acceptable for a single-user local network tool. If the UI requests an unreasonable range, the DB query will be slow and the response large; that is a UI concern, not an API concern at this scale." (180-arch.data-serving.md#Range Policy). D4 introduces a server-side admission policy that rejects unreasonable ranges before any DB work. The slice's measurement-driven rationale (extended-hours density, executor starvation risk) is good, but the architectural stance it is replacing is unambiguous, and the replacement should be recorded in the architecture — not introduced silently under "hardening." At minimum the slice should explicitly note "supersedes 180 §Range Policy" and request an architecture update.

### [CONCERN] D5 contract change contradicts the architecture's documented 404 semantics

The arch doc states: "**404** — symbol not found in instruments table, **or** no data in requested range." (180-arch.data-serving.md#Error Handling). D5 splits this single 404 condition into two: unknown symbol → 404, known-but-empty window → 200 with `count: 0`. The slice correctly identifies this as a breaking contract change and lists mitigation, but it is reversing a contract the architecture explicitly documents. The breaking-change flag is good; the architectural inconsistency it leaves behind needs to be reflected in 180.

### [CONCERN] D5 instrument-existence lookup in the route violates the thin-wrapper design principle

The arch's design principle is: "The API does not contain business logic. … If a new query capability is needed, it gets added to the data access layer first (where it's testable without HTTP), then exposed via an endpoint." (180-arch.data-serving.md#Design Principle: Thin Wrapper). D5 introduces a new query capability (instrument-existence lookup with primary-key seek in `instruments`) inside the route handler, used to decide 404 vs 200. By the arch's own rule, this belongs in the data access layer first (e.g. a `symbol_exists()` or `get_symbol_data_status()` method on the DB classes), with the route doing only the status-code mapping. As written, the route is reaching past its prescribed boundary and embedding a new query capability directly. The slice notes the pattern follows 185 D8a's connection scoping — but the scoping concern is separate from the layering concern.

### [CONCERN] New DB I/O path introduced by D5 has no enumerated failure modes or handling strategy

D5 adds a new DB I/O path: a primary-key seek against `instruments` whenever `get_bars` produces an empty frame. The slice does not enumerate failure modes for this path (e.g. lookup query exceeds the new 20 s `statement_timeout`; lookup exception distinct from the bars query exception; pool exhaustion under the empty-window case; peer disconnect mid-lookup) and does not state the handling strategy for any of them. The slice has good failure-mode treatment for the bigger changes (D4's admission check is described as "one comparison," D1's timeout is bounded), so the omission here is conspicuous — and on the error-deciding path, where a swallowed exception can change 404 into 200 or vice versa. At minimum: what HTTP status is returned when the lookup query itself fails or times out? "Fallback to 404" and "fallback to 200" have very different client-visible meanings and one of them needs to be stated.

### [CONCERN] D1 session-settings plumbing touches a layer boundary the arch declares out of scope

The architecture says: "The API wraps class methods that already exist on `TimescaleMinuteDataDB` and `TimescaleDailyDataDB`. There is minimal new logic." (180-arch.data-serving.md#Overview) and the example shows `TimescaleMinuteDataDB(db).get_minute_data(...)` — i.e. a single shared pool passed in. D1 instead identifies that the API process opens three independent pools (app.state.db_pool + two class-owned pools) and proposes widening the DB class API with an optional session-settings parameter so the API can give them API-sized settings. This is a defensive correction of a real-world architecture/implementation mismatch (the arch doc's "single pool" assumption is wrong as built), and the slice's choice — defaults that preserve CLI/daemon behavior — is the right shape. But the architecture's stated model of how the API connects to the DB is materially inaccurate, and the slice is fixing the gap by widening a class seam rather than by raising the mismatch with 180. Either the architecture's pooling model needs updating, or the data-access layer should be restructured to actually expose a single shared pool the API can configure once — the current arrangement is what makes D1's plumbing necessary.
