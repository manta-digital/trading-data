---
docType: review
layer: project
reviewType: arch
slice: data-acquisition-futures-tick-primary-focus
targetKind: arch
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md
aiModel: moonshotai/kimi-k3
status: complete
dateCreated: 20260923
dateUpdated: 20260923
reviewedSha: be7a06c7c6a37e7e62be26651223f0a9bf0bd41e
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 37
findings:
  - id: F001
    severity: concern
    category: consistency
    summary: "I4 \"pure boundary function\" conflicts with the DB-backed calendar implementation"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Technical Considerations"
  - id: F002
    severity: concern
    category: completeness
    summary: "Calendar schema cannot represent sessions that open on the prior calendar date"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Technical Considerations"
  - id: F003
    severity: concern
    category: completeness
    summary: "Hard dependency on slice 923 without confirmed scheduling"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Anticipated Slices"
  - id: F004
    severity: concern
    category: technology
    summary: "`create_app` gains a `tick_db_url` seam, conflicting with the existing \"never read from environment\" discipline"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Technical Considerations"
  - id: F005
    severity: concern
    category: completeness
    summary: "\"Bounded record batches\" and \"the bound is the backpressure\" are under-specified for a multi-gigabyte ingest"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Architectural Principles"
  - id: F006
    severity: concern
    category: consistency
    summary: "Session template is described as \"code and configuration, not data\" but must live in the production database for I4 compliance"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#Technical Considerations"
  - id: F007
    severity: note
    category: consistency
    summary: "`PassKind.TICK` already exists in `src/manta_trading/data/acquisition/state.py` but not in `pass_runs.PassKind`"
    location: "unverified"
---

# Review: arch — slice 0

**Verdict:** CONCERNS
**Model:** moonshotai/kimi-k3

## Findings

### [CONCERN] I4 "pure boundary function" conflicts with the DB-backed calendar implementation

The architecture asserts the futures session model is "a pure boundary function over a per-product session template plus a stored exception calendar" and insists it be served through "the single session-query function I4 mandates." In the codebase, however, that single function is `TradingCalendar.get_trading_hours()` in `src/manta_trading/data/base/trading_calendar.py`, which is backed by a `psycopg_pool.ConnectionPool` reading `trading_calendars`, `trading_holidays`, and `trading_sessions`. It is not a pure function; it opens connections, caches per instance, and raises `OutOfHorizonError` when data is missing. The document presents the CME session model as code and configuration, but the only existing session-query surface requires database state. Slice design will have to choose between (a) adding a second, genuinely pure function and violating I4, or (b) shoehorning futures sessions into `TradingCalendar`, which inherits the horizon/extension machinery and database dependency the architecture says is not required.

### [CONCERN] Calendar schema cannot represent sessions that open on the prior calendar date

The document correctly identifies that CME sessions (e.g. a Sunday-evening open) span two calendar dates, and flags this as a "slice-design verification item." The existing schema, visible in `src/manta_trading/data/base/session_population.py` and `src/manta_trading/data/base/trading_calendar.py`, stores one row per `(calendar_id, session_date)` with `session_open_utc` and `session_close_utc` both derived from that same calendar date. A CME session that opens Sunday 18:00 and closes Monday 17:00 has no natural representation: it would need a row dated Sunday with a close on Monday, or a row dated Monday with an open on Sunday, neither of which the current populate logic generates. The document defers the decision of whether to change the minute-track schema "for every calendar," but this is not a minor detail — the completeness ledger, the session-boundary validation check, and the derived-bar cross-check all depend on getting session identity right before the first purchase. The architecture should have settled the representation, or at least bounded the options.

### [CONCERN] Hard dependency on slice 923 without confirmed scheduling

The storage track explicitly depends on foundation slice 923 ("multi-database migration and credential plumbing") being sequenced first. The document states this "is slice 923 of the foundation-cleanup plan (`900-slices.foundation-cleanup.md`), not a slice of this one." I could not verify from the architecture document or the referenced plan excerpts that 923 is actually scheduled, staffed, or sequenced before the tick work. If 923 slips or is descoped, the tick storage track has no fallback: the migration CLI is single-database today, and the document rules out sharing the production database. This is a load-bearing external dependency that the architecture treats as settled but does not control.

### [CONCERN] `create_app` gains a `tick_db_url` seam, conflicting with the existing "never read from environment" discipline

The architecture proposes that `mt-serve` starts without the tick database and that `create_app` gains a `tick_db_url` seam. However, the existing codebase pattern (slice 187 D9) is that `create_app(db_url=…)` receives the URL explicitly so the load tier never reads `MT_TIMESCALE_DB_URL` from the environment. Adding a `tick_db_url` parameter continues that pattern, but the architecture then says "the tick pool is opened at startup but its failure is recorded, not raised." That is a new failure posture for the codebase — today a missing primary database fails fast at startup. Introducing a soft-failure pool for tick while keeping hard-failure for the primary is defensible, but it is not "the same posture" as the existing API, and the document does not acknowledge the operational complexity of an API that starts in a partially degraded state. Slice design will need to define health-check semantics, retry behavior, and how `/api/v1/health` reports the distinction.

### [CONCERN] "Bounded record batches" and "the bound is the backpressure" are under-specified for a multi-gigabyte ingest

The adapter is defined as a synchronous iterator yielding "bounded record batches," with the bound described as "the backpressure." The document does not say what the bound is — rows, bytes, or time — nor who sets it. For a multi-gigabyte DBN file, a row count that is too small creates excessive `COPY` round-trips; a byte size that is too large risks memory pressure when the Rust decoder materializes records. The architecture also leaves the threads-versus-processes decision to a slice-design verification of whether the decoder releases the GIL. That is reasonable, but the batch-bound decision interacts with it: in a process-pool model, the batch must be pickled across a pipe; in a thread model, it can be shared memory. Deferring both the bound and the execution model while calling the bound "the backpressure" leaves a critical performance parameter undefined at the architecture level.

### [CONCERN] Session template is described as "code and configuration, not data" but must live in the production database for I4 compliance

The architecture states the per-product session template (weekly open, daily close, maintenance break) is "code and configuration, not data," while the exception calendar is stored in the production database. Yet the same section insists the session model be served through the single I4 session-query surface, which today reads its configuration from `trading_calendars` rows. If the template is truly code/config, it cannot be read by `TradingCalendar`; if it is meant to be rows in `trading_calendars`, it is data. This is not merely semantic: the `TradingCalendar` constructor expects a `calendar_id` row with `market_open`, `market_close`, etc., and the populate job generates `trading_sessions` rows from those columns. A futures template with a daily maintenance break does not fit the existing "one open, one close per day" schema. The architecture should have specified whether the template becomes a new table, a new calendar row shape, or a parallel config structure — each has different migration and I4-compliance implications.

### [NOTE] `PassKind.TICK` already exists in `src/manta_trading/data/acquisition/state.py` but not in `pass_runs.PassKind`

The architecture says `PassKind.TICK` will be added to the `pass_runs` CHECK constraint via a minute-track migration. A grep shows `TICK = "tick"` already exists in `src/manta_trading/data/acquisition/state.py` at line 40, but not in `src/manta_trading/data/acquisition/pass_runs.py` (which defines the `PassKind` used by the CHECK constraint). The document should clarify whether the existing `state.TICK` is a legacy placeholder to be removed or whether the new `PassKind.TICK` will be a separate enum member, to avoid two tick enumerations in the same codebase.

### Run Digest

- Response length: 8570 chars
- Response is newline-free: no
- Tool calls made: 37
- Tool calls failed: 2
- Stop reason: stop
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
