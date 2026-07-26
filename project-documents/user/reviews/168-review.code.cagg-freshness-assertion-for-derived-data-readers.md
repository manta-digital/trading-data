---
docType: review
layer: project
reviewType: code
slice: cagg-freshness-assertion-for-derived-data-readers
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/168-slice.cagg-freshness-assertion-for-derived-data-readers.md
aiModel: z-ai/glm-5.2
status: complete
dateCreated: 20260726
dateUpdated: 20260726
findings:
  - id: F001
    severity: concern
    category: uncategorized
    summary: "`now` clock seam does not flow through to `_evaluate`"
    location: src/manta_trading/market/maintenance/cagg_freshness.py#assert_cagg_fresh
  - id: F002
    severity: concern
    category: uncategorized
    summary: "Production module exceeds 300-line guideline"
    location: src/manta_trading/market/maintenance/cagg_freshness.py
  - id: F003
    severity: note
    category: uncategorized
    summary: "`_EvalConnection` fixture mishandles explicit `last_successful_finish=None`"
    location: test/unit/market/test_cagg_freshness.py#_EvalConnection
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Error handling follows project conventions"
    location: src/manta_trading/market/maintenance/cagg_freshness.py:210
  - id: F005
    severity: pass
    category: uncategorized
    summary: "SQL injection surface is correctly bounded"
    location: src/manta_trading/market/maintenance/cagg_freshness.py#_max_probe
  - id: F006
    severity: pass
    category: uncategorized
    summary: "Timeout discipline is thorough and well-tested"
    location: src/manta_trading/market/maintenance/cagg_freshness.py#_set_probe_timeout
  - id: F007
    severity: pass
    category: uncategorized
    summary: "Threshold ceiling prevents the 270-day regression"
    location: src/manta_trading/market/maintenance/cagg_freshness.py#_resolve_threshold
---

# Review: code — slice 168

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.2

## Disposition (20260726)

- **F001 — fixed.** `now` is threaded into `_evaluate`; pinned by a test that
  fails when the pass-through is reverted.
- **F003 — fixed.** `_EvalConnection` uses an explicit `_UNSET` sentinel; two
  new tests exercise the cold-start shape it previously could not produce.
- **F002 — declined (PM decision).** 569 lines, ~250 executable; the balance is
  docstrings and four incident write-ups. The proposed extraction leaves the
  remaining module at ~320 lines — still over guideline — for import churn
  across 49 tests and zero behavior change. File length over guideline is
  acceptable when the excess is not code complexity. Rationale recorded in the
  slice design under "Code review disposition".

See `slices/168-slice.cagg-freshness-assertion-for-derived-data-readers.md`.

## Findings

### [CONCERN] `now` clock seam does not flow through to `_evaluate`

`assert_cagg_fresh` accepts a `now: Callable[[], datetime]` parameter, documented as a "Clock seam; overridden in tests." However, `_evaluate` — which performs the actual freshness evaluation including the `LAST_SUCCESS_TOO_OLD` check — calls `_now()` directly rather than receiving the `now` callable. This means the `now` seam only controls cache TTL expiry, not the freshness evaluation itself. The unit tests work around this by monkeypatching `cagg_freshness._now` at the module level (the `_frozen_clock` fixture), but this is a leaky abstraction: a caller passing a custom `now` would reasonably expect it to control all time-dependent logic, not just cache expiry. Threading `now` into `_evaluate` (or at minimum documenting that `now` is cache-only) would make the seam's scope explicit and prevent future callers from assuming broader control than they have.

### [CONCERN] Production module exceeds 300-line guideline

The `cagg_freshness.py` module is 566 lines, nearly double the project convention of ~300 lines. The module bundles four distinct concerns: (1) catalog/job reads (`_read_refresh_job`, `_JobRow`), (2) edge probes (`_cagg_max`, `_raw_max`, `_max_probe`), (3) threshold/source resolution (`_resolve_source_table`, `_resolve_threshold`), and (4) evaluation + caching (`_evaluate`, `assert_cagg_fresh`, `_VERDICT_CACHE`). Extracting the probe functions and catalog read into a separate `_cagg_probes.py` (or similar) would bring both files under the guideline and improve testability by reducing the surface area each test file imports.

### [NOTE] `_EvalConnection` fixture mishandles explicit `last_successful_finish=None`

The `_EvalConnection.__init__` uses `last_successful_finish if last_successful_finish else _NOW` to build the catalog row. If a test explicitly passes `last_successful_finish=None` (to simulate the cold-start "never succeeded" case that the production code explicitly handles at the `if job.last_successful_finish is not None` guard), the falsy check substitutes `_NOW` instead, making the fixture unable to produce that scenario. No current test exercises this path, so it's latent, but switching to `last_successful_finish if last_successful_finish is not None else _NOW` (or better, allowing the `None` to flow through) would make the fixture faithfully represent the production data shape.

### [PASS] Error handling follows project conventions

The `_restore_probe_timeout` function catches `psycopg.Error` and uses `logger.exception` with an inline comment explaining why swallowing is correct ("The connection is already broken... the caller's next statement will surface it"). The `_evaluate` function catches `psycopg.Error` specifically (not bare `except`), logs at ERROR level with `logger.exception`, and returns a `PROBE_FAILED` verdict rather than propagating. Both patterns satisfy the project's exception-handling rule (option b for the restore, option a/b hybrid for the evaluate).

### [PASS] SQL injection surface is correctly bounded

All user-derived identifiers (`relation`, `column`) arrive from `GRANULARITY_SOURCE` or hardcoded literals, never from caller input. The `# noqa: S608` suppressions are annotated with comments explaining the safety rationale. Bound parameters (`%s`) are used for `view_name`, `bucket_width`, and `start_offset` everywhere they appear as values rather than identifiers.

### [PASS] Timeout discipline is thorough and well-tested

The choice of `SET` over `SET LOCAL` is documented with empirical verification (PG 17.7, 2026-07-26). Every probe path calls `_set_probe_timeout` before its query, verified by parametrized unit tests (`TestProbeTimeoutDiscipline`) that assert on statement *order*, not SQL text. The integration `TestInducedSlowness` proves the bound works against a live database with a genuinely slow query, not a mocked exception.

### [PASS] Threshold ceiling prevents the 270-day regression

The `min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)` ceiling is load-bearing and tested: `test_daily_cagg_stalled_100_days_is_stale_despite_270_day_offset` explicitly asserts that a 100-day stall exceeds the threshold even with a 270-day `start_offset`, with a comment that the test must fail if the ceiling is removed. The `end_offset` addition is also correctly tested.
