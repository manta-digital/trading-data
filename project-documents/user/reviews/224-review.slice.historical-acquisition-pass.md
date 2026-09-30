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
sourceDocument: project-documents/user/slices/224-slice.historical-acquisition-pass.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20260929
dateUpdated: 20260929
reviewedSha: 3e8e272e6b3d6a5f50aacd6cf8e806519170d6d9
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 4
squadronVersion: 0.16.0
findings:
  - id: F001
    severity: concern
    category: scope
    summary: "The document mixes two slices, so 224's own scope can't be isolated"
    location: "project-documents/user/slices/224-slice.historical-acquisition-pass.md:16-64"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Submit-attempt bookkeeping is underspecified, and money safety depends on it"
    location: "project-documents/user/slices/224-slice.historical-acquisition-pass.md#Technical Decision 9: a submit that may have been charged is reconciled, never re-bought blind"
  - id: F003
    severity: concern
    category: architecture
    summary: "\"Rebuild the manifest from the archive\" does not rebuild the spend record the guard reads"
    location: "project-documents/user/slices/224-slice.historical-acquisition-pass.md:934-946, 201-216"
  - id: F004
    severity: concern
    category: nfr
    summary: "The await budget doesn't bound download time, and the 30-day retention target isn't restated"
    location: "project-documents/user/slices/224-slice.historical-acquisition-pass.md:856-872, 1116"
  - id: F005
    severity: note
    category: architecture
    summary: "The interim absence of `pass_runs` accounting isn't in the supersession list"
    location: "project-documents/user/slices/224-slice.historical-acquisition-pass.md:145-149, 1599-1626"
  - id: F006
    severity: note
    category: cost-control
    summary: "The spend guard counts definitively refused submits for 30 days"
    location: "project-documents/user/slices/224-slice.historical-acquisition-pass.md:711-714"
  - id: F007
    severity: pass
    category: architecture
    summary: "Boundaries and dependency direction match the architecture"
    location: "project-documents/user/slices/224-slice.historical-acquisition-pass.md#Component Structure"
  - id: F008
    severity: pass
    category: cost-control
    summary: "Money-safety ordering and the guard align with the cost principle"
    location: "project-documents/user/slices/224-slice.historical-acquisition-pass.md#Technical Decision 7: the spend guard is all-or-nothing, both ceilings are required, and the 30-day sum comes from the manifest"
  - id: F009
    severity: pass
    category: error-handling
    summary: "The failure-mode table covers the new I/O paths"
    location: "project-documents/user/slices/224-slice.historical-acquisition-pass.md:1110-1123"
  - id: F010
    severity: pass
    category: architecture
    summary: "Departures from the architecture are bounded and recorded"
    location: "project-documents/user/slices/224-slice.historical-acquisition-pass.md#Technical Decision 5: definitions mirror the tier request, and 224 projects them"
---

# Review: slice — slice 224

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] The document mixes two slices, so 224's own scope can't be isolated

The document is titled 224 but is "the design of record for both" 223 and 224. Only the Slice Split table (lines 58-61) says which parts belong to 224.
- **Scope lists are unfiltered.** The Overview, Technical Scope "Included" (items 1-11), Component Structure, Success Criteria and Verification Walkthrough all cover both slices.
- **223 items are already merged.** These include `tick_006`, settings, adopt, reset, backup enrolment, and walkthrough steps 1-4, 8 and 9. Some carry recorded "Result (223, 2026-09-29)" text.
- **The frontmatter looks like a fresh 224 doc.** It says `status: not_started` and `review: none`, and lists 223 as a dependency.
- **Duplication risk.** A task breakdown built from "Included" will duplicate shipped work, or leave the reviewer unsure which criteria 224 must newly satisfy.

Restructure the doc, or add a 224-only scope and success-criteria subset, so the pass, planner, guard, definitions and delivery work is separable from what is done.

### [CONCERN] Submit-attempt bookkeeping is underspecified, and money safety depends on it

The `TICK_SUBMIT_RESOLVE_AGE` and never-auto-resubmit rules are keyed on `last_attempt_at`. But the pre-submit transaction (lines 811-814) inserts the request and its units and does not say that it sets `last_attempt_at`, `attempt_count` or a `fetch_status`. Three cases are unspecified:
- **Crash after the pre-submit insert, before `submit_batch` returns.** The units have no attempt time. Whether the 1-hour age check treats NULL as "young forever" or as "old" decides whether the row is ever exhausted or silently re-submitted.
- **Crash after the provider accepts the job, before the second transaction.** This is the same NULL-timestamp state, so the reconcile match path must cope with it.
- **The purchase phase's skip rule.** It skips rows with no job id, but the row's `fetch_status` in this state is unspecified.

State that the pre-submit transaction stamps `last_attempt_at = now`, increments `attempt_count`, and sets a defined status. Add a test for a crash at each of the two points.

### [CONCERN] "Rebuild the manifest from the archive" does not rebuild the spend record the guard reads

The architecture says the 30-day cap is summed from the manifest, and that the archive can rebuild the database projection. The slice extends that to the manifest and uses it to justify having no production tick cluster. Re-adoption only recovers jobs that have files in the archive. It loses three things:
- **Paid jobs with no files.** Expired jobs (whose repurchases are also in the manifest), unresolved submits, and in-flight jobs not yet downloaded are all missing from a rebuilt manifest. Their spend then drops out of the trailing sum, so the guard undercounts.
- **Provenance.** Re-adopted pass purchases become `is_adopted = TRUE`. `estimated_cost`, `download_deadline`, `reopened_at` and the repurchase and supersession links are also gone. Walkthrough step 10 checks only job id, cost and `committed_at`.
- **The interim exposure.** With no durable tick cluster in the plan, a scratch or lost database resets the 30-day accounting. Only the provider-side limit is behind it.

Either narrow the claim to "file-bearing jobs are recoverable", or add a reconcile step that lists the account's recent jobs and adopts any the manifest lacks. Also assign cluster provisioning to a named slice, as the doc already recommends (225 or 226).

### [CONCERN] The await budget doesn't bound download time, and the 30-day retention target isn't restated

The architecture ties the delivery path to two figures. Retention is 30 days after the job finishes, and the manual-regime wait budget is "minutes, not hours". The slice restates the budget (`TICK_WAIT_BUDGET_SECONDS` = 1,800 s, `TICK_POLL_INTERVAL_SECONDS` = 15 s, and the measured 5-84 s job times). It never states the retention window as a target or the 8-hour lag as the edge-staleness expectation. Two gaps follow:
- `download_batch` is bounded only per file (`TICK_DOWNLOAD_TIMEOUT_SECONDS`). A job with many files, each near the timeout, can run far past the wait budget inside a single `in_flight.advance()` call. The doc doesn't say whether that overrun is acceptable, or whether the budget is checked between jobs or files.
- The same call sits under the advisory lock, so a long download keeps every other tick writer out.

State the total-time bound for `advance()`, or say explicitly that the budget applies to polling only. Restate the 30-day deadline target next to the deadline-ordering rule.

### [NOTE] The interim absence of `pass_runs` accounting isn't in the supersession list

The architecture says the tick pass records its run through the shared pass-run recorder. Its Cadence section also says a manual-only tick pass needs an explicit "no timer" answer in `schedule_for`, not an omission. The slice defers `PassKind.TICK` and `schedule_for` entirely to 233, so a manual run writes no `pass_runs` row. That is consistent with "cadence deferred", and the Excluded section says so. It is not among the six "supersedes" items, though. Add it so the architecture's Revision Log stays in agreement with the design.

### [NOTE] The spend guard counts definitively refused submits for 30 days

Trailing spend includes every `tick_request` row, including 4xx-refused requests known to have charged nothing. That is conservative and deliberate, and it errs the safe way. But a refused row can keep the plan over the 30-day cap with no way to release it short of raising the cap. Consider excluding rows exhausted by a definite refusal, since the reason is recorded, or state that the cap must be raised in that case.

### [PASS] Boundaries and dependency direction match the architecture

- **No source-to-source import.** The pass contract is a copy, and the parity test that imports both packages lives in tests, not `src`. This follows the architecture's "third source" principle, including the per-slice diff as a named task.
- **The calendar edge is tick → production only.** It is placed in planning and adoption. Reconcile, download, verify and definitions need no calendar, so a production outage never strands a paid delivery. This matches the architecture's coupling paragraph and Revision Log.
- **Provider access.** Only the adapter and DBN reader touch `databento`. `submit_batch` is the sole paid call, and nothing calls `fetch_range` (batch-only purchases).

### [PASS] Money-safety ordering and the guard align with the cost principle

- **Estimate before submit, in the manifest.** The row is written at *requested*, carrying the estimate, before `submit_batch`.
- **Deadline-ordered downloads.** Downloads run earliest-deadline-first, before any new spend.
- **Two ceilings, no defaults.** Both ceilings are required and neither has a default. Actual cost is used where known, else the estimate.
- **Misconfiguration is caught.** Preflight refuses unknown `MT_TICK_*` keys, which closes the misspelt-ceiling failure.
- **Unknown submit outcomes are not auto-rebought.** An unknown submit outcome is never re-submitted automatically, only after `reset`.

### [PASS] The failure-mode table covers the new I/O paths

Each new path names its cost, bound, outcome and residue: provider calls, submit, download, verify, await, the tick database, the calendar, the adoption copy, volume-full, and the lock. Archive-volume exhaustion is checked before any submit. Peer disconnect and timeout mid-download rest on `.partial` resume. None is left as TBD. The gaps are the two noted above, in the crash-window bookkeeping and the total await bound.

### [PASS] Departures from the architecture are bounded and recorded

Four departures are stated, and the architecture's 2026-09-28 Revision Log entry records them (spreads only through explicit configuration; the missing-day rule is documented but not in that entry):
- Definition projection lives in the acquisition pass. It is restricted to the `DEFINITION` schema and enforced by a unit test, and tier data and `statistics` stay in 225's ingest.
- Spreads are included in ES by explicit configuration, with the 2.35% cost stated and 226 re-confirming.
- `missing` days are recorded in `tick_day_condition` and not bought, instead of creating a hole unit.
- `PROVIDER_HOLE` marks a unit only when a completed job delivered no file for a session day.

### Run Digest

- Response length: 10396 chars
- Response is newline-free: no
- Tool calls made: 4
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
