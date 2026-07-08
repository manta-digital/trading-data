---
docType: review
layer: project
reviewType: slice
slice: bars-endpoint
project: squadron
verdict: PASS
sourceDocument: project-documents/user/slices/182-slice.bars-endpoint.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260513
dateUpdated: 20260513
findings:
  - id: F001
    severity: note
    category: implementation-documentation-consistency
    summary: "Code path divergence from architecture example"
    location: 182-slice.bars-endpoint.md#db-instance-lifecycle
  - id: F002
    severity: note
    category: code-maintainability
    summary: "Code path constant fragility"
    location: 182-slice.bars-endpoint.md#granularity-routing
  - id: F003
    severity: pass
    category: architecture-alignment
    summary: "Thin wrapper principle — no business logic added"
    location: 182-slice.bars-endpoint.md#overview
  - id: F004
    severity: pass
    category: error-handling
    summary: "Error handling — failure modes enumerated"
    location: 182-slice.bars-endpoint.md#404-handling
  - id: F005
    severity: pass
    category: architecture-alignment
    summary: "Async/sync bridge — approach documented"
    location: 182-slice.bars-endpoint.md#async-sync-bridge
  - id: F006
    severity: pass
    category: interface-definition
    summary: "Cross-slice interfaces — clearly defined"
    location: 182-slice.bars-endpoint.md#cross-slice-interfaces
  - id: F007
    severity: pass
    category: implementation-correctness
    summary: "Serialization — msgpack and JSON correctly implemented"
    location: 182-slice.bars-endpoint.md#response-serialization
  - id: F008
    severity: pass
    category: testing-completeness
    summary: "Unit tests — comprehensive coverage"
    location: 182-slice.bars-endpoint.md#unit-tests
  - id: F009
    severity: pass
    category: architecture-alignment
    summary: "Response model structure matches architecture"
    location: 182-slice.bars-endpoint.md#response-models
---

# Review: slice — slice 182

**Verdict:** PASS
**Model:** minimax/minimax-m2.7

## Findings

### [NOTE] Code path divergence from architecture example

The architecture document's example code shows `TimescaleMinuteDataDB(db)` instantiated **per-request** inside the route handler:

```python
bars_df = await loop.run_in_executor(
    None, lambda: TimescaleMinuteDataDB(db).get_minute_data(...)
)
```

The slice instead instantiates both DB objects **once during the lifespan** and stores them in `app.state`, then provides them via dependency injection. This is architecturally sound for the stated reason: each DB object creates its own `ConnectionPool` internally, so per-request instantiation would create a new pool on every call. The slice's approach avoids this while still achieving the same functional result.

This is **not** a violation — the architecture's example is illustrative, not prescriptive, and the slice documents a valid optimization. However, it creates a minor inconsistency between documentation and implementation that implementors should be aware of.



---

### [NOTE] Code path constant fragility

The slice declares `_MINUTE_GRAINS` as a module-level constant in `routes/bars.py` that mirrors the private set in `timescale_minute_db.py`:

```python
_MINUTE_GRAINS = frozenset({
    Granularity.M1, Granularity.M5, Granularity.M15,
    Granularity.H1, Granularity.H4,
})
```

The slice acknowledges this is fragile because it imports a private symbol. The architecture says the API "will replicate this as a module-level constant" — this is expected but carries maintenance risk if the underlying sets change. This is a known tradeoff, not a defect.



---

### [PASS] Thin wrapper principle — no business logic added

The slice correctly implements the architecture's core principle: the route "wraps `TimescaleMinuteDataDB.get_minute_data` and `TimescaleDailyDataDB.get_daily_data` without adding business logic." Parsing, DB calls, serialization, and error responses only.



---

### [PASS] Error handling — failure modes enumerated

The slice explicitly handles the key failure modes with stated strategies:
- **Empty DataFrame** → `HTTPException(404)` with `{"error": "..."}` shape (not `{"detail": "..."}`)
- **Invalid granularity** → FastAPI auto-returns `422` with `{"detail": [...]}` (correct; 422 is a Pydantic validation error)
- **Non-404 HTTP exceptions** → delegated to FastAPI's default handler
- **`ValueError` from daily DB if minute grain passed** → uncaught; falls through to global 500 handler (slice 184)

This is explicit handling, not "TBD" or implicit.



---

### [PASS] Async/sync bridge — approach documented

The slice uses `asyncio.get_running_loop().run_in_executor(None, ...)` to call synchronous psycopg3 methods from async handlers. This matches the architecture's prescribed approach. The document explains that the executor call is the only blocking operation and all other work is fast CPU work on the calling coroutine.



---

### [PASS] Cross-slice interfaces — clearly defined

The slice clearly enumerates:
- **Consumed from slice 181**: `app.state.db_pool`, `create_app()`, `models/responses.py` stubs, `deps.py` extension
- **Consumed from existing codebase**: `TimescaleMinuteDataDB`, `TimescaleDailyDataDB`, `Granularity`, `Settings`
- **Provided to slice 183**: `models/responses.py` extensions

Integration points are explicit and consistent with parent architecture.



---

### [PASS] Serialization — msgpack and JSON correctly implemented

The slice correctly implements:
- **JSON**: `orjson.dumps` → `Response(media_type="application/json")` (or `ORJSONResponse`)
- **msgpack**: `msgpack.packb(response.model_dump(), default=str)` → `Response(media_type="application/x-msgpack")`

The `default=str` fallback handles datetime types that msgpack cannot natively encode. This is documented and explicit.



---

### [PASS] Unit tests — comprehensive coverage

Six test cases covering:
1. Daily JSON response shape
2. Minute routing + date→datetime conversion
3. msgpack serialization + Content-Type
4. 404 error shape (`{"error": "..."}`)
5. 422 for invalid granularity
6. `adjusted` parameter passthrough

All use mocked DB instances via `app.dependency_overrides`; no live DB required.



---

### [PASS] Response model structure matches architecture

`BarsResponse` and `BarRecord` match the architecture's specified JSON shape:
- `symbol: str`, `granularity: str`, `adjusted: bool`, `count: int`, `bars: list[BarRecord]`
- `BarRecord`: `timestamp`, `open`, `high`, `low`, `close`, `volume`

`granularity` is serialized as `str` (not `Granularity` enum) so orjson produces `"1d"` directly without custom encoders.
