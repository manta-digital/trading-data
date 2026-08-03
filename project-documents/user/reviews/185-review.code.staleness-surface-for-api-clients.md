---
docType: review
layer: project
reviewType: code
slice: staleness-surface-for-api-clients
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/slices/185-slice.staleness-surface-for-api-clients.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260803
dateUpdated: 20260803
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Clean staleness surface with proper semantic preservation"
    location: src/manta_trading/api_server/models/responses.py:140-200
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Concise route handler with appropriate async boundaries"
    location: src/manta_trading/api_server/routes/status.py:1-124
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Thorough test coverage with appropriate mocking boundaries"
    location: test/unit/api_server/test_status.py:1-294
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Staleness probing only when needed"
    location: src/manta_trading/api_server/routes/bars.py:74-87
  - id: F005
    severity: note
    category: uncategorized
    summary: "Health endpoint coverage probe could add latency in degraded conditions"
    location: src/manta_trading/api_server/routes/health.py:36-46
  - id: F006
    severity: pass
    category: uncategorized
    summary: "Type safety and modern Python patterns"
    location: src/manta_trading/api_server/models/responses.py:1-10
  - id: F007
    severity: pass
    category: uncategorized
    summary: "Clear separation between health and bars staleness signals"
    location: src/manta_trading/api_server/routes/health.py:26-46
---

# Review: code — slice 185

**Verdict:** PASS
**Model:** minimax/minimax-m3

## Findings

### [PASS] Clean staleness surface with proper semantic preservation

The model projections correctly preserve the semantic distinction between "could not measure" (`None`) and "no lag" (`0.0`), which matches the project's "Never use silent fallback values" principle. The `from_verdict` and `from_freshness` classmethods are pure projections with no recomputation, and the docstrings clearly document the design rationale. The `exclude_none=True` serialization in the health endpoint preserves backward compatibility for existing clients.

### [PASS] Concise route handler with appropriate async boundaries

The route handler correctly uses `run_in_executor` for blocking DB calls, runs the two queries sequentially (with clear justification about shared connection serialization and verdict cache amortization), and lets DB failures propagate to the global 500 handler as designed (D9). The health filter resolution is well-documented and derives defaults from `HealthStatus` rather than restating them, satisfying the DRY principle.

### [PASS] Thorough test coverage with appropriate mocking boundaries

Tests cover: default and explicit parameters, invalid inputs (422 responses), empty result sets, stale coverage reporting (D9/167 D3a), route registration, and verify the auto-extend side effect doesn't leak into the API (D4). The `test_route_does_not_auto_extend` test is particularly valuable as a guardrail. Mocking boundaries match the CLI staleness tests for consistency.

### [PASS] Staleness probing only when needed

The `CAGG_BASE_GRANULARITY[granularity] != granularity` check correctly skips the probe for raw hypertables (M1/D1) while probing all cagg-served granularities. The concurrent execution via `asyncio.gather` uses different connections (db vs executor pool), so true parallelism is achieved. The comment correctly notes the "report, don't refuse" behavior (stale cagg still returns 200 with bars).

### [NOTE] Health endpoint coverage probe could add latency in degraded conditions

The sync handler runs the coverage freshness probe in the worker thread, which is appropriate. However, if a coverage probe hangs (unlikely given statement_timeout guards elsewhere, but worth noting), it could delay the liveness response. The test `test_health_coverage_probed_exactly_once` guards against accidental double-probing. This is a minor operational consideration, not a defect.

### [PASS] Type safety and modern Python patterns

The code uses `from __future__ import annotations`, modern union syntax (`str | None`), `TYPE_CHECKING` guards for forward references, and `Literal` types for constrained string values. The `BarsResponse.from_dataframe` factory uses keyword-only `is_stale` parameter with no default, enforcing that staleness must be explicitly established (matching the project principle of avoiding silent fallbacks).

### [PASS] Clear separation between health and bars staleness signals

The health endpoint's `coverage` field is correctly scoped to the two coverage caggs behind `data_status` (D6), not all seven bar-serving caggs. The docstring explicitly states that per-granularity truth belongs to the bars endpoint's `is_stale` field. This avoids redundant probing and maintains clear semantic boundaries.

---

**Overall Assessment:** The slice successfully surfaces staleness information to API clients with appropriate semantic precision. The implementation follows the project's core principles (no silent fallbacks, clear separation of concerns, DRY), includes comprehensive test coverage, and maintains backward compatibility. The two model `from_*` projection methods, the careful `None` vs `0.0` distinction, and the documented design decisions around when to probe vs not probe demonstrate thoughtful engineering. The only observation is operational (potential latency under degraded conditions), not a defect requiring change.
