---
docType: review
layer: project
reviewType: slice
slice: active-contract-and-roll-methods
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/229-slice.active-contract-and-roll-methods.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20261009
dateUpdated: 20261009
reviewedSha: 8162bf8535a68b8cfb110348971155ed5e133a04
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 3
durationSeconds: 42.6
runId: run-20261010-p4-4b514b73
squadronVersion: 0.21.2
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "Slice contradicts itself on whether `coverage` gets the new snapshot and timeout"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:163-165"
  - id: F002
    severity: concern
    category: integration
    summary: "Snapshot ownership is unclear, and `status` may mix verdicts and rolls from different snapshots"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:614-616"
  - id: F003
    severity: concern
    category: error-handling
    summary: "The calendar read has no hang or disconnect bound"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:516"
  - id: F004
    severity: concern
    category: scope
    summary: "The slice reaches beyond roll resolution, into connection behaviour of existing commands and exit semantics"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:154-165"
  - id: F005
    severity: concern
    category: architectural-alignment
    summary: "Narrowing the architecture's \"next roll\" and calendar-offset wording is only planned, not yet recorded"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:429-433"
  - id: F006
    severity: concern
    category: risk
    summary: "The default rule depends on a lag inferred from two rolls"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:283-301"
  - id: F007
    severity: note
    category: architectural-alignment
    summary: "Open interest is evaluated and deferred, as the architecture asked"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:367-392"
  - id: F008
    severity: pass
    category: architectural-alignment
    summary: "Layering and dependency direction"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:140-168"
  - id: F009
    severity: pass
    category: architectural-alignment
    summary: "Explicit contract and rule on every answer, with reproducibility"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:191-217"
  - id: F010
    severity: pass
    category: error-handling
    summary: "Failure modes, bounds and the realtime-paths check"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:509-565"
---

# Review: slice — slice 229

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Slice contradicts itself on whether `coverage` gets the new snapshot and timeout

The component changes say the `_read` connection opens a read-only snapshot with a statement timeout "for `status`, `coverage` and `active`". TD9 says the opposite at lines 521-523: the snapshot and timeout "apply to `status` and `active`" and "`coverage` keeps its current connection". Success criterion 8b names only `status` and `active`. The architecture says `coverage` adds a raw-count proof over a bounded range. If it picked up the 30 s timeout, that scan could be cancelled. Decide which is intended and make the component list, TD9 and the tests agree. If `coverage` stays unchanged, `_read` needs a per-command switch, which the slice should describe.

### [CONCERN] Snapshot ownership is unclear, and `status` may mix verdicts and rolls from different snapshots

`build_schedule` is documented as opening its own snapshot (line 614). The component list says `_read` opens the snapshot (line 163). `latest_active` takes an already-open `conn` plus `verdicts` that `status` computed earlier (lines 186-189, 615). The slice does not say that `status` computes those verdicts inside the same `REPEATABLE READ` transaction as the roll reads. If it doesn't, the "one snapshot, consistent answer" claim in TD9 fails for `status`. Concurrent ingest could then leave a lag session marked COMPLETE while the ledger rows the roll reads differ. State who opens the transaction for each caller, and that verdicts and roll reads share it. 231 will call `build_schedule` too, so the ownership contract matters to it.

### [CONCERN] The calendar read has no hang or disconnect bound

TD9 gives the tick-database path a statement timeout and keepalives. The calendar read on the production database is covered only by "existing `calendar_sessions` mapping". The slice does not say whether that read has a statement timeout or detects a peer that drops mid-read. The calendar sits outside the snapshot and is called through `asyncio.to_thread` (lines 576-577), so a hang there would block `active` and `status` with no exit code. The architecture requires hang, timeout and peer-disconnect handling for each new I/O path. Either cite the existing bound for the calendar read or add one.

### [CONCERN] The slice reaches beyond roll resolution, into connection behaviour of existing commands and exit semantics

The slice changes `_read` connection behaviour (read-only snapshot, statement timeout, keepalives) for already-shipped verbs. It also widens `EXIT_PARTIAL` in `tick_exit.py` and makes `status` exit 3 on a roll-line error. These changes are not roll-method work. They could be justified as hardening that `status` needs once it reads rolls, but the slice does not say so, and the architecture does not assign them to this slice. Either limit the snapshot and timeout to the new roll reads, or state the reason and add regression tests for `status`'s existing exit codes and output. This also applies to the contradiction in the first finding.

### [CONCERN] Narrowing the architecture's "next roll" and calendar-offset wording is only planned, not yet recorded

The architecture says status "reports the active contract per configured product and rule, its expiration, and the next roll." For `v.0`, the default rule, the slice reports an exact next roll only through L+2. Otherwise it gives a no-later-than bound. The architecture also says the calendar offset is "days before expiry", and the slice makes it sessions (line 258). Both are reasonable and are listed as Revision Log work (lines 726-728), but they are deliverables not yet made, so the two documents disagree until then. Make the Revision Log entry a gating task before the slice closes, not a late doc step.

### [CONCERN] The default rule depends on a lag inferred from two rolls

The architecture requires that a request phrased either way resolves to the same contract on the same day. The lag of 2 matches only the two rolls we hold, and the slice says so (lines 832-836). The mitigations are sound: one constant, a recorded-fixture test, and a re-record task in 232. But `v.0`, the project default, is then unresolvable for the two sessions after any gap. 230 and 231 inherit that behaviour on their default path. Say explicitly in the hand-off to 230 and 231 that a product-level default read can return unresolved sessions, and how each should surface them, so the consumers don't discover it.

### [NOTE] Open interest is evaluated and deferred, as the architecture asked

The architecture says open interest "must be evaluated when roll methods are designed" and that purchased figures must be archive units with manifest provenance. The slice records free evidence (ES `n.0` equals `v.0` on all 11 rolls, GC differs), lists what shipping it would take, keeps `n` unassigned so adding it changes no existing rule, and hands the question to 232. This satisfies the evaluation obligation. No purchase is made.

### [PASS] Layering and dependency direction

`roll_resolve.py` is pure. Reads are isolated in `roll_inputs.py`. The only cross-database edge is the existing tick → production calendar read that `status` and `coverage` already use, and the slice adds no new edge. Nothing imports from another data source. Acquisition is untouched and stays on `ES.FUT` requests, so the rule that roll machinery is a read-time concern holds. Nothing is stored or cached, which honours "a continuous series is a rule."

### [PASS] Explicit contract and rule on every answer, with reproducibility

Every answer names the contract, the rule, the expiration and the basis session. Unit-id sets let a client detect that the inputs changed. This meets the architecture's reproducibility and "names the latest session its inputs cover" requirements. The default is defined once and the grammar is shared by the CLI and the API. Roll-day handling states a default of one contract per session, with no merging across the roll.

### [PASS] Failure modes, bounds and the realtime-paths check

TD9 enumerates the failure modes: unreachable database, read hang, mid-read peer loss, calendar failure, ingest committing between reads, and broken invariants. Each has a handling strategy and an exit code. Unresolved sessions are values with reasons, not guesses, and a missing ledger row raises instead of reading as 0. TD10 measures and caps the cost of a request. The realtime-paths check is present, as the architecture requires for every 220 slice. The architecture states no latency NFR for this path, so the slice's own sub-1 s target is a self-imposed bound.

### Run Digest

- Response length: 7841 chars
- Response is newline-free: no
- Tool calls made: 3
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 42.6 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
