---
docType: review
layer: project
reviewType: code
slice: ingest-pass-and-proof-parity
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/225-slice.ingest-pass-and-proof-parity.md
aiModel: deepseek/deepseek-v4.1-flash
status: complete
dateCreated: 20260930
dateUpdated: 20260930
reviewedSha: f328230d409b21ef1c9d8f8d96db21a478169076
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 35
diffTruncated: true
squadronVersion: 0.16.0
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "A calendar that simply lacks coverage is reported as a storage outage"
    location: "src/manta_trading/data/tick/tick_status_build.py#calendar_sessions"
  - id: F002
    severity: note
    category: error-handling
    summary: "The supersession delete silently skips, and the resulting `overlap` failure blames the wrong thing"
    location: "src/manta_trading/data/tick/ingest_worker.py#supersede"
  - id: F003
    severity: note
    category: error-handling
    summary: "Ingest and the read verbs disagree on the exit code for \"tick database unreachable\""
    location: "src/manta_trading/cli/commands/tick_store_cmds.py#INGEST_EPILOG"
  - id: F004
    severity: note
    category: naming-structure
    summary: "The phase summary has two dict shapes distinguished by key presence"
    location: "src/manta_trading/data/tick/ingest_pass.py#_Tally"
  - id: F005
    severity: note
    category: security
    summary: "`tier_rank_sql` accepts a SQL placeholder where it documents a column"
    location: "src/manta_trading/data/tick/constants.py#tier_rank_sql"
  - id: F006
    severity: pass
    category: testing
    summary: "Test coverage is written with the implementation, including the concurrency tier"
    location: "test/load/test_225_tick_ingest_nfr.py"
---

# Review: code — slice 225

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash
**Diff:** truncated: 262144 of 271385 characters reached the model

## Findings

### [CONCERN] A calendar that simply lacks coverage is reported as a storage outage

`calendar_sessions` maps `OutOfPopulatedRangeError` to `TickCalendarError`, and `exit_code_for` (in `tick_exit.py`) maps `TickCalendarError` to `EXIT_STORAGE`. So a range the calendar has no rows for — `mt data tick coverage --start 2030-01-01 --end 2030-02-01`, or a held unit day past the populated span reaching `_scope` — exits 4, whose epilog text (`READ_EPILOG` in `tick_store_cmds.py`) says "the tick or production (calendar) database is unreachable". The database is fine and the remedy is `mt data extend`; the operator is pointed at a connectivity problem that does not exist. `ingest_plan.Calendars.frame` gets this right for the same error class — it converts it to `UnitCheckFailed(SESSION_BOUNDARY, planning_span_reason(...))` with the explicit "run mt data extend, then reset" remedy. The read verbs should distinguish the two cases (a preflight refusal naming `mt data extend` vs. exit 4) instead of collapsing them.

### [NOTE] The supersession delete silently skips, and the resulting `overlap` failure blames the wrong thing

`supersede` deletes the old unit's rows only when `old.state is UnitState.INGESTED and old.first_event_ns is not None`. Because `tick_trade`'s key (`TICK_TRADE_KEY`) does not include `unit_id`, a skipped delete does not orphan rows silently — the new COPY hits the unique key and the unit fails as `overlap`, which is good. But `overlap_reason` is built from `_other_current_units`, and by then the old unit is already marked superseded, so it is no longer "current": the message can read `... other current units on 2024-12-03: []`, sending the operator after a unit that does not exist instead of at the missing ledger bounds. Worth either including the superseded unit in the diagnostic or asserting that an INGESTED superseded unit has ledger bounds.

### [NOTE] Ingest and the read verbs disagree on the exit code for "tick database unreachable"

`TickDatabaseUnreachable` is a `TickPreflightError`, so it exits 1 for `ingest` (no `storage=` override is passed to `run_mapped`) and 4 for `status`/`coverage` (which pass `_read_storage()`). The split is deliberate and documented in `store_context.py`, but `INGEST_EPILOG` lists exit 4 as "storage (tick database or calendar)", which reads as if the initial connect failure would land there. Since exit 4 *is* reachable for ingest (a worker losing its connection mid-run), the epilog should say which tick-database failure maps to which code.

### [NOTE] The phase summary has two dict shapes distinguished by key presence

`_Tally.unit` writes `unit_id`/`unit_date`/`schema`/`outcome`/`records`/`reason`, while `_Tally.skip` writes only `unit_id`/`outcome`/`reason`. The renderer then infers the shape from key presence — `if "unit_date" in unit` and `if "duration_seconds" in unit` in `tick_ingest_render._unit_line`. Nothing enforces the invariant, so a future field added to one branch silently changes rendering of the other, and the `--json` payload shape is undocumented at the type level. A small frozen dataclass per entry (serialized via `asdict`) would make the contract explicit and let the renderer branch on the type rather than on string keys.

### [NOTE] `tier_rank_sql` accepts a SQL placeholder where it documents a column

The docstring and parameter say the argument is "a schema name" that is a code constant, but `ingest_plan._SUPERSEDED` passes `tier_rank_sql('%(schema)s')` — a bind placeholder, not a column. It works, and the interpolation is only of tier names and the caller-supplied text, but the function builds SQL by string interpolation with no validation and no note that the argument is embedded verbatim into the statement. Adding one line ("`column` is interpolated verbatim into the SQL text; it must be a constant, never input") or narrowing the accepted forms would keep the next caller from passing something user-derived.

### [PASS] Test coverage is written with the implementation, including the concurrency tier

`tests/` gains per-module unit tests (checks, records, rows, constants, CLI rendering), integration tests for selection, planning, worker transactionality, supersession and the two read verbs, plus `test_tick_ingest_rows.py::test_copy_types_match_the_table` pinning `COPY_TYPES` to the migration's DDL. `test/load/test_225_tick_ingest_nfr.py` bounds unit time and event-loop gap on the largest real day and fails (rather than skips) when the archived file is missing, satisfying the load-test tier requirement for the worker-thread/COPY path. The load tier keeps its own prod-URL guard.

### Run Digest

- Response length: 5791 chars
- Response is newline-free: no
- Tool calls made: 35
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 42262
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
