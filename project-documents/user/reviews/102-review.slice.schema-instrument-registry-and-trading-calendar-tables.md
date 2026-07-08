---
docType: review
layer: project
reviewType: slice
slice: schema-instrument-registry-and-trading-calendar-tables
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/slices/102-slice.schema-instrument-registry-and-trading-calendar-tables.md
aiModel: claude-sonnet-4-6
status: complete
dateCreated: 20260402
dateUpdated: 20260402
---

# Review: slice — slice 102

**Verdict:** CONCERNS
**Model:** claude-sonnet-4-6

## Findings

### [PASS] Table Placement on Correct Host

All four new tables (`instruments`, `provider_symbol_mapping`, `trading_calendars`, `trading_holidays`) and the `minute_ohlcv` column addition are targeted at `<db-host>` (TimescaleDB host). The architecture explicitly requires `instrument_id` FK targets to be on the same host as `minute_ohlcv` and future tick data. The architecture's statement that "No changes to `MarketDB` or its tables on the PostgreSQL 16 host" is correctly reflected in the Out of Scope section.

---

### [PASS] Calendar + Seed Data Treated as Atomic

The architecture calls out a hard constraint: `TradingCalendar.__init__` calls `_load_calendar_data()` unconditionally and raises `ValueError` if the DB row is missing. The architecture states this means "schema creation and seed data must be a single atomic slice." Slice 102 delivers both schema and seed data in the same migration sequence (migrations 001–008), satisfying this constraint before slices 103/104 ever instantiate those classes.

---

### [PASS] instrument_id Column Matches Architecture Guidance

The architecture specifies `instrument_id` should be "nullable initially and backfilled via a migration script" with existing symbol-keyed queries continuing to work. The slice adds `BIGINT NULL` with no FK and no index, defers backfill to future work, and explicitly verifies continuous aggregate compatibility (architecture §Technical Considerations confirms aggregates use explicit column lists, so `ADD COLUMN` is invisible to them). This is a precise implementation of the architecture's stated approach.

---

### [PASS] Dependency Direction and Integration Points

The slice correctly depends on Slice 100 for `TimescaleMinuteDataDB` + psycopg3 pool, and provides tables to slices 103/104/105 in the order the slice plan requires. The slice plan (the `parent` document) lists Slice 105's dependencies as `[100]` only — slice 105 creates the tick hypertable on a separate instance and only needs `instruments` for FK reference, which this slice provides. Direction is correct throughout.

---

### [PASS] Tick Hypertable Deferral Is Architecturally Sound

The architecture's "Anticipated Slices" section groups tick schema with instrument/calendar schema into one "Schema and migrations" bullet. The slice plan refines this by making tick its own slice (105) with `dependencies: [100]` — independent of 102. The architecture document's anticipated slices are guidance; the slice plan's decomposition is the authoritative breakdown. Deferring tick to slice 105 does not violate the schema-first principle because the tick hypertable will still be created before any tick application code (Initiative 120).

---

### [CONCERN] Internal Contradiction: CLI Command Explicitly Excluded Then Defined

The **Technical Decisions** section contains a subsection titled "No CLI Commands in This Slice" that reads:

> *"No new CLI commands. Schema inspection is done via `psql` or integration tests. This slice is pure schema — no application-layer reads or writes beyond the migration runner."*

The document then contradicts this in the **CLI Entry Point** section, which fully specifies an `mt data migrate` command, including invocation, behavior, and output. This command also appears in the Success Criteria and the Verification Walkthrough (step 1 and step 6).

The command itself is appropriate — the architecture is CLI-first, and a scriptable migration entry point is needed for operational use. The problem is the explicit "No CLI Commands" decision text, which is wrong and was not updated when the CLI section was added. An implementer reading the decision section first may omit the command; one reading the CLI section first may add it. This must be resolved before implementation begins.

**Recommended fix:** Remove or replace the "No CLI Commands in This Slice" subsection with an accurate statement, e.g. "One minimal CLI command (`mt data migrate`) is added to make migration application scriptable. No commands for reading the new tables are added — those are in slices 103 and 104."

---

### [CONCERN] Missing FK Constraint from `instruments.trading_calendar_id` to `trading_calendars`

The `instruments` table defines `trading_calendar_id VARCHAR(32)` which logically references `trading_calendars.calendar_id`, and the column alignment table explicitly maps this to the class attribute `trading_calendar_id: str | None`. However, no `REFERENCES trading_calendars(calendar_id)` constraint is present in the DDL, and no subsequent migration adds one.

This is a real enforcement gap: an instrument record can be inserted with a `trading_calendar_id` value that has no corresponding row in `trading_calendars`, and the database will accept it silently. This has downstream consequences for slice 104 (`TradingCalendar` queries) when it joins on this field.

The migration ordering makes the FK deferred-add pattern necessary (instruments at migration 002, trading_calendars at migration 004), but the solution is straightforward: add a migration step (e.g., `009_instruments_calendar_fk`) after 004 that executes:

```sql
ALTER TABLE instruments
    ADD CONSTRAINT fk_instruments_calendar
    FOREIGN KEY (trading_calendar_id)
    REFERENCES trading_calendars(calendar_id);
```

The slice already sets the precedent for deferred constraints — it deliberately defers the `minute_ohlcv → instruments` FK to the backfill step with clear rationale. The `instruments → trading_calendars` FK has no such rationale for deferral: both tables are created in this slice, the seed data is inserted in the same migration run, and the column is never populated before `trading_calendars` exists.
