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
dateCreated: 20260927
dateUpdated: 20260927
reviewedSha: b5d50b2293d733da58ab13756d3e494ca5e43b4e
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 30
findings:
  - id: F001
    severity: concern
    category: consistency
    summary: "The retention guarantee and the deferred cadence contradict each other; with no timer there is no \"next pass\" to meet the 30-day deadline"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#technical-considerations"
  - id: F002
    severity: concern
    category: completeness
    summary: "Supersession is under-specified for the overlapping-range case the design itself creates"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#architectural-principles"
  - id: F003
    severity: concern
    category: feasibility
    summary: "The ingest pass depends on forward CME session population, but forward extension is minute-pass-driven — tick ingest can fail on an unpopulated calendar with no in-initiative mechanism to populate it"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#technical-considerations"
  - id: F004
    severity: concern
    category: abstraction
    summary: "Single hypertable design conflates trades-tier and tbbo-tier rows for the same contract, and the tier-upgrade supersession path fights the natural key"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#envisioned-state"
  - id: F005
    severity: concern
    category: consistency
    summary: "\"Same command, same state\" for backfill vs. steady-state hides an unresolved state-machine coupling with FetchStatus semantics"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#architectural-principles"
  - id: F006
    severity: concern
    category: feasibility
    summary: "The estimate→guard→submit path has no idempotency for the estimate-vs-actual gap, and the 30-day cap can be gamed by estimate accuracy"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#architectural-principles"
  - id: F007
    severity: note
    category: antipattern
    summary: "Scope is very large for one initiative; the \"parity is delivered in two tiers\" split is the only thing keeping it reviewable, and it deserves the sequencing gates it describes"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#anticipated-slices"
---

# Review: arch — initiative 220

**Verdict:** CONCERNS
**Model:** moonshotai/kimi-k3

## Findings

### [CONCERN] The retention guarantee and the deferred cadence contradict each other; with no timer there is no "next pass" to meet the 30-day deadline

The document makes download-before-retention-expiry "enforced, not assumed" (Architectural Principles, "Acquire and ingest are separate") and builds four mechanisms around it (Technical Considerations, "Delivery mode and retention"): ordering, explicit expiry, escalation, and "cadence bounded by the window." But "Cadence" simultaneously defers all schedule code indefinitely ("No schedule code is built until realtime work makes the cadence decidable: no timers, no `schedule_for` branch, no `PassKind.TICK` firing calendar... every tick pass runs by hand"). The four mechanisms do not compose without a timer:

- The reconcile phase only runs when a pass runs. With no timer, a pass runs only when an operator remembers to run it. A submitted job's 30-day window will silently expire if nobody runs the pass for 30 days — entirely plausible during a pause between slices (the document itself contemplates a long wait for slice 923).
- The escalation finding in the health check is keyed to "the tick pass's next scheduled firing (`schedule_for`)", and the fallback for on-demand runs is "any in-flight unit within a fixed number of days of its deadline" — but the health check (919) is itself a pass that must be run; if it is timer-driven today and tick has no timer, the finding fires only when the operator happens to run health. The document never states whether the health pass remains timer-driven and picks up tick findings, so the "human in the loop" link is assumed, not specified.

The practical failure mode: a manual purchase run leaves a unit at *submitted*; work pauses; the window lapses; the unit transitions to *failed* "retention expired" only when someone next runs the pass — i.e., after the money is already committed and the download is lost. The design documents this as a named state but provides no mechanism that prevents it in the manual-only regime it has chosen. Either the "enforced, not assumed" claim should be downgraded to "operator responsibility until cadence exists," or a minimal schedule (even a daily systemd timer that only reconciles, explicitly allowed as the exception to "no schedule code") is needed to close the hole.

### [CONCERN] Supersession is under-specified for the overlapping-range case the design itself creates

"Every row knows its unit; replacing data is a supersession" states: "When one unit replaces others over a range, the superseded units' rows for that range are deleted and the new unit's loaded in one transaction." The design elsewhere deliberately creates overlapping coverage: a CME session spans two calendar days, so it is covered by two day-grained units, and the ingest ledger is keyed (instrument, session, unit) precisely so a session can be "written by more than one source" (Technical Considerations, "Realtime paths"). When historical data later supersedes a live segment — the stated default precedence — the replacement unit's range is a calendar day, but the superseded live segment's range is presumably a sub-day capture window. The document never defines:

- The range semantics of "over a range" — is supersession keyed on the *range* recorded on the unit, on the instrument-session ledger grain, or on the natural key? Deleting "the superseded units' rows for that range" only works if the superseding and superseded units' ranges nest cleanly; a day unit superseding a 6-hour live segment leaves the other 18 hours of live rows whose session-ledger rows must be reconciled, and nothing says which transaction owns that.
- What happens to the superseded unit's *ledger rows*, which is what completeness reads. "Session totals are summed at read time over the session's current units, excluding superseded ones" handles the simple all-or-nothing case, but a partial-range supersession means the superseded unit is still "current" for part of the session while another part is now served by the new unit — the (instrument, session, unit) grain cannot express "current for timestamps 09:00–15:00 only."
- The transaction boundary between deleting old rows and loading new ones spans, by the document's own admission, multi-gigabyte COPY loads; "deleted and loaded in one transaction" conflicts with the ingest design where "a unit's ledger rows and its *ingested* transition commit together, after its last batch" — a supersession that must delete first and load multi-GB second holds an open transaction with row-deletion locks for the duration of a long COPY, with attendant vacuum/bloat and lock-contention costs that the "concurrent-pass contention" measurement does not cover.

This is the hardest correctness problem in the design and it is left at one paragraph.

### [CONCERN] The ingest pass depends on forward CME session population, but forward extension is minute-pass-driven — tick ingest can fail on an unpopulated calendar with no in-initiative mechanism to populate it

The session model (Technical Considerations, "Session model") seeds the CME calendar "backward over the wanted range" and relies on "the same per-calendar extension that keeps `NYSE` current (`TRADING_SESSIONS_EXTENSION_YEARS`, `mt data extend --calendar`, and the automatic extension `mt data status` runs)." Verified against the code: `TradingCalendar` raises `OutOfHorizonError` past the populated horizon, and `populate_trading_sessions` is the single population path the document plans to extend with the open-after-close rule. The gap:

- The session-boundary validation check treats "a timestamp outside the populated range" as "an explicit failure... never a guess." So ingest of any unit whose range extends past the CME calendar's populated horizon *fails the unit*. Forward extension is driven by `mt data status`/auto-extension machinery that today serves NYSE and lives in the minute tier's operational cadence. With no tick timer and a manual-only tick pass, nothing in this initiative guarantees the CME calendar is populated forward at the moment an operator runs ingest. A unit bought "for development and testing, to take the plan's included year" reaches forward to the availability edge; if the CME horizon lags, ingest fails with a session-model error whose remedy ("run `mt data extend`") belongs to a different source's command surface.
- The document also claims the existing `TradingCalendar` is "the single session-query function I4 requires," but verified code shows its RTH path is *date-keyed* (`get_trading_hours(trade_date)`); the document itself notes the new need is "a method on `TradingCalendar`... answering which session contains a timestamp." That timestamp→session lookup is a new API on a shared, minute-critical class, and the "one rule" change to `populate_trading_sessions` (open after close ⇒ open on previous day) alters the function the minute tier's `get_trading_hours` RTH path delegates to for algorithm parity. The claim "NYSE opens before it closes, so its rows are unchanged" is true for the generated rows but the change is in a shared code path whose existing NYSE tests may not cover the new branch; the document's own testing strategy section never mentions regression coverage of `populate_trading_sessions` for NYSE as an obligation.

### [CONCERN] Single hypertable design conflates trades-tier and tbbo-tier rows for the same contract, and the tier-upgrade supersession path fights the natural key

"One hypertable for the trades tier — trade fields always present, best-bid/offer fields present when the instrument's tier includes them" with tier "never a column on the tick row." Two problems:

- The document acknowledges tbbo "shares the natural key" with trades, so a tbbo unit over history already loaded as trades "would otherwise silently no-op" — hence supersession. But supersession is described per-range/per-unit. For a contract whose *configured tier changes* (the stated use case: "a tier upgrade over loaded history"), every unit in the contract's history must be superseded, meaning the upgrade is a full re-ingest of the contract's archive with per-range deletes. That is feasible but is a bulk maintenance operation on a multi-billion-row hypertable holding deleted+rewritten rows; the document's own minute-tier history (chunk geometry failure at 7B rows, journal 20260719) is cited as the cautionary tale, yet the vacuum/dead-tuple cost of a full-table tier upgrade is never measured in the proof's list — the proof only measures load, chunk geometry, and query latency on *fresh* data.
- Row-width heterogeneity in one table: "48-byte trades and 520-byte definitions" is cited for decode batches, but the same issue lands in storage — trades rows are narrow, tbbo rows carry quote fields. A nullable-BBO single table pays the tbbo width for every trades-tier row in compression layout (the candidate "segment by instrument, order by event time and sequence" is one layout for the whole table, while the document itself says per-instrument row counts "differ by orders of magnitude, which is where a single global choice hurts"). The physical-grouping decision is deferred to the proof, but the *table-count* decision (one table vs. one per tier) is made now and is the more consequential one; the document asserts it without recording an alternative or why "a row whose BBO fields are null is a trades-tier row by construction" beats two tables, given that two tables would make the tier upgrade a non-event (load tbbo into its own table; no supersession needed).

### [CONCERN] "Same command, same state" for backfill vs. steady-state hides an unresolved state-machine coupling with FetchStatus semantics

Each unit "carries the minute tier's fetch-state vocabulary, `FetchStatus`... `PROVIDER_HOLE` when the provider reports every day of the unit as missing." Verified: `FetchStatus` in `src/manta_trading/data/quality/fetch_status.py` is documented as "lifecycle states for a data_gaps row" with `OPEN_FETCH_STATUSES` rendered into minute-track views (`data_status.gap_count`). Reusing the enum values for archive units is fine, but the semantics don't transfer cleanly and the document doesn't reconcile them:

- In minute, `PROVIDER_HOLE` is terminal ("the provider's answer that nothing is there"). For a tick unit, "the provider reports every day of the unit as missing" can later change: dataset condition metadata has a `last_modified` field and conditions move from pending→available→(degraded/missing). A day marked *missing* at request time but later backfilled by the provider would leave a tick unit terminally holed while the same range is actually purchasable — the "manual reset reopens exhausted units" is described for `RETRY_EXHAUSTED` ("as minute's gap reset does"), not for `PROVIDER_HOLE`. Whether a holed unit re-enters the wanted set when the dataset-condition metadata improves is unstated.
- The wanted-set computation is "universe × range minus the manifest in the provider's own request grain." A `PROVIDER_HOLE` unit is in the manifest, so it subtracts from the wanted set forever — meaning "caught up" can be reached with holes that are only visible via status. The document's completeness definitions never say whether a holed instrument-session counts as complete, complete-with-zero-records, or failed; the closest statement ("A missing day is also where a unit's `PROVIDER_HOLE` comes from") implies the session is surfaced as *missing* via the worst-condition rule, but then "the universe is caught up when every configured instrument is complete for every session in its wanted range" — is a missing session in the wanted range or out of it? The definitions of caught-up, complete-with-zero-records, and provider-holed overlap without a stated precedence.

### [CONCERN] The estimate→guard→submit path has no idempotency for the estimate-vs-actual gap, and the 30-day cap can be gamed by estimate accuracy

"Cost is a first-class input" commits: the rolling 30-day cap is "summed from the manifest, which records each unit's estimate at *requested* and the provider's actual cost once the job reports it. The sum uses the actual where known and the estimate otherwise." Two gaps:

- Between *requested* and job completion, the cap counts the *estimate*. `get_cost` on Databento is documented as an estimate; if it systematically underestimates (e.g., on the plan boundary, where whether plan-included data prices at $0 is explicitly "verified when the plan is subscribed" — i.e., unknown today), a pass can submit units whose actuals blow the 30-day cap *after* all local checks passed. The document treats the provider-side account limit as "the outer guard," so the failure mode is: jobs are submitted, the account limit blocks or bills them, and reconciliation discovers it. The design has no stated policy for what a unit that *submitted successfully but billed above the cap* does to subsequent passes (does the pass refuse to buy anything until the trailing-30-day sum drains? that would stall the whole initiative on one estimate error). "An overrun counts as soon as the provider reports it" says how it is counted, not what happens next.
- `BatchJobState` (verified in `src/manta_trading/data/tick/constants.py`) has only QUEUED/PROCESSING/DONE/EXPIRED — no FAILED/ERROR state. The manifest state machine has "*failed* reachable from any state," but the reconcile phase maps provider job states to unit states; if Databento's batch API reports a failed job via a state not in the shipped enum (the enum is the slice-220 author's reading of the API), reconcile has an unmapped state. The document's own rule about lenient parsing and its repeated "verify at slice design" flags don't cover what reconcile does with an unknown provider state — the risk is a submitted unit that never advances and never fails, defeating the in-flight reconciliation the whole state machine exists for.

### [NOTE] Scope is very large for one initiative; the "parity is delivered in two tiers" split is the only thing keeping it reviewable, and it deserves the sequencing gates it describes

The Anticipated Slices list is effectively nine slices spanning storage, acquisition, ingest, session model, roll methods, CLI parity, API parity, backup, and steady-state arbitration, with an external hard gate (923, "Scheduling 923 is the PM's call") on the critical path. The document is candid about this, and the proof-parity/full-parity split plus the explicit 923 gate ("a hard gate with no fallback, by design") are the right mitigations. One residual risk: the initiative's own success metric ("a small ES tick history sits in the archive and the database, every number... measured on it") is achievable only after the storage track, which is gated on 923, which this initiative does not own — so the primary goal can be blocked indefinitely by work outside its control, and "220's and 221's outputs do not decay" is asserted only for fixtures/SDK, not for the operator procedures and PM attention the manual-pass regime depends on (see the retention finding). Worth an explicit escalation path if 923 slips past the retention window of any in-flight purchases.

### Run Digest

- Response length: 16765 chars
- Response is newline-free: no
- Tool calls made: 30
- Tool calls failed: 0
- Stop reason: stop
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
