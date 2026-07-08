---
docType: review
layer: project
reviewType: arch
slice: data-serving
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/architecture/180-arch.data-serving.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260512
dateUpdated: 20260512
findings:
  - id: F001
    severity: concern
    category: consistency
    summary: "Code sample uses different function names than prose describes"
    location: 180-arch.data-serving.md#Code Location
  - id: F002
    severity: concern
    category: feasibility
    summary: "Async/sync boundary is acknowledged but not resolved"
    location: 180-arch.data-serving.md#Code Location
  - id: F003
    severity: concern
    category: technology
    summary: "msgpack serialization library not specified"
    location: 180-arch.data-serving.md#Technical Stack
  - id: F004
    severity: concern
    category: completeness
    summary: "Granularity enum location and values not defined in this document"
    location: 180-arch.data-serving.md#Technical Stack
  - id: F005
    severity: concern
    category: consistency
    summary: "Date format inconsistency between endpoint docs and response examples"
    location: 180-arch.data-serving.md#Endpoints
  - id: F006
    severity: note
    category: completeness
    summary: "Error handling strategy is implied but not specified"
    location: 180-arch.data-serving.md#What This Does NOT Include
  - id: F007
    severity: note
    category: technology
    summary: "Project's actual FastAPI/orjson/msgpack dependencies not verified against this document"
    location: 180-arch.data-serving.md#Technical Stack
  - id: F008
    severity: concern
    category: completeness
    summary: "`available` ranges data source is underspecified"
    location: 180-arch.data-serving.md#Symbol Detail
  - id: F009
    severity: note
    category: completeness
    summary: "No pagination mentioned for potentially large responses"
    location: 180-arch.data-serving.md#Endpoints
  - id: F010
    severity: concern
    category: consistency
    summary: "Missing cross-reference to confirm database function signatures"
    location: 180-arch.data-serving.md#dependencies
---

# Review: arch — slice 180

**Verdict:** CONCERNS
**Model:** minimax/minimax-m2.7

## Findings

### [CONCERN] Code sample uses different function names than prose describes

The **Overview** states the API "wraps functions that already exist (`get_minute_data`, `get_daily_data`, `get_universe`, etc.)" but the **Code Location** example shows:

```python
bars = TimescaleMinuteDataDB.get_minute_data(...)
bars = TimescaleDailyDataDB.get_daily_data(...)
```

This is a class-method call pattern, not the bare function names stated in the prose. If the actual API in `100-arch_data-storage.md` exposes these as bare module-level functions or as a different class hierarchy (e.g., `DataDB.get_minute_data`), the code sample will be misleading during implementation. The document should use consistent naming that reflects the actual data access layer structure.

---

### [CONCERN] Async/sync boundary is acknowledged but not resolved

The document states:

> "Actual implementation will need to handle the async/sync boundary (existing DB functions are synchronous; FastAPI is async). Options: `run_in_executor` for now, async psycopg3 (`AsyncConnection`) later if it matters. At single-user scale on a local network, `run_in_executor` is fine."

This defers a real architectural decision. `run_in_executor` in a thread pool has measurable overhead under concurrent load and complicates connection pool management (FastAPI's async lifespan vs. sync psycopg3 connections). The "later if it matters" threshold is never defined — how many concurrent users or requests/sec before async psycopg3 becomes necessary? Without a clear trigger, the "later" migration may never happen and the technical debt accumulates.

---

### [CONCERN] msgpack serialization library not specified

The document states msgpack is "optional for large bar responses" and mentions "orjson for fast JSON serialization as a quick win before msgpack" — but never names a msgpack library. Python's standard library has no msgpack support; this requires a dependency (e.g., `msgpack`, `msgspec`, or `pymsgpack`). The tech stack section should name the chosen library, especially since it affects both the server implementation and the TypeScript client's decoder choice.

---

### [CONCERN] Granularity enum location and values not defined in this document

The endpoint signature uses `granularity: Granularity` (a `StrEnum`), but:

- The document never defines where `Granularity` lives (is it in `data-serving`, in `data-storage`, or shared?)
- The supported values are scattered across prose ("minute granularities (1m, 5m, 15m, 1h, 4h)", "daily+ (1d, 1w, 1mo, 1q)") and never consolidated into a single list
- The `MINUTE_GRANULARITIES` constant used in the code sample is never explained or defined

Without a canonical definition, implementers will need to cross-reference `100-arch_data-storage.md` to reconstruct the enum, violating the single-source-of-truth principle.

---

### [CONCERN] Date format inconsistency between endpoint docs and response examples

The endpoint docs show query parameters `start=2024-06-01` and `end=2024-06-15` (date only, no time), but the response example uses:

```json
"timestamp": "2024-06-10T09:30:00Z"
```

This is internally consistent, but the parameter parsing should explicitly document whether time components are accepted or silently discarded. If a caller passes `start=2024-06-01T09:35:00Z`, does the API truncate to the date or reject with a 422? The Pydantic model types (`date` in the code sample) imply truncation behavior, but this should be stated explicitly.

---

### [NOTE] Error handling strategy is implied but not specified

The document correctly omits authentication and rate limiting, but error handling is conspicuously absent from "What This Does NOT Include." The code sample shows no try/except, and the API makes no mention of:

- HTTP status code conventions (404 for missing symbol, 422 for invalid params, 500 for DB errors)
- Whether SQL errors surface to clients or are sanitized
- Whether the API returns error bodies in a consistent shape

Given that this is a data-serving layer hitting a database directly, error handling is non-trivial and should be addressed — at minimum in the "Slice 4: Polish" phase, but ideally earlier to prevent ad-hoc decisions during implementation.

---

### [NOTE] Project's actual FastAPI/orjson/msgpack dependencies not verified against this document

The document recommends `orjson` as a "quick win" and assumes msgpack library selection is a local decision. However, the project's existing dependencies in `pyproject.toml` or equivalent are not cited. If the project already has `msgspec` for other reasons, that would change the msgpack recommendation. If `orjson` is already a dependency, it should be mentioned as "we'll configure it here" rather than a new addition. The document should reference actual project dependencies rather than treating library choices as open questions.

---

### [CONCERN] `available` ranges data source is underspecified

The document states:

> "The `available` ranges come from the data — MIN/MAX timestamps per symbol per source table/aggregate. This may be a view or a cached query; it doesn't need to be realtime-accurate (stale by hours is fine)."

"May be a view or a cached query" is not a design decision — it's a deferral. The implementation will need to pick one. If it's a view, what is the DDL? If it's a cached query, what is the cache invalidation strategy? Stating that staleness is acceptable is fine, but the mechanism for achieving it must be defined somewhere, or this endpoint will be implemented inconsistently across slices.

---

### [NOTE] No pagination mentioned for potentially large responses

The `/bars` endpoint can return "thousands of bars" for minute granularities over weeks. The document acknowledges msgpack for large payloads but does not address pagination. If a client requests a full year of 1-minute data for a liquid symbol, the response could be hundreds of thousands of bars. Without pagination or a configurable `limit`, the API may return payloads that stress client memory or cause timeouts on slow connections. The document should either specify a pagination strategy or explicitly state that clients should request bounded ranges.

---

### [CONCERN] Missing cross-reference to confirm database function signatures

The document lists dependencies on `100-arch_data-storage.md` and `140-arch_data-quality-operations.md` but does not verify that the function signatures (`get_minute_data`, `get_daily_data`, `get_universe`) and their parameter types match what the API code sample expects. Specifically:

- Does `get_minute_data` accept `granularity` as a parameter, or is granularity baked into the function name?
- Do the date parameters accept Python `date` objects, `datetime` objects, or ISO strings?
- Does `adjusted` exist as a parameter on both functions?

If these functions were designed without an API consumer in mind, the API may need to adapt parameters, introducing friction that the "thin wrapper" principle implies shouldn't exist.
