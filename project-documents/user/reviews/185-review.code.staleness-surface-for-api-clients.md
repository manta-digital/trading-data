---
docType: review
layer: project
reviewType: code
slice: staleness-surface-for-api-clients
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/185-slice.staleness-surface-for-api-clients.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260803
dateUpdated: 20260803
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "Empty `health=` query value silently filters to zero rows"
    location: src/manta_trading/api_server/routes/status.py:42-70
  - id: F002
    severity: note
    category: api-consistency
    summary: "422 error-body shape diverges from the \"shared contract\" the docstring claims"
    location: src/manta_trading/api_server/routes/status.py:48-52
  - id: F003
    severity: pass
    category: correctness
    summary: "Async/executor discipline, connection-pool scoping, and staleness plumbing"
    location: src/manta_trading/api_server/routes/bars.py:84-109
---

# Review: code — slice 185

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] Empty `health=` query value silently filters to zero rows

`_resolve_health_filter` splits `health` on `,` and drops blank tokens (`if token.strip()`), so `GET /api/v1/status?health=` (health present but empty) produces `tokens = []`. Since `invalid = []` too, no 422 is raised — the function returns `[]`, which becomes `health = ANY(%s)` with an empty array, silently matching **no rows** for any symbol. This is a distinct, more surprising outcome than both neighboring cases: omitting `health` falls back to the documented CLI default, and `all=true` clears the filter explicitly. An empty-but-present `health` (an easy accident from client-side URL templating, e.g. `?health={filter}` when `filter` is unset) instead returns an empty result set with a 200 and no diagnostic, which looks identical to "your filter matched nothing" rather than "you sent a malformed query." Given the project's "never use silent fallback values; fail explicitly" and "prefer lenient parsing" conventions, this case should either be treated the same as omitted (fall back to the CLI default) or explicitly rejected with the same 422 the invalid-token path already returns. It is also untested — `test_status.py` covers omitted, `all=true`, valid CSV, and invalid tokens, but not an empty string.

### [NOTE] 422 error-body shape diverges from the "shared contract" the docstring claims

The docstring for `_resolve_health_filter` states its 422 "is the same status FastAPI already returns for an invalid `granularity` on the bars route, so one malformed-query contract covers both." Verified by running both: an invalid `granularity` on `/api/v1/bars/{symbol}` returns FastAPI's native validation body, `{"detail": [{"type": "enum", "loc": [...], "msg": ..., "input": ..., "ctx": ...}]}`, while an invalid `health` token here returns `{"detail": "Invalid health values: BOGUS. Valid: ..."}` — a plain string, not a list of structured errors. Status code parity holds, but the response *shape* does not, so a client that generically parses `detail` as a list of `{loc, msg}` objects (the FastAPI-idiomatic pattern) will break on this endpoint's 422. Not a functional bug, but the docstring overstates the guarantee — worth either loosening the wording or aligning the body shape (e.g. raising via a `RequestValidationError`-compatible path) if a uniform error contract across endpoints is actually intended.

### [PASS] Async/executor discipline, connection-pool scoping, and staleness plumbing

All blocking DB/pandas work in the new and touched async routes (`get_bars`, `get_status`) is offloaded via `loop.run_in_executor`, matching the project's async-correctness rule. The bars route's pool-checkout change (probe-only, not full-request) is well-motivated by a documented regression (0.010s → 4.03s stall) and is covered by explicit regression tests (`test_raw_granularity_checks_out_no_connection`, `test_cagg_granularity_checks_out_exactly_one_connection`). `is_stale`/`coverage` are propagated as required, non-defaulted fields end-to-end (`BarsResponse.from_dataframe(..., is_stale=...)`, `StatusResponse.coverage`), consistent with "never use silent fallback values." Ruff (`select = E,F,W,I,UP,BLE,ASYNC,B`) and the full `test/unit/api_server/` suite (57 tests) pass clean on the changed files.
