---
docType: review
layer: project
reviewType: tasks
slice: psycopg3-migration-and-connection-consolidation
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/100-tasks.psycopg3-migration-and-connection-consolidation.md
aiModel: claude-haiku-4-5-20251001
status: complete
dateCreated: 20260402
dateUpdated: 20260402
---

# Review: tasks — slice 100

**Verdict:** CONCERNS
**Model:** claude-haiku-4-5-20251001

## Findings

### [FAIL] Task Sequencing - psycopg2 Import Removal Must Precede First Code Execution

**Issue:** Task 7 (Remove psycopg2 imports from data/base modules) is positioned AFTER tasks 4.5 and 5.4, which are the first code execution points (running pytest). This violates the task sequencing principle and creates a broken intermediate state.

**Why it matters:** 
- Task 1.1 removes the `psycopg2-binary` dependency from pyproject.toml
- Data/base modules (`instrument_registry.py`, `trading_calendar.py`) still have top-level `import psycopg2` statements (confirmed via grep)
- Other modules in the codebase import from `data.base` (e.g., `historical_minute/service.py`, `data/base/session_classifier.py`)
- Task 4.5 executes `pytest test/unit/testmarketdb.py -v` -- the first test run after the dependency swap
- If any test imports a module that transitively imports data/base, the `import psycopg2` will fail because the package is no longer installed

**Current order:**
```
Task 1.1 (swap dependencies) → Task 2.1-3.1 → Task 4-5 implementation → Task 4.5-5.4 (FIRST CODE EXECUTION) → Task 7 (remove imports) ❌
```

**Required order:**
```
Task 1.1 (swap) → Task 7 (remove imports) → Task 2.1+ → Task 4.5 (now safe to run tests) ✓
```

Or move Task 7 immediately after Task 1.1, before Task 2.1. This ensures the codebase is in a valid state before any code is executed.

**Also note:** The slice design's "Migration Order" section (lines 257-264) does not mention removing psycopg2 imports from data/base modules at all, even though this is listed as item 8 in the "In Scope" section. The slice design should clarify when this removal happens in relation to the dependency swap.

---

### [CONCERN] Incomplete Verification of Removed Methods

**Issue:** Tasks 4.1 and 4.4 remove methods without explicit verification of non-usage:
- Task 4.1: "Remove `__aenter__`/`__aexit__`/`aclose` (fake async wrappers)"
- Task 4.4: "Remove `showError` method (uses psycopg2-specific diagnostics)"

**Why it matters:** While the slice design notes these are "fake async wrappers with no async benefit" and psycopg2-specific, there's no explicit step to verify they're not called elsewhere in the codebase.

**Mitigation:** The test suite in Task 9.2 should catch any breaking removals, but consider adding explicit grep checks to Task 4.1 and 4.4:
```bash
grep -r "__aenter__\|__aexit__\|aclose" src/  # should be empty
grep -r "showError" src/                       # should be empty
```

This is a minor issue because tests should catch it, but the task description could be more explicit.

---

### [PASS] Success Criteria Coverage

All 10 success criteria are covered by tasks:
1. MarketDB no psycopg2 → Task 4 migration + Task 9.1 verification
2. TimescaleMinuteDataDB no SQLAlchemy → Task 5 migration + Task 9.1 verification
3. Settings dual-URL fields → Task 2.1
4. Dependencies removed → Task 1.1 + Task 9.4 verification
5. Dependencies added → Task 1.1 + Task 9.1 verification
6. CLI commands work → Task 6 consumers + Task 9.3 verification
7. Tests pass → Task 4.5/5.4/9.2
8. Error message on missing config → Task 6.1 + Task 9.3 verification
9. No silent connection failures → Task 4.1 + Task 9.2 verification
10. COPY bulk writes work → Task 5.2 + Task 9.2 verification

---

### [PASS] Task Granularity

Tasks are appropriately scoped:
- Implementation tasks grouped by module (MarketDB, TimescaleMinuteDataDB)
- Consumer updates grouped by concern (CLI, news.py, backtest, config)
- Support tasks (Settings, fixtures) separated cleanly
- No single task is obviously too large; none require splitting

---

### [PASS] Test-With Pattern

Implementation tasks are immediately followed by test updates:
- Tasks 4.1-4.4 (MarketDB implementation) → Task 4.5 (MarketDB tests) ✓
- Tasks 5.1-5.3 (TimescaleMinuteDataDB implementation) → Task 5.4 (TimescaleMinuteDataDB tests) ✓

---

### [PASS] Commit Checkpoints Distributed

8 commits are spread throughout the work (tasks 1.1, 2.1, 3.1, 4.5, 5.4, 6.4, 7.2, 8.1), not batched at the end. Checkpoints align with logical work units.

---

### [PASS] No Circular Dependencies

Task dependencies are linear and acyclic:
- Task 1 (dependencies) → Task 2 (settings) → Task 3 (fixtures) → Task 4-5 (implementations) → Task 6 (consumers) → Task 7-8 (cleanup) → Task 9 (verify)

(Note: Task 7 should move earlier per the FAIL finding above.)
