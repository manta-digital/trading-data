---
docType: review
layer: project
reviewType: slice
slice: psycopg3-migration-and-connection-consolidation
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/slices/100-slice.psycopg3-migration-and-connection-consolidation.md
aiModel: claude-haiku-4-5-20251001
status: complete
dateCreated: 20260402
dateUpdated: 20260402
---

# Review: slice — slice 100

**Verdict:** CONCERNS
**Model:** claude-haiku-4-5-20251001

## Findings

### [CONCERN] Unaddressed psycopg2 Imports in Out-of-Scope Modules

**Description:**

The slice removes `psycopg2-binary` from dependencies and includes a verification step to "Verify no psycopg2/sqlalchemy imports remain in src/". However, InstrumentRegistry and TradingCalendar — which exist in `src/manta_trading/data/base/` — currently have direct psycopg2 imports:
- `instrument_registry.py`: lines 13-14 (`import psycopg2`, `from psycopg2.extras import RealDictCursor`)
- `trading_calendar.py`: lines 15-16 (same imports)

The slice scope explicitly marks these modules as out-of-scope: "The `data/base/` modules (`InstrumentRegistry`, `TradingCalendar`) are *not* migrated here — they have no working DB tables yet and will be written directly for psycopg3 in slices 103/104."

**The problem:** These modules are actively used in the codebase:
- `TradingCalendar` is imported by `session_classifier.py`
- `SessionClassifier` is imported by `data/historical_minute/service.py` and `processor.py`

When the slice removes `psycopg2-binary` from dependencies, the verification step `grep -r "import psycopg2" src/` will fail — psycopg2 imports still exist in src/. Alternatively, if the grep-check is understood to pass, the code will break at runtime when `session_classifier` tries to import `TradingCalendar` without psycopg2 installed.

The architecture states (line 43): "The `data/base/` modules don't have real tables yet and will be written directly for psycopg3 with URL-based connection, not migrated from the existing psycopg2 `db_config` pattern," implying the old versions will be replaced in slices 103/104. But the slice doesn't address what happens to the current versions during this migration.

**Required clarification/action:**
- Either include removal or stubbing of the old `InstrumentRegistry` and `TradingCalendar` classes as part of the dependency-swap phase
- Or clarify that the verification step only applies to the two modules being directly migrated (`MarketDB`, `TimescaleMinuteDataDB`)
- Or defer the `psycopg2-binary` removal until slices 103/104 complete the rewrite of these modules

---

### [PASS] Architectural Scope Alignment

**Description:**

The slice's scope is well-defined and properly aligned with the architecture document. The two main DB modules (MarketDB, TimescaleMinuteDataDB), Settings dual-URL fields, consumer updates, dependency swap, and test migration all directly implement what the architecture's "Anticipated Slices" section specifies. The slice does not exceed architectural boundaries or introduce scope creep.

---

### [PASS] Technical Decisions Alignment

**Description:**

The technical migration strategies properly align with the architecture's design principles:
- Constructor changes from individual connection params to connection URL strings match the "Single DB access layer, multiple databases" principle
- Use of `psycopg_pool.ConnectionPool` for both databases matches the specified pattern
- Settings fields (`market_db_url`, `timescale_db_url`) with `MT_` env var prefix match the architecture's expectation
- Silent failure fixes in `_create_market_db` directly address the concern flagged in the architecture document (line 140: "Fix `_create_market_db` silent exit (CONCERN from review)")

---

### [PASS] Dependency Management and Integration Points

**Description:**

The slice correctly identifies all major consumers of the two modules being migrated and proposes appropriate updates. The dependency swap (removing `psycopg2-binary` and `sqlalchemy`, adding `psycopg` and `psycopg_pool`) directly implements the architecture's consolidation goal. Consumer updates span CLI commands, services, and integration points comprehensively.

---

### [PASS] Testing and Risk Acknowledgment

**Description:**

The slice identifies key risks (pd.read_sql_query DataFrame structure, COPY performance, transaction semantics) and documents testing strategies to validate behavior. The proposed `conftest.py` fixture pattern for database availability aligns with the architecture's "Real data over abstractions" principle.
