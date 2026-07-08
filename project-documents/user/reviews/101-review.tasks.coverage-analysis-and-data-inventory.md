---
docType: review
layer: project
reviewType: tasks
slice: coverage-analysis-and-data-inventory
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/101-tasks.coverage-analysis-and-data-inventory.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260402
dateUpdated: 20260402
---

# Review: tasks — slice 101

**Verdict:** CONCERNS
**Model:** minimax/minimax-m2.7

## Findings

### [CONCERN] Compression info from `get_coverage_analysis` not traced to CLI output

**Description:** The success criterion states: "`mt data minute coverage --symbol AAPL` displays per-symbol detail: date range, row count, missing-day gaps, per-day bar counts (for anomaly inspection), **compression info**"

Task 5 specifies `get_symbol_coverage()` calls `db.get_coverage_analysis(symbol)` but does not explicitly state the result (which contains compression info per the slice design's "Technical Decisions") is included in the returned dict. The success criteria for Task 5 only mention: "calls only public `TimescaleMinuteDataDB` methods" but omit what the method returns.

Task 10 (CLI command) success criteria mention "format as table or JSON" but do not explicitly verify compression info is included in the output.

**Risk:** The analyzer may compose the results but drop the compression field, and verification at Task 15 may not catch it because the acceptance criteria are not explicit.

**Recommendation:** Add to Task 5 success criteria: "Verify returned dict includes compression data (ratio, status) from `get_coverage_analysis()`". Add to Task 10 success criteria: "Verify compression info appears in per-symbol table output."

---

### [CONCERN] `minute_metrics` CLI output modes not explicitly tested

**Description:** Task 11 covers the `minute_metrics` command implementation but its success criteria focus on `db.get_system_metrics()` being called and `db.close()` in `try/finally`. The success criteria do not explicitly verify:
- Text table output format
- `--json` flag JSON output

Task 13 (CLI unit tests) explicitly tests `mt data minute coverage` with `--json` but only mentions `mt data minute metrics` output with mocked `get_system_metrics()`. There is no explicit test verifying the CLI correctly routes to text vs JSON output for `minute_metrics`.

**Risk:** The `--json` functionality for `minute_metrics` may not work correctly without any test catching it.

**Recommendation:** Add to Task 11 success criteria: "Verify `--json` flag routes to `print_result(..., json_mode=True)`". Add to Task 13: explicit test case for `mt data minute metrics --json` verifying valid JSON output.

---

### [PASS] All success criteria have corresponding tasks

**Description:** Cross-reference confirms every functional and technical requirement maps to at least one task:

| Success Criterion | Tasks |
|---|---|
| `mt data minute coverage` fleet summary | Tasks 5, 6, 10, 13 |
| `mt data minute coverage --symbol` detail | Tasks 5, 6, 10, 13 |
| `mt data minute metrics` | Tasks 9, 11, 13 |
| `mt data daily coverage` | Tasks 7, 8, 12, 13 |
| `--json` flag | Tasks 10, 11, 12, 13 |
| Gap detection (>3 days) | Tasks 2, 4, 5, 6 |
| Per-day bar counts | Tasks 3, 4, 5, 14 |
| DB URL error messages | Tasks 9, 13 |
| `MinuteCoverageAnalyzer` unit tests | Task 6 |
| `MarketDB.get_daily_coverage()` unit tests | Task 8 |
| New DB methods unit tests | Task 4 |
| CLI unit tests | Task 13 |
| Integration tests with skip | Task 14 |
| No async code | All tasks (implicit) |
| No changes to existing methods | All tasks (implicit) |

---

### [PASS] No scope creep detected

**Description:** All 14 tasks trace directly to slice design requirements. The out-of-scope items (gap filling, partial-day classification, IDataService protocol, TimescaleMonitor) are not present in any task.

---

### [PASS] Test placement follows test-with pattern

**Description:** Unit test tasks immediately follow their implementation tasks:
- Task 4 (unit tests) follows Task 3 (get_daily_bar_counts)
- Task 6 (analyzer tests) follows Task 5 (analyzer rewrite)
- Task 8 (MarketDB tests) follows Task 7 (get_daily_coverage)
- Task 13 (CLI tests) follows all CLI implementation (Tasks 9-12)

---

### [PASS] Commit distribution is appropriate

**Description:** Three commits are spread across the implementation:
1. DB layer methods (Tasks 1-4)
2. Analyzer layer (Tasks 5-6)
3. CLI commands (Tasks 9-13)
4. Integration tests (Task 14)
5. Finalization (Task 15)

This avoids batching all commits at the end.

---

### [PASS] Task sequencing is correct

**Description:** Dependencies are respected:
- Tasks 1-3 add DB methods before Task 5 uses them
- Task 5 (analyzer) depends on Task 1 (get_fleet_summary) — correctly ordered
- Tasks 9-12 (CLI) depend on Tasks 5-7 (analyzer + MarketDB) — correctly ordered
- Task 14 (integration) comes after all unit tests
- Task 15 (validation) is the final checkpoint

No circular dependencies exist.

---

### [PASS] Task granularity is appropriate

**Description:** Tasks are appropriately sized. The largest tasks (Task 5: analyzer rewrite, Task 10: coverage CLI) are cohesive single features. No tasks need splitting. Task 15 (validation) is comprehensive but appropriately serves as the final checkpoint.

---

### [PASS] Tasks are independently completable

**Description:** Each task has clear success criteria that a junior AI can verify. DB methods, analyzer, CLI commands, and tests all have explicit acceptance conditions.
