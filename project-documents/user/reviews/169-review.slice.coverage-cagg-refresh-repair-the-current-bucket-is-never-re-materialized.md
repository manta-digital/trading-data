---
docType: review
layer: project
reviewType: slice
slice: coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260813
dateUpdated: 20260813
reviewedSha: d75725f4c155a9ef6f977026c1b2e1e8406d9d3e
dispositionDate: 20260813
dispositionStatus: all-findings-addressed
findings:
  - id: F001
    severity: concern
    category: nfr-restatement
    summary: "Content-edge probe cost at the new width is neither restated nor re-derived, and its architecture-stated timeout degrades to a hard refusal"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:557-565"
  - id: F002
    severity: concern
    category: verification-gap
    summary: "Nothing verifies the repaired refresh policy actually materializes the head — every check runs immediately after a manual full-span refresh"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:656-659"
  - id: F003
    severity: concern
    category: failure-modes
    summary: "Migration 051's ①→③ window makes `data_status` non-existent, contradicting the Rebuild Window's \"nothing crashes\""
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:708-715"
  - id: F004
    severity: concern
    category: operational-cost
    summary: "Refresh offsets are reinstalled \"unchanged\" without re-examining a 750-day start_offset that only existed to satisfy the 365-day floor"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:478"
  - id: F005
    severity: concern
    category: architecture-consistency
    summary: "140-arch's `MAX_COVERAGE_SOURCE_STALENESS` block still asserts the cancellation D3a refutes — the amendment D6a claims complete leaves the architecture self-contradictory"
    location: "project-documents/user/architecture/140-arch.data-quality-operations.md:1225-1228"
  - id: F006
    severity: note
    category: analysis-consistency
    summary: "D2's row-count table mixes one measured actual with computed worst cases, weakening the \"~12×\" figure now normative in the architecture"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:141-151"
  - id: F007
    severity: note
    category: documentation
    summary: "Three cross-references point to an \"Open Questions\" section that does not exist"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:294"
  - id: F008
    severity: note
    category: sequencing
    summary: "The architecture now states 30 days normatively while the slice treats the width as measurement-pending"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:415-420"
  - id: F009
    severity: pass
    category: nfr-restatement
    summary: "NFR correctly identified, restated with a specific target, and defended against a measurement that would have masked a regression"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:557-565"
  - id: F010
    severity: pass
    category: failure-modes
    summary: "Failure modes for the rematerialization I/O path are enumerated concretely with handling, not deferred"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md#The-Rebuild-Window"
  - id: F011
    severity: pass
    category: scope
    summary: "Scope boundaries and dependency direction are correct"
    location: "project-documents/user/slices/169-slice.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized.md:428-442"
---

# Review: slice — slice 169

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Disposition (2026-08-13)

Re-review of `d75725f`, after the first round's F001–F007 were addressed. All
eight findings **accepted and addressed**; no prior finding was re-raised.

| ID | Disposition | Where addressed |
|---|---|---|
| F001 | Accepted — verified | New **D3b**; criterion 17; walkthrough step 1 |
| F002 | Accepted | New walkthrough step **8a**; criterion 18 |
| F003 | Accepted | Rebuild Window split into Windows A and B |
| F004 | Accepted | New **D4a**; criterion 19; migration table |
| F005 | Accepted — verified | 140-arch `MAX_COVERAGE_SOURCE_STALENESS` amendment |
| F006 | Accepted | D2 table normalized to one basis; arch amendment corrected |
| F007 | Accepted | Both references retargeted to issue #14 |
| F008 | Accepted | D6a gains an explicit B1 checklist |

**F001 verified and is the most consequential.**
`CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT = 10s` is live in `constants.py:94`,
sized against a measured 0.37–2.14 s/cagg, and a timeout degrades to
`PROBE_FAILED` — a refusal. The content-edge probe reads `max(last_bucket)`, a
plain aggregate column that is not chunk-excludable, over caggs this slice
grows ~12×. If it crosses 10 s, criteria 6 and 8 fail by a route the design
never considered, and the operator-visible result is **indistinguishable from
the staleness the slice exists to clear**. Now a Task B1 measurement that can
*reject a width*, with raising the timeout explicitly ruled out as the wrong
lever.

**F002 is the sharpest observation in either round.** Every verification step
was satisfiable by the bug: steps 5–7 ran immediately after a *manual*
full-span refresh, and step 8 read `scheduled = true` — the same two green
signals that persisted through all 205 no-op runs. Nothing observed the policy
itself write. Step 8a now waits one tick with no manual refresh and confirms
`MAX(last_bucket)` advanced while raw also advanced.

**F005 verified.** The refuted sentence — "Bucket width is NOT part of the
budget … the structural offset cancels exactly" — was still standing unqualified
in the Constants block, the section the architecture designates as definitive.
D6a had claimed the amendment complete while leaving the architecture
self-contradictory. Amended; D6a's count corrected from three to four.

**F006 accepted as stated.** The 365-day minute cell carried slice 167's
measured actual (~15 k) while every other cell was a worst case — ~8× apart, in
the one table used to justify the width. Normalized to ~129 k on a single
basis, with the mixed-basis trap called out, and the inherited "~12×" claim
corrected in 140-arch.

### [CONCERN] Content-edge probe cost at the new width is neither restated nor re-derived, and its architecture-stated timeout degrades to a hard refusal

140-arch states an NFR on the freshness probe path that this slice directly perturbs: `CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT = 10s`, sized as "a small multiple of measured cost (0.37-2.14 s/cagg on prod)", with the explicit failure behavior "On timeout the check degrades to a refusal (PROBE_FAILED) rather than stalling the reader" (140-arch:1238-1244). The slice never restates this bound or re-derives it at the new width.

That matters here specifically because D3 (:180-185) describes the content-edge check as `max(last_bucket)` on the cagg — a plain aggregate column, distinct from the `max(time_bucket)` the generic check uses per D3a (:228-229), so it is not chunk-excludable — over caggs D2 grows ~12× (daily worst case ~780k → ~9.5M rows), which D4 (:311-313) confirms carry columnstore policies. The slice's own walkthrough sets `statement_timeout = '30s'` for exactly this query (:611-614), three times the production budget, without noting the discrepancy.

Failure scenario: after Task C completes, the enlarged `daily_coverage` pushes `max(last_bucket)` past 10s on prod. Every `data_status` reader through the guarded accessor gets `PROBE_FAILED` → refusal, so `mt data status`, `/api/v1/status`, and `/api/v1/health` report unusable coverage — failing success criteria 6 and 8 by a mechanism the design never considered, and looking identical to the staleness the slice set out to clear. The fix is cheap: measure the probe at candidate widths in Task B1 alongside the view read, and state the resulting bound.

### [CONCERN] Nothing verifies the repaired refresh policy actually materializes the head — every check runs immediately after a manual full-span refresh

The defect this slice exists to repair is precisely "a policy that reports `Success` while doing nothing" (:24-26, and D1's argument against leaving "a lying job in the catalog", :88-93). But the verification sequence never observes the policy itself write. Step 5 materializes full history manually; step 6 checks `MAX(last_bucket)` immediately afterward; step 8 only re-reads `scheduled = true` from the catalog. Criterion 5 and criterion 11 are satisfiable entirely by the manual refresh plus a catalog read — the same two signals that were green throughout the 205 successful no-op runs.

Failure scenario: the new width is chosen, migrations apply, Task C materializes everything, all 16 criteria pass — and the hourly policy is still structurally unable to advance the head (e.g. a subsequent offset or width interaction, or a policy that failed to reinstall correctly in 052). The head silently re-drifts, and the next detection is another prod measurement months later. The missing step is one line: after the rebuild, wait for one policy tick and confirm `MAX(last_bucket)` advanced with no manual `refresh_continuous_aggregate` issued. That is the only observation that distinguishes a working policy from the exact bug being fixed.

### [CONCERN] Migration 051's ①→③ window makes `data_status` non-existent, contradicting the Rebuild Window's "nothing crashes"

The Rebuild Window enumerates reader behavior thoroughly for the *empty-cagg* state and concludes "**This is correct reporting of a true state** (167 D3a: report, don't refuse) and nothing crashes." That is accurate for the post-051 materialization window but not for the DDL window itself: 051 ① drops `data_status` and ③ re-installs it (:474-477), and 051 runs under `requires_autocommit` with "no transactional rollback" (:492-495). Between ① and ③, `data_status` does not exist.

The design recognizes this exact outcome — but only as the consequence of the rejected `CASCADE` spelling ("`mt data status`, `/api/v1/status`, and `/api/v1/health` all fail against a missing relation until someone notices", :496-499). The intended path has the same window, and a failure between ① and ③ makes it indefinite rather than brief. This is a missing-relation exception, not a stale report — a categorically different failure mode from the one the Rebuild Window handles, and the section that would have covered it explicitly says the opposite. Criterion 13 confirms the view exists *after* 051, which detects the state but does not handle it. State the handling: whether the API server is stopped for 051, and what the recovery is if 051 fails between ① and ③.

### [CONCERN] Refresh offsets are reinstalled "unchanged" without re-examining a 750-day start_offset that only existed to satisfy the 365-day floor

The migration plan reinstalls both policies "at unchanged offsets." Per 140-arch:1179-1195, `MINUTE_COVERAGE_REFRESH_START_OFFSET = 750 days` was chosen as ">= 2 buckets (731d floor)" — i.e. it is a value forced by the 365-day width, not an independently motivated lookback. D2 notes narrowing "*relaxes* this — it is the one constraint that gets easier" (:132-136) but then keeps the value.

The consequence is unexamined: the slice's own premise is that both hourly policies "have been successful no-ops since creation" (:25-26). After narrowing they begin doing real work every hour against a 750-day window — ~25 buckets across 12,040 daily symbols and 5,871 minute symbols — on a host where the daemon writes concurrently and where prod query discipline is a standing concern. No criterion bounds the policy's runtime against its 1-hour schedule interval, and no measurement in Task B1 covers it.

Failure scenario: the hourly refresh at the new width exceeds its schedule interval or contends with daemon writes on prod, producing either a perpetually-behind head (re-creating the symptom) or the kind of resource pressure that has previously destabilized this host. At minimum, justify retaining 750 days at a 30-day width, and add the policy's measured runtime to Task B1's record.

### [CONCERN] 140-arch's `MAX_COVERAGE_SOURCE_STALENESS` block still asserts the cancellation D3a refutes — the amendment D6a claims complete leaves the architecture self-contradictory

D3a quotes 140-arch's justification verbatim and demonstrates it is false for the coverage caggs: *"The raw edge is bucketed to the cagg's own grid before comparison, so the structural offset cancels exactly"* — a cancellation that "depends on the head bucket being materialized" (:230-239). D6a then records three amendments as **Done 2026-08-13** (:415-420): the `COVERAGE_BUCKET_INTERVAL` block, the slice-167 bounded-consistency paragraph, and the refresh-policy block. The `MAX_COVERAGE_SOURCE_STALENESS` block — the one that carries the refuted sentence, and the one a reader working on freshness will actually open — is not among them, and it still reads unqualified.

The per-view budget change is described in the prose amendment at 140-arch:129-135, so the information exists; but a reader arriving at the Constants section (the section the architecture designates as the definitive list, :1094-1096) reads a flat "Bucket width is NOT part of the budget" and would size a future change wrong. This is precisely the failure D6a itself names: "a stale architecture is the same failure as a lying job catalog" (:422-424). One sentence in the constant's block, using the same amendment convention, closes it.

### [NOTE] D2's row-count table mixes one measured actual with computed worst cases, weakening the "~12×" figure now normative in the architecture

D2 states the table uses slice 170's measured spans as worst cases assuming every symbol spans full history. Applied consistently, minute at 365 days gives ~22 years × 5,871 symbols ≈ 129k rows; the table's first row instead carries **~15 k**, the *actual* figure inherited from slice 167's rationale. Every other cell in that column follows the worst-case formula (30 d: 22 × 365.25/30 × 5,871 ≈ 1.6M ✓). So the baseline row is roughly 8× lower than the same method would produce, making the width comparison apples-to-oranges in the one place it is used to justify the choice.

This propagates: the arch amendment now states "The row count rises ~12x (the ~15k-row figure this block previously cited no longer holds)" (140-arch:1159-1162), where ~12× is the width ratio applied to an actual, while D2's table is in worst cases. Task B1 measures actual rows anyway, so the correction is to normalize the table's first row to the same basis and let B1 supply the real number.

### [NOTE] Three cross-references point to an "Open Questions" section that does not exist

D3 defers the `bars_summary` head-probe follow-on "out of scope here; noted in Open Questions", and D7 repeats the pointer (:433-434). The document has no Open Questions section — it was resolved into "PM Decisions" (:785), where the item does appear as PM item 1's follow-on. Relatedly, 140-arch:141 records that follow-on as **GitHub issue #14**, while the slice describes it as "Follow-on, not scheduled" (:799); the architecture is the more current record. Retarget the three references and cite issue #14.

### [NOTE] The architecture now states 30 days normatively while the slice treats the width as measurement-pending

D6a amended 140-arch with 30 days before Task B1 selects the width, and explicitly assigns B1 the duty to update it and the derived thresholds if measurement chooses differently. This is acknowledged and bounded, so it isn't an error — but between now and B1's completion the architecture states a value the slice calls "the design's working assumption," which is the transient version of the staleness D6a exists to prevent. Worth carrying as an explicit B1 checklist item rather than only as prose, since the amendment reads as settled to anyone who doesn't also read this slice.

### [PASS] NFR correctly identified, restated with a specific target, and defended against a measurement that would have masked a regression

140-arch states "View latency stays sub-second at full-universe scope" (140-arch:204-207), reinforced by the slice-167 amendment recording the 7.8 s starting point. Criterion 12 restates it with the exact target and measurement form (`SELECT count(*) FROM data_status`), and explicitly demotes the `bars_summary` `GROUP BY` to a diagnostic with the reasoning why — "A fast CTE with a regressed view read would pass a CTE-only criterion while breaking the actual architectural NFR." Walkthrough step 1 makes width selection subordinate to that measurement rather than to taste. This is the correct handling of an inherited NFR under a change that trades against it.

### [PASS] Failure modes for the rematerialization I/O path are enumerated concretely with handling, not deferred

The Rebuild Window addresses hang (statement timeout sized to one sub-window, with the reasoning that an unbounded session removes the only server-side bound on a call whose memory is outside `work_mem`), client disconnect mid-operation (`pg_cancel_backend` before retry, because disconnect does not cancel the backend), partial materialization (content-based detection against the history floor, not catalog presence), and recovery semantics (re-run, not rollback, with sub-windowing limiting re-work). The 163 concurrent-refresh corruption lesson is applied as catalog-resolved job pausing with an explicit prohibition on hardcoded job IDs, and the `minute_4hour_ohlcv`-must-stay-scheduled constraint is carried with its incident reference. No "TBD" in this section.

### [PASS] Scope boundaries and dependency direction are correct

D7 excludes head-refresh machinery, the `bars_summary` reshape, `mt data caggs repair` extension, the unrelated minute-rollup parity shortfall, and `approximate_row_count` — each with a reason, and each consistent with 140-arch's "Out of scope" discipline. D1 rejects the custom-job alternative on operator-visibility grounds that mirror the architecture's own transparency Purpose. The D3a mechanism (per-view budget resolved alongside `COVERAGE_SOURCE_TABLE`, with fallback for the seven pre-167 caggs) keeps the generic freshness helper data-driven rather than special-cased, preserving 168's layer boundary, and the slice-plan entry (#29) plus the 170 entry's ordering note support the added 170 dependency.
