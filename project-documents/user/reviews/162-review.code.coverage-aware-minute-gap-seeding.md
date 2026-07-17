---
docType: review
layer: project
reviewType: code
slice: coverage-aware-minute-gap-seeding
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/162-slice.coverage-aware-minute-gap-seeding.md
aiModel: moonshotai/kimi-k2.7-code
status: complete
dateCreated: 20260717
dateUpdated: 20260717
findings:
  - id: F001
    severity: concern
    category: api-design
    summary: "`update_data_gaps` may insert precomputed minute ranges when no fetch status is intended"
    location: src/manta_trading/data/gaps/update_data_gaps.py:138-143
  - id: F002
    severity: concern
    category: style
    summary: "Long lines in new test code may exceed project 88-character limit"
    location: test/unit/data/acquisition/daemon/test_minute.py
  - id: F003
    severity: pass
    category: correctness
    summary: "Coverage-aware minute seeding design is fail-safe and well-tested"
    location: src/manta_trading/data/gaps/minute_coverage.py
  - id: F004
    severity: pass
    category: maintainability
    summary: "Constants and progress logging are cleanly factored"
    location: src/manta_trading/constants.py
---

# Review: code — slice 162

**Verdict:** CONCERNS
**Model:** moonshotai/kimi-k2.7-code

## Findings

### [CONCERN] `update_data_gaps` may insert precomputed minute ranges when no fetch status is intended

The minute branch now gives `precomputed_ranges` precedence over the legacy single-span behavior, but it does not guard against `fetch_status_for_unfilled=None`. In the original minute path, `fetch_status_for_unfilled=None` meant no gap rows were inserted; with the new branch, passing `precomputed_ranges=[...]` and `fetch_status_for_unfilled=None` would still insert those ranges (with a null/unspecified status). Because `update_data_gaps` is a public function, either document that `precomputed_ranges` requires a non-null `fetch_status_for_unfilled` or raise `ValueError` when the two are inconsistent.

### [CONCERN] Long lines in new test code may exceed project 88-character limit

Several new `patch(...)` calls in the regression test and the helper stack — for example around `patch("manta_trading.data.acquisition.daemon.minute.update_data_gaps", mock_update_gaps)` and the `patch(`manta_trading.data.gaps.minute_coverage.fetch_sessions...` block — visually exceed 88 characters. If `ruff format` has not already been run on these files, format them now to avoid CI style failures.

### [PASS] Coverage-aware minute seeding design is fail-safe and well-tested

`build_minute_coverage_index` returns `None` on operational failure without crashing the daemon cycle, `compute_missing_minute_sessions` seeds only genuinely missing sessions, and the `_do_minute_symbol` wiring falls back to legacy single-span behavior when no coverage index is available. Regression and unit tests cover the diff logic, the day-granularity datetime/date normalization, and the new return-value plumbing.

### [PASS] Constants and progress logging are cleanly factored

`MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT` and `MINUTE_SEED_PROGRESS_LOG_INTERVAL` centralize tuning values with descriptive docstrings. The progress log in `run_minute_cycle` emits bounded, informative messages without spamming, and the completion log reports the accumulated totals.
