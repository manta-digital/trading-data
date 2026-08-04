---
docType: review
layer: project
reviewType: code
slice: symbols-ranges-via-coverage-caggs-api-load-test-tier
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260804
dateUpdated: 20260804
findings:
  - id: F001
    severity: concern
    category: typing
    summary: "Inconsistent Connection generic in new query functions"
    location: src/manta_trading/api_server/queries.py:96-235
  - id: F002
    severity: concern
    category: best-practices
    summary: "UniverseEdgeCache.get uses a default-argument lambda"
    location: src/manta_trading/api_server/queries.py:172
  - id: F003
    severity: concern
    category: error-handling
    summary: "Dead code: _apply_content_edge_check logs at ERROR then returns None"
    location: src/manta_trading/data/maintenance/status_coverage.py:124-138
  - id: F004
    severity: concern
    category: testing
    summary: "test_app's MagicMock for db_pool no longer matches the real type"
    location: test/unit/api_server/test_symbols.py:78-84
  - id: F005
    severity: concern
    category: best-practices
    summary: "_BucketingCursor._wide is a private attribute access"
    location: test/unit/market/test_cagg_freshness.py:838-869
  - id: F006
    severity: concern
    category: testing
    summary: "Hard-coded database name \"ephemeral\" in seam URL"
    location: test/unit/api_server/test_app.py:225
  - id: F007
    severity: concern
    category: testing
    summary: "_CountingLoop.__getattr__ proxy may swallow AttributeError"
    location: test/unit/api_server/test_symbols.py:286-301
  - id: F008
    severity: concern
    category: design
    summary: "_apply_content_edge_check uses 'detail' field to convey bucket_width context"
    location: src/manta_trading/data/maintenance/status_coverage.py:160-168
  - id: F009
    severity: note
    category: correctness
    summary: "Thread-safety in UniverseEdgeCache is correctly noted and tested"
    location: src/manta_trading/api_server/queries.py:145-186
  - id: F010
    severity: note
    category: testing
    summary: "Test coverage of detect_floor in test_cagg_freshness is thorough"
    location: test/unit/market/test_cagg_freshness.py:786-902
  - id: F011
    severity: note
    category: correctness
    summary: "All three range statements correctly carry time predicates"
    location: src/manta_trading/api_server/queries.py:96-140
  - id: F012
    severity: note
    category: documentation
    summary: "COVERAGE_CONTENT_STALENESS derivation is documented"
    location: src/manta_trading/constants.py:379-414
  - id: F013
    severity: note
    category: design
    summary: "The create_app(db_url) seam is well-tested"
    location: src/manta_trading/api_server/app.py:131-145
  - id: F014
    severity: note
    category: testing
    summary: "Comprehensive integration tests with real database"
    location: test/integration/test_symbol_ranges_sql.py, test/integration/test_coverage_content_edge.py
  - id: F015
    severity: note
    category: testing
    summary: "Load test gating and URL discipline"
    location: test/load/test_187_api_nfr.py:1-80
---

# Review: code — slice 187

**Verdict:** CONCERNS
**Model:** minimax/minimax-m3

## Findings

### [CONCERN] Inconsistent Connection generic in new query functions

The new functions (`fetch_universe_edges`, `fetch_symbol_coverage`, `fetch_symbol_head`, `_fetch_head_partial`) use `psycopg.Connection[Any]` while the existing `symbol_exists` and the integration tests use `psycopg.Connection[object]`. The mixing of `[Any]` and `[object]` is inconsistent. Per the project's pyright strict mode, this could cause issues with `type[psycopg.Connection[Any]]` vs `type[psycopg.Connection[object]]` compatibility in test code that uses `MagicMock(spec=psycopg.Connection)`. The test file `test_queries.py` does use `MagicMock(spec=psycopg.Connection)`, which works for both, but other code (and the integration test that casts `conn` to `psycopg.Connection[object]`) suggests the project convention is `[object]`. Recommend standardizing on `psycopg.Connection[object]` for consistency with the existing `symbol_exists` and the integration tests' `_merged`/`_lazy` helpers.

### [CONCERN] UniverseEdgeCache.get uses a default-argument lambda

```python
def get(
    self,
    conn: psycopg.Connection[Any],
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[CycleGranularity, date | None]:
```

Default-argument lambdas are flagged by ruff's `B006` rule (mutable-argument-default for lambdas) and the project rules state "small, single-purpose functions" with explicit arguments. The tests in `test_queries.py` already pass a clock explicitly via `now=self._clock(_T0)`, so the default is not relied upon in tests. More importantly, the lambda captures `datetime` and `UTC` by reference and creates a fresh closure on every call. A module-level helper or just `datetime.now` with `UTC` imported would be cleaner. Recommend: `now: Callable[[], datetime] | None = None` and `now = now or (lambda: datetime.now(UTC))` inside, or define a module-level `_utcnow` helper. The tests still work either way.

### [CONCERN] Dead code: _apply_content_edge_check logs at ERROR then returns None

The `_content_edge_lag` function catches `psycopg.Error` and logs at ERROR level (good), but then returns `None` and the `_apply_content_edge_check` caller treats `None` as "unmeasurable" rather than "indeterminate/stale". The docstring acknowledges this is deliberate ("the generic evaluation that ran first already carries PROBE_FAILED for a broken connection"), but there's a gap: if the first (generic) probe succeeded and *this* probe fails, the verdict silently retains its (possibly stale-but-not-yet-detected) generic verdict with no signal that the check was attempted and failed. The comment says "Logged because a probe that fails here and not there would otherwise be invisible" — but the log is at ERROR, and operators reading only the verdict won't see it. Consider whether this should add a signal like `CONTENT_EDGE_PROBE_FAILED` to make the failure visible in the verdict, or downgrade the log level to WARNING since the verdict is intentionally not affected. The current behavior is documented but may surprise operators.

### [CONCERN] test_app's MagicMock for db_pool no longer matches the real type

```python
app.state.db_pool = MagicMock(name="sentinel_pool")
```

The route uses `get_db` which expects a `psycopg.Connection`, not a pool. The mock was likely needed for the old `get_db` signature that took a pool. With the current code, this mock is not actually accessed by the route (which calls `get_db` for a connection, not the pool). It's harmless dead code, but if the intent was to keep a pool around for `get_db`'s pool-to-connection checkout, the mock should match `ConnectionPool` spec. The `app.state.universe_edges` assignment is correct and load-bearing. The `db_pool` mock can be removed.

### [CONCERN] _BucketingCursor._wide is a private attribute access

The `_BucketingCursor` accesses `self._wide._raw_edge` and `self._wide._cagg_edge`, crossing class boundaries with private attributes. This is test code so it's acceptable, but the `_wide` attribute is only needed by the cursor and could be passed as a constructor argument directly, avoiding the `_wide` indirection. Minor.

### [CONCERN] Hard-coded database name "ephemeral" in seam URL

```python
_SEAM_URL = "postgresql://seam:pass@localhost:5432/ephemeral"
```

This is a hard-coded test URL. It's not a real secret and not a connection that will be opened (the test mocks `ConnectionPool`), so it's not a security issue. But the `seam:pass` credentials violate the project rule "Never include credentials, API keys, or secrets in source code or comments" — even for test fixtures. Use clearly-fake credentials like `user:pass@localhost:5432/test_db` or `seam_test:seam_test@...` with a comment noting these are intentionally fake. The existing `_DB_URL` constant in the same file likely has the same issue; check it for consistency.

### [CONCERN] _CountingLoop.__getattr__ proxy may swallow AttributeError

```python
def __getattr__(self, name: str) -> Any:
    return getattr(self._loop, name)
```

If `_loop` itself raises `AttributeError` (e.g., for a truly missing attribute), this proxy will re-raise it correctly, but if `getattr` raises something else (unlikely but possible with a custom loop), behavior is opaque. More practically: the proxy is only needed for `run_in_executor`; everything else could be `self._loop.<name>` accessed via `__getattr__`. The current implementation is fine, but the `_CountingLoop` class deserves a comment explaining why a proxy is used rather than a full loop substitute. The docstring is good; the *why proxy not substitute* point is the part that could be clearer.

### [CONCERN] _apply_content_edge_check uses 'detail' field to convey bucket_width context

The `detail` string includes `bucket_width={verdict.bucket_width}` which is already a separate field on the verdict. Including it in a formatted string is fine, but the string is `verdict.detail` which may be reformatted by callers (e.g., truncated for display). The fact that `bucket_width` is now a first-class field on `FreshnessVerdict` (good — exposed in the dataclass) means callers can format it themselves. The duplication is minor; consider whether the `detail` field's bucket_width mention is redundant or whether the field is the right place for it. The code is correct; the question is whether `detail` should reference `verdict.bucket_width` via f-string interpolation of a field that is also separately serialized.

### [NOTE] Thread-safety in UniverseEdgeCache is correctly noted and tested

The lock is held across the fetch, the cache test verifies this with 8 threads (concurrency assertion is sound), and the docstring explains the thundering-herd concern. Good. However: the lock is a `threading.Lock`, not a `RLock`, and the test doesn't exercise a thread that calls `clear()` while another is in `get()`. `clear()` and `get()` are not reentrant on each other, so the current `threading.Lock` is correct — just noting that if a future change calls `get()` from within `clear()` (or vice versa) it will deadlock. The docstring could mention the non-reentrancy.

### [NOTE] Test coverage of detect_floor in test_cagg_freshness is thorough

The new `TestDetectionFloor` class correctly pins both sides of the floor: sub-bucket lag is invisible, supra-bucket lag is caught. The `_WideBucketConnection` is a well-designed fixture that responds based on the SQL it receives, so removing the bucketing step would actually change what the fixture returns — making the test load-bearing rather than vacuous. Good test design.

### [NOTE] All three range statements correctly carry time predicates

The SQL strings all carry `WHERE time > %s` or `WHERE time_bucket > %s` predicates. The test `test_every_branch_carries_a_time_predicate` asserts this for all three statements. The D1 design rationale (bounded plans over chunk-exclusion) is correctly implemented.

### [NOTE] COVERAGE_CONTENT_STALENESS derivation is documented

The constant's docstring shows the arithmetic derivation (`MAX_COVERAGE_SOURCE_STALENESS` + max `end_offset` = 1 day 4 h) and the production measurement context (jobs 1107/1108 on 2026-08-04). Excellent. The explicit statement that this is a "detection fix, not a policy tightening" and the explanation of why it's a separate constant rather than a reuse are both load-bearing. Good.

### [NOTE] The create_app(db_url) seam is well-tested

The `lifespan=partial(lifespan, db_url=db_url)` is the right way to bind a default argument to FastAPI's lifespan (which takes only `app`). The four tests in `test_app.py` cover the override path, the no-override path, the failure mode, and the load-tier's actual requirement. Good coverage.

### [NOTE] Comprehensive integration tests with real database

Both new integration test files use `ephemeral_db` fixtures and follow the slice 167/168 precedent. The fixtures are well-commented with production-shape rationale. The D2 four-case parametrization in `test_symbol_ranges_sql.py` covers spanning, before, after, and absent, each asserted against the lazy pre-187 result as oracle. Good.

### [NOTE] Load test gating and URL discipline

The load test module's docstring is thorough about gating (`MT_RUN_LOAD_TESTS=1`), the fixture-honesty caveat (no 3,371-chunk reproduction), and the "every bound was derived from a measurement" claim. The reference to `test_load_tier_never_references_prod_db_url` and the `create_app(db_url=...)` seam making that possible closes the loop on the D9 requirement.

---

**Overall assessment:** The slice is well-implemented with thorough documentation, comprehensive test coverage, and clear design rationale. The concerns are minor: typing inconsistency between `[Any]` and `[object]`, a couple of style/best-practice items (default-argument lambda, hard-coded fake credentials, test mock that's now dead code), and one design question about the `psycopg.Error` handling in `_content_edge_lag`. None are blockers; all are addressable in a follow-up.
