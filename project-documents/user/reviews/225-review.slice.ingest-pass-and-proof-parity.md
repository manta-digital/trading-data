---
docType: review
layer: project
reviewType: slice
slice: ingest-pass-and-proof-parity
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/225-slice.ingest-pass-and-proof-parity.md
aiModel: claude-opus-5-5
status: complete
dateCreated: 20260930
dateUpdated: 20260930
reviewedSha: f2b66de67ee4a7342fc4ffa502d7c033d7446148
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 6
squadronVersion: 0.16.0
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Acquire/ingest split, per-unit worker and connection, and write path match the architecture"
    location: "project-documents/user/slices/225-slice.ingest-pass-and-proof-parity.md#technical-decision-2-one-transaction-per-unit-on-the-workers-own-connection-with-a-plain-copy"
  - id: F002
    severity: pass
    category: architecture-alignment
    summary: "Validation loop, ledger grain and supersession match the architecture"
    location: "project-documents/user/slices/225-slice.ingest-pass-and-proof-parity.md#technical-decision-5-a-higher-tier-supersedes-a-loaded-lower-tier-at-ingest"
  - id: F003
    severity: pass
    category: dependencies
    summary: "Isolation, dependency direction and scope boundaries respected"
    location: "project-documents/user/slices/225-slice.ingest-pass-and-proof-parity.md#technical-scope"
  - id: F004
    severity: concern
    category: architecture-alignment
    summary: "TD7's invariant (\"a unit's day is always inside the populated span\") is false at the calendar horizon"
    location: "project-documents/user/slices/225-slice.ingest-pass-and-proof-parity.md:504-512"
  - id: F005
    severity: concern
    category: architecture-alignment
    summary: "Unit-level \"complete\" in status is a deviation from the architecture's completeness definition but is not recorded in the supersedes list"
    location: "project-documents/user/slices/225-slice.ingest-pass-and-proof-parity.md:599-624"
  - id: F006
    severity: concern
    category: dependencies
    summary: "`status` and `coverage` add a production-database dependency not among the architecture's recorded coupling edges"
    location: "project-documents/user/slices/225-slice.ingest-pass-and-proof-parity.md:210-212"
  - id: F007
    severity: concern
    category: error-handling
    summary: "Hang/timeout and several failure modes are unspecified for the worker's new connection"
    location: "project-documents/user/slices/225-slice.ingest-pass-and-proof-parity.md:514-539"
  - id: F008
    severity: concern
    category: architecture-alignment
    summary: "Progress/heartbeat from worker to loop is dropped, contrary to the architecture"
    location: "project-documents/user/slices/225-slice.ingest-pass-and-proof-parity.md:215-217"
  - id: F009
    severity: concern
    category: nfr
    summary: "Ingest-throughput requirement only partially restated"
    location: "project-documents/user/slices/225-slice.ingest-pass-and-proof-parity.md:322-330"
  - id: F010
    severity: note
    category: architecture-alignment
    summary: "Deterministic failures skip `FAILED_RETRYABLE` and go straight to `RETRY_EXHAUSTED`"
    location: "project-documents/user/slices/225-slice.ingest-pass-and-proof-parity.md:527-534"
  - id: F011
    severity: note
    category: dependencies
    summary: "Supersession delete and overlap detection will need revisiting once 226 enables compression"
    location: "project-documents/user/slices/225-slice.ingest-pass-and-proof-parity.md:728-733"
---

# Review: slice — slice 225

**Verdict:** CONCERNS
**Model:** claude-opus-5-5

## Findings

### [PASS] Acquire/ingest split, per-unit worker and connection, and write path match the architecture

TD2 matches the architecture's "One provider adapter owns the wire format" principle:
- Each worker thread decodes through 220's bounded iterator and `COPY`s over a synchronous connection it owns.
- Records never cross into the event loop (lines 215–217).
- The ledger rows and the *ingested* transition commit on the connection that loaded the rows.

The thread-state review (lines 338–349) repeats 220 TD6's requirement, as that decision says it must.

### [PASS] Validation loop, ledger grain and supersession match the architecture

- **Checks:** counts, resolution and session boundaries gate the commit (TD2, TD6, TD8), with no silent fallback.
- **Ledger:** one row per (instrument, session, unit), including zero-record rows, with session totals summed over current units.
- **Supersession:** whole day over whole day in one transaction, as in the "Every row knows its unit" principle. TD5 fills the gap the architecture left (nothing wrote the tier-upgrade link) and records that in its "supersedes" list.
- **Rank and precedence:** "A loaded higher tier is never replaced by a lower one" and the `TICK_TIER_RANK` single definition fit the architecture's precedence rules.

### [PASS] Isolation, dependency direction and scope boundaries respected

- `data/tick` never imports `data/kalshi`.
- Production is read-only.
- Nothing is bought.
- Schedule code (`PassKind.TICK`, timer) stays with 233, matching the "Cadence" deferral.
- Tuning of workers, batch budget and write path is left to 226.
- API work is left to 230, and the slice returns `to_dict()` dataclasses so 230 can serve them unchanged (TD11).

Making `TickPass` generic is a declared divergence and is added to the standing Kalshi-diff task. That is the process the "third pass shape" principle requires.

### [CONCERN] TD7's invariant ("a unit's day is always inside the populated span") is false at the calendar horizon

Architecture round 3 (F003) decided that ingest extends the CME calendar itself, so it "does not rely on the minute tier" for this (arch line 149). 221 also lists `extend_calendar_sessions` as something 225 consumes (221 line 315). TD7 reverses that on the grounds that every unit's day came from `session_days` over the populated calendar.

That reasoning breaks at the horizon:
- The UTC day X on which the last populated session closes (around 21:00Z) is a day that session touches, so the planner can produce a unit for it.
- 221 specifies that `sessions_between(start, end)` raises `OutOfPopulatedRangeError` whenever `end_utc` is after the last populated close (221 line 214).
- This slice calls `sessions_between(day 00:00Z, day+1 00:00Z)` (line 190). For day X it will raise.
- Even apart from that, X's records after the evening reopen belong to a session that is not populated.

`OutOfPopulatedRangeError` raised during planning does not appear in TD8's failure table (lines 516–525), so its outcome is unspecified.

The horizon is normally years ahead, so this is rare, but the architecture ruled out depending on the minute tier's extension. Pick one of these:
- (a) Keep the architecture's extend-before-load step.
- (b) Show that 224's planner and 223's adoption never emit a day beyond the last populated close, and add a test for it.
- (c) Map `OutOfPopulatedRangeError` in planning to a named outcome in TD8 and document the manual `mt data extend` → `reset` remedy.

### [CONCERN] Unit-level "complete" in status is a deviation from the architecture's completeness definition but is not recorded in the supersedes list

The architecture defines an instrument-session as complete only when every covering unit is complete **and** the raw count equals the sum of the current units' ledger counts. Its principle is also titled "Completeness is answered from the manifest and the raw table". The plan entry says status reads "the manifest, the ledger, and raw counts".

TD10 changes this:
- `status` now reports `complete` without the raw-count half.
- "Caught up" (TD9, lines 593–597) is derived from that weaker `complete`.

The rationale (scanning about 1 B rows) is sound. But this redefines an architecture term, and it is missing from "Architecture and plan statements this design supersedes" (lines 1081–1099). Fix:
- Add the deviation to that list.
- Have `status` output say that its `complete` is unit-level, pointing to `coverage` for the raw proof, so an operator doesn't read it as the architecture's full definition.

### [CONCERN] `status` and `coverage` add a production-database dependency not among the architecture's recorded coupling edges

The "Operational state has one home" principle lists exactly three tick → production edges:
1. composing surfaces reading "what ran";
2. ingest reading the calendar;
3. acquisition planning and adoption reading the calendar.

`status` and `coverage` read the production calendar for sessions, and they exit 4 when production is unreachable (lines 702–703). That is a fourth edge. It also means a production outage blanks the tick status surface, although the manifest and ledger it mostly reads live on the tick database.

Fix:
- Record the new edge (in the supersedes list, and flag it for the architecture's Revision Log, as 224 did).
- Decide whether a production outage should degrade `status` (for example, report unit and ledger tallies without session classification) rather than fail it outright. Either way, state the choice.

### [CONCERN] Hang/timeout and several failure modes are unspecified for the worker's new connection

TD8 covers a lost tick database (`OperationalError`), but these cases are not addressed:
- **Hang, no timeout:**
  - No `connect_timeout`, TCP keepalive or `statement_timeout` is specified for the worker's synchronous connection.
  - A network partition that doesn't close the socket, or a `DELETE`/`UPDATE` waiting on a row lock held by another session, blocks the worker thread indefinitely.
  - Threads can't be cancelled from the loop, so the run hangs with no outcome.
- **Lost compare-and-set:** the table doesn't list `ManifestTransitionError` from `mark_ingested` or `mark_superseded` inside the worker's transaction. That happens when a concurrent `reset`, reopen or acquisition step changes the row. Is the result a rollback and skip, a unit failure, or a run abort?
- **Other database errors:** the table doesn't list `DataError`, `CheckViolation` (for example, the ledger's CHECKs) or other `IntegrityError`s besides `UniqueViolation`. As designed, they propagate to `run_phase`, but the slice doesn't say which run outcome they map to.
- **Abort with a worker still running:** "Workers already running finish or roll back" (line 533) is not a mechanism. State how the loop waits for, or abandons, the second worker's thread when the first raises.

Add rows for each of these, and name the timeout constants in `constants.py`.

### [CONCERN] Progress/heartbeat from worker to loop is dropped, contrary to the architecture

The architecture says the worker returns "only progress to the loop", and that the async contract "orchestrates phases and heartbeats only ... so progress stays observable during a multi-gigabyte ingest and the unit's start-timeout budget is spent on work that reports." The plan entry repeats "Only progress returns to the loop."

In this slice, only a final `UnitOutcome` returns. Nothing is reported between a unit's start and its commit. Today's units are small (seconds each), but Standard-plan days and 233's systemd timeouts are exactly the case the principle was written for.

Either:
- specify a minimal per-batch progress callback (for example, via a thread-safe queue or `loop.call_soon_threadsafe`); or
- record the omission as a deviation and hand it to 226/233 explicitly.

### [CONCERN] Ingest-throughput requirement only partially restated

The architecture's pass/fail has two targets: "a day's sessions ... must ingest far faster than a day of market time" **and** "a month of sessions ... must ingest within an operator's working session."

TD2 restates the first target only, and as a prose estimate rather than a success criterion. Measurement belongs to 226. Still:
- restate both targets;
- make walkthrough step 4's recorded wall time an explicit check against them, so 226 starts from a stated baseline and not only from raw durations.

### [NOTE] Deterministic failures skip `FAILED_RETRYABLE` and go straight to `RETRY_EXHAUSTED`

The architecture's vocabulary is "`FAILED_RETRYABLE` after a failed attempt, until a configured attempt limit makes it `RETRY_EXHAUSTED`", and failed units "move to *failed*".

Keeping the unit at *verified* with `RETRY_EXHAUSTED` is well argued, since the file and definitions are immutable inputs. It is also consistent with 224's fetch-status model. For traceability, add a one-line entry to the supersedes list.

### [NOTE] Supersession delete and overlap detection will need revisiting once 226 enables compression

TD5's claim that the time-bounded `DELETE` touches only the chunks O used, and TD2's reliance on `UniqueViolation`, both assume uncompressed chunks. 226 decides the compression layout.

Deleting from compressed chunks, and enforcing unique constraints on them, behave differently in TimescaleDB, in both cost and constraints. Add a line under "Provides to Other Slices" saying 226 must re-verify supersession and overlap detection under the chosen compression policy.

### Run Digest

- Response length: 12019 chars
- Response is newline-free: no
- Tool calls made: 6
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 11
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 11
- Finding-shaped matches — surviving validation: 11
