---
docType: review
layer: project
reviewType: tasks
slice: mt-data-refetch
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/148-tasks.mt-data-refetch.md
aiModel: z-ai/glm-5.1
status: complete
dateCreated: 20260504
dateUpdated: 20260504
findings:
  - id: F001
    severity: concern
    category: completeness
    summary: "T6 omits `coalesce_data_gaps` call for minute granularity"
    location: src/manta_trading/data/acquisition/daemon/minute.py
  - id: F002
    severity: concern
    category: test-coverage
    summary: "SC8 advisory-lock concurrent access has no automated test"
    location: test/integration/test_data_refetch.py
  - id: F003
    severity: concern
    category: test-coverage
    summary: "SC12 no-terminal-gaps confirmation path has no automated test"
    location: test/integration/test_data_refetch.py
  - id: F004
    severity: note
    category: test-coverage
    summary: "SC14 calls for unit tests of CLI behavior; T12 provides integration tests instead"
    location: test/integration/test_data_refetch.py
  - id: F005
    severity: note
    category: scope
    summary: "Adding `coalesce_data_gaps` to `_do_daily_symbol` changes normal daemon cycle behavior"
    location: src/manta_trading/data/acquisition/daemon/daily.py
---

# Review: tasks — slice 148

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.1

## Findings

### [CONCERN] T6 omits `coalesce_data_gaps` call for minute granularity

T2 explicitly adds a `coalesce_data_gaps(symbol, 'daily')` call to `_do_daily_symbol`, noting "daily now also coalesces." T6 (the minute counterpart) does not mention adding `coalesce_data_gaps` to `_do_minute_symbol`. However, SC10 requires that "coalesce_data_gaps runs after all chunks are processed for both daily and minute granularities," and the slice design's `run_minute_refetch` step 5 specifies "After loop: `coalesce_data_gaps(symbol, 'minute')`." If `_do_minute_symbol` does not already call `coalesce_data_gaps` (and the fact that T2 must add it for daily suggests it may not exist for minute either), this is a missing implementation step that would cause SC10 to fail for minute granularity. T7's tests should also verify this call.

---

### [CONCERN] SC8 advisory-lock concurrent access has no automated test

SC8 requires that "the advisory lock on `(symbol, granularity)` is held during the refetch (verified by concurrent access test: daemon blocked on same symbol during refetch)." T14 Step 5 covers this as a manual walkthrough step, but T12 (integration tests) contains no automated concurrent-lock test. Since the lock is inherited from `_do_*_symbol` and not new code, this is lower risk, but SC8 explicitly calls for a verification that currently has no automated coverage and could regress silently.

---

### [CONCERN] SC12 no-terminal-gaps confirmation path has no automated test

SC12 states "a refetch of a symbol with no terminal gaps completes successfully when confirmed." T11 implements the no-terminal-gaps prompt path ("No terminal gaps in scope for {symbol}. Refetch anyway? [y/N]"), but T12's integration test list does not include a test case for this scenario. This path is distinct from the normal confirmation prompt and from `--yes`, and deserves its own test to prevent regression.

---

### [NOTE] SC14 calls for unit tests of CLI behavior; T12 provides integration tests instead

SC14 explicitly lists "unit tests cover: … `--daily`/`--minute` flag resolution, dry-run (no mutations), `--yes` skips prompt." T12 tests these behaviors as integration tests (with DB, `pytest.mark.integration`). This is a reasonable approach since CLI command logic is difficult to unit-test in isolation, but it does not strictly match the SC's "unit tests" wording. If the intent was strict unit tests (mocked DB, no I/O), an additional unit test task would be needed.

---

### [NOTE] Adding `coalesce_data_gaps` to `_do_daily_symbol` changes normal daemon cycle behavior

T2 adds `coalesce_data_gaps(symbol, 'daily')` unconditionally to `_do_daily_symbol`, meaning the normal daemon daily cycle will also coalesce — not just the refetch path. The slice design describes coalescing as a refetch step, but extending `_do_daily_symbol` makes it apply universally. This appears intentional (the parenthetical "daily now also coalesces" acknowledges it), and coalescing is idempotent, but it is a behavioral change to the existing daemon path that goes beyond just adding `force_reset_terminal`/`window`. SC15 says "normal daemon cycle behavior is unchanged" — if the normal cycle previously did not coalesce, adding coalescing is technically a change. Worth confirming this is desired.
