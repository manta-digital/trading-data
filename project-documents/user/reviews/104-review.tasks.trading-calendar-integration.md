---
docType: review
layer: project
reviewType: tasks
slice: trading-calendar-integration
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/104-tasks.trading-calendar-integration.md
aiModel: z-ai/glm-5
status: complete
dateCreated: 20260403
dateUpdated: 20260403
---

# Review: tasks — slice 104

**Verdict:** CONCERNS
**Model:** z-ai/glm-5

## Findings

### [PASS] Success criteria coverage is comprehensive

All functional and technical success criteria from the slice design trace to specific tasks:
- `MarketStatus` StrEnum and `Holiday` update → Tasks 1-2
- `TradingCalendar` lazy init with per-instance cache → Tasks 3-4
- `is_trading_day()` with weekend/holiday detection → Tasks 5-6
- `get_trading_hours()` with holiday overrides → Tasks 7-8
- `get_expected_bar_count()` with ZoneInfo DST handling → Tasks 9-10
- CLI `mt data calendars` commands → Tasks 11-12
- Integration tests against TimescaleDB → Task 13
- No `@lru_cache`, no `pytz`, parameterized queries → Tasks 1, 3, 9

### [PASS] Task sequencing follows correct dependency order

- Tasks 1-2 establish `MarketStatus` enum (foundation for all methods)
- Tasks 3-4 rewrite core before implementing methods
- Tasks 5-6 implement `is_trading_day` and `get_holidays` (needed by `get_trading_hours`)
- Tasks 7-8 implement `get_trading_hours` (needed by `get_expected_bar_count`)
- Tasks 9-10 implement `get_expected_bar_count` (depends on `get_trading_hours`)
- Tasks 11-12 (CLI) depend on complete `TradingCalendar`
- Tasks 13-14 (integration and verification) come last

No circular dependencies detected.

### [PASS] Test-with pattern correctly applied

Each implementation task is immediately followed by its unit test task:
- Task 1 (implement) → Task 2 (test)
- Task 3 (implement) → Task 4 (test)
- Task 5 (implement) → Task 6 (test)
- Task 7 (implement) → Task 8 (test)
- Task 9 (implement) → Task 10 (test)
- Task 11 (implement) → Task 12 (test)

Integration tests (Task 13) follow all unit tests.

### [PASS] Commit checkpoints distributed throughout

8 commits distributed across the task breakdown (after Tasks 2, 4, 6, 8, 10, 12, 13, 14) rather than batched at the end. This enables incremental progress tracking and easier rollback.

### [PASS] Task sizes appropriately scoped

- Atomic tasks (enum creation, individual methods) are separate
- Related work is grouped appropriately (is_trading_day + get_holidays share DB patterns)
- No task appears too large to complete in a single session
- No task is so granular it should be merged

### [CONCERN] SessionClassifier end-to-end verification not explicit

**Slice design success criterion:** "SessionClassifier.classify_bar_session() works end-to-end with the rewritten calendar"

**Gap:** The task breakdown does not include an explicit test or verification step for `SessionClassifier` integration. The slice design notes that `SessionClassifier` already calls `TradingCalendar` methods correctly and "will work once methods are implemented," but this is a functional success criterion that requires verification.

**Current coverage:**
- Task 14 mentions running the full test suite and checking for regressions
- If existing `SessionClassifier` tests exist, they would catch breakage
- However, no explicit test verifies the end-to-end flow

**Recommendation:** Add an explicit verification in Task 13 or 14. For example:
- Add `test_session_classifier_with_trading_calendar` to Task 13 integration tests
- Or add a manual verification step in Task 14: "Verify `SessionClassifier.classify_bar_session()` returns correct session type for a known timestamp (e.g., 2025-01-02 10:00 ET → RTH)"
