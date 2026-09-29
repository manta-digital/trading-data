---
docType: review
layer: project
reviewType: slice
slice: historical-acquisition-pass
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/223-slice.historical-acquisition-pass.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20260928
dateUpdated: 20260928
reviewedSha: 07884267b380b8f846f609b69fb1471933c0b86a
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 4
squadronVersion: 0.15.1
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Core acquisition contract matches the architecture"
    location: "project-documents/user/slices/223-slice.historical-acquisition-pass.md#Technical Decisions"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Reconcile can re-buy after an unknown submit outcome"
    location: "project-documents/user/slices/223-slice.historical-acquisition-pass.md:728-738"
  - id: F003
    severity: concern
    category: documentation-drift
    summary: "Divergences from architecture statements are recorded only in the slice plan Notes"
    location: "project-documents/user/slices/223-slice.historical-acquisition-pass.md:1386-1408"
  - id: F004
    severity: concern
    category: scope-boundary
    summary: "The ES universe entry (`ES.FUT` parent) buys spreads, contrary to an architecture principle the slice does not acknowledge"
    location: "project-documents/user/slices/223-slice.historical-acquisition-pass.md:60,465-478,534-541"
  - id: F005
    severity: concern
    category: architecture-alignment
    summary: "Definition projection inside the acquisition pass blurs acquire/ingest separation"
    location: "project-documents/user/slices/223-slice.historical-acquisition-pass.md:554-585"
  - id: F006
    severity: concern
    category: risk
    summary: "Definition change-detection risk is under-specified"
    location: "project-documents/user/slices/223-slice.historical-acquisition-pass.md:572-578,1290-1294"
  - id: F007
    severity: concern
    category: error-handling
    summary: "Failure-mode table omits archive-volume exhaustion and leaves a referenced timeout undefined"
    location: "project-documents/user/slices/223-slice.historical-acquisition-pass.md:988-1000"
  - id: F008
    severity: note
    category: operational-safety
    summary: "First real purchase is recorded in a manifest the walkthrough then drops"
    location: "project-documents/user/slices/223-slice.historical-acquisition-pass.md:1149-1151,1277-1284"
  - id: F009
    severity: note
    category: integration
    summary: "Advisory-lock sharing with 224 is left open"
    location: "project-documents/user/slices/223-slice.historical-acquisition-pass.md:1014"
  - id: F010
    severity: pass
    category: nfr
    summary: "NFRs and cadence deferral are restated or bounded correctly"
    location: "project-documents/user/slices/223-slice.historical-acquisition-pass.md#Technical Decision 9"
---

# Review: slice — slice 223

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Core acquisition contract matches the architecture

- The manifest row is written at *requested*, before the submit, carrying the estimate.
- The first phase reconciles in-flight units, and downloads run earliest deadline first before any new spend.
- Both ceilings are required and neither has a default. The guard refuses with the amounts over each ceiling and the date the plan would fit.
- Purchases are batch jobs only. Unknown job states exhaust the unit and name the state.
- Expired units are reopened, and the repurchase links back to the expired unit.
- The backup obligation is met in 223, as the architecture requires.
- The Kalshi contract is copied, not imported, with a parity test and declared divergences. This matches the "third source must not create a third pass shape" principle.
- The slice also lists what it excludes (schedule code, `PassKind.TICK`, status verbs) in line with the cadence deferral.

### [CONCERN] Reconcile can re-buy after an unknown submit outcome

The architecture's central promise is that "the same range is never bought twice". For an unknown submit outcome, the slice treats "no matching job in `batch_jobs_since`" as proof that the submit never reached the provider, and the purchase phase re-submits the same row. The Risk section (lines 1295-1315) concedes that a lagging job listing would produce exactly this double purchase. Its mitigation is that the duplicate is *accounted for* by the trailing sum. That is detection after the money is spent, not prevention. The claim that the next run comes "after a whole run's interval" is not a bound, because a manual run can follow immediately.

Specify a minimum age before "not found" is trusted, or require an explicit operator decision (for example through `reset`) for a request whose submit outcome was never resolved. Record the measured listing lag as a design input rather than a task-file afterthought.

### [CONCERN] Divergences from architecture statements are recorded only in the slice plan Notes

Six statements in the architecture are superseded, but the slice records them only in the slice plan's Notes. The architecture document is the parent authority, and its Revision Log is where such changes belong. Four of them contradict text in the architecture that a reader will otherwise take as normative:
- "Tick acquisition, which needs no calendar, continues" through a production outage. The slice makes planning and adoption depend on the production calendar. This is a new tick → production edge on the purchase path, beyond the two edges the architecture enumerates.
- `PROVIDER_HOLE` originating from missing days. The slice records a missing day in `tick_day_condition` and creates no unit.
- Definition scope, which the slice buys per tier request shape and not per configured product.
- Adoption per file, which the slice makes all-or-nothing per job.

The design reasoning is sound. Add an architecture revision entry so the two documents do not disagree, and state the calendar dependency in the architecture's production-coupling paragraph.

### [CONCERN] The ES universe entry (`ES.FUT` parent) buys spreads, contrary to an architecture principle the slice does not acknowledge

The architecture says calendar spreads and other non-outright instruments "are excluded from the universe unless explicitly configured". The slice's universe entry (`ES.FUT`, `parent`) covers spreads by construction. Technical Decision 5 makes the definitions request follow the same symbols so spread trades resolve. The reasoning is driven by the adopted files, which already contain spreads. But the slice never states that including spreads is an explicit configuration decision, or that it departs from the default. Nothing bounds the cost of spread data for later expansion (GC, further months).

State the decision explicitly, and say whether 225's go/no-go must re-confirm it.

### [CONCERN] Definition projection inside the acquisition pass blurs acquire/ingest separation

The architecture separates acquire and ingest as "each idempotent", and lists "definitions capture into the futures instrument model" under the storage track and ingest. The slice adds a decode-and-write phase to the acquisition pass. It uses the single async connection, not the dedicated per-unit worker connection the architecture prescribes for decode-and-write work. It marks units *ingested* with no ledger.

The scale (61 records per day) justifies this pragmatically, and the slice cites the plan's Notes for the assignment. Still, bound the exception explicitly: definitions are the only schema projected by acquisition, and the phase never grows to tier data. Otherwise 227's `statistics` schema will be tempted to follow the same path.

### [CONCERN] Definition change-detection risk is under-specified

Definitions are re-sent daily (about 27 times per instrument per month). Any difference in a kept column fails the unit terminally as `RETRY_EXHAUSTED`. The Risk section lists only missing validity windows. It does not consider that some kept column may legitimately vary across daily re-sends, for example a limit or reference price if that field is in `TICK_DEFINITION_COLUMNS`. The free-credit jobs hold no definition records, so this was not checkable at design time. A volatile kept column would exhaust every definition unit after the first day's re-send. Add this as a named risk. The walkthrough purchase is the first real observation, so say what the design does if it fails, for example narrowing the compared column set.

### [CONCERN] Failure-mode table omits archive-volume exhaustion and leaves a referenced timeout undefined

The table covers most new I/O paths well. Two gaps remain against the architecture's requirement for explicit handling:
- Disk full or write failure on the archive volume during `download_batch` or adoption copy is not enumerated. The adoption row says only "local I/O". Say whether it is a unit failure, a run abort, or a preflight free-space check.
- `TICK_DOWNLOAD_TIMEOUT_SECONDS` appears as the download bound, but the constants list (lines 218-219) does not include it. Say it is inherited from 220, or define it.

### [NOTE] First real purchase is recorded in a manifest the walkthrough then drops

The 30-day cap is summed from the manifest, but the walkthrough's paid definitions purchase lands in a scratch database that step 10 drops. The design's answer is that re-adopting archived job directories rebuilds the manifest, costs included. That works, but only if the pass-bought jobs' `manifest.json` files were written. Until a durable tick database exists, the provider-side account limit is the only durable spend record. The slice correctly flags the missing production cluster and recommends assigning it to 224 or 225.

### [NOTE] Advisory-lock sharing with 224 is left open

The lock serializes 223's manifest writers, and whether 224's ingest shares it is deferred. `reset` also targets units that 224's ingest exhausts. Two manifest writers under different locks could act on the same unit. Fix the answer here, or list it as an explicit 224 obligation.

### [PASS] NFRs and cadence deferral are restated or bounded correctly

The 30-day retention deadline (`download_deadline`, downloads ordered by it) and the wait budget are restated with specific values. The 15 s poll and 1,800 s budget are stated as conservative constants for 225 to re-set, consistent with the measured 5–84 s job durations. The slice builds no timer, `schedule_for` branch or `PassKind.TICK`, and that matches the architecture's cadence deferral. No ingest-throughput NFR is touched by this slice.

### Run Digest

- Response length: 9261 chars
- Response is newline-free: no
- Tool calls made: 4
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
