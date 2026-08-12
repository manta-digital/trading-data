---
docType: review
layer: project
reviewType: code
slice: daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease
project: trading-data
verdict: FAIL
sourceDocument: project-documents/user/slices/170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md
aiModel: moonshotai/kimi-k2.7-code
status: complete
dateCreated: 20260811
dateUpdated: 20260811
reviewedSha: bffeaf255d22b43e66f39bf7600d58ec386ae556
findings:
  - id: F001
    severity: fail
    category: error-handling
    summary: "Preflight handler crashes when hypertable is missing"
    location: src/manta_trading/market/maintenance/rechunk.py:197-217
  - id: F002
    severity: concern
    category: testing
    summary: "Integration test still reads production database URL variable"
    location: test/integration/test_rechunk_driver.py:36
  - id: F003
    severity: pass
    category: design
    summary: "Rechunk target registry centralizes table-specific values"
    location: src/manta_trading/market/maintenance/rechunk.py:50-105
  - id: F004
    severity: pass
    category: testing
    summary: "Daily rechunk tests use isolated throwaway database"
    location: test/integration/test_rechunk_driver.py:203-220
---

# Review: code — slice 170

**Verdict:** FAIL
**Model:** moonshotai/kimi-k2.7-code

## Findings

### [FAIL] Preflight handler crashes when hypertable is missing

`_assert_dimension_interval` fetches the dimension row and immediately indexes `row[0]`, but never checks whether `row` is `None`. When the target table is not a hypertable (or the dimension query returns no row), this raises `TypeError: 'NoneType' object is not subscriptable` instead of the `PreflightError` the new unit test `test_missing_hypertable_is_a_preflight_error` expects. That test at `test/unit/market/test_rechunk.py` asserts `pytest.raises(PreflightError, match="not a hypertable")`, so the implementation and the test added in this change are inconsistent. Add a guard such as:

```python
if row is None:
    raise PreflightError(f"{table} is not a hypertable")
```

before indexing `row[0]`.

### [CONCERN] Integration test still reads production database URL variable

The 166 rechunk test suite reads `MT_TIMESCALE_DB_URL` via `os.environ.get("MT_TIMESCALE_DB_URL", "")` and passes it to `psycopg.connect` in the `scratch_db` fixture (`test/integration/test_rechunk_driver.py:115`). The testing rules ("Production Database Protection") state: "Tests never read the production DB URL variable." The new daily suite correctly uses the `ephemeral_db` fixture, but the pre-existing minute suite still relies on the production URL variable. It is gated by `@requires_working_url` and skipped by default, but the code still reads the variable. Migrate the minute suite to `ephemeral_db` (or a dedicated test admin URL) so the tier never accesses production credentials.

### [PASS] Rechunk target registry centralizes table-specific values

`RechunkTargetSpec` and `RECHUNK_TARGETS` provide a single source of truth for table names, chunk intervals, dependent cagg views, and migration IDs. Dispatch is by `RechunkTarget` enum rather than string comparisons, and the CLI uses the same enum for its `--table` choices. This follows the project convention to never scatter comparison values across code.

### [PASS] Daily rechunk tests use isolated throwaway database

The new `daily_scratch_db` fixture builds the daily-shaped scratch hypertable inside `ephemeral_db` and never touches `daily_ohlcv` or `minute_ohlcv`. This matches the production-database protection rule: destructive fixtures only target databases the fixture created.
