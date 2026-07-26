---
docType: review
layer: project
reviewType: tasks
slice: cagg-freshness-assertion-for-derived-data-readers
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/168-tasks.cagg-freshness-assertion-for-derived-data-readers.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260726
dateUpdated: 20260726
findings:
  - id: F001
    severity: concern
    category: sequencing
    summary: "Commit checkpoints are batched at the end instead of distributed per task"
    location: project-documents/user/tasks/168-tasks.cagg-freshness-assertion-for-derived-data-readers.md:324-328
  - id: F002
    severity: concern
    category: test-coverage
    summary: "`_cagg_max`/`_raw_max` statement_timeout discipline has no dedicated test"
    location: project-documents/user/tasks/168-tasks.cagg-freshness-assertion-for-derived-data-readers.md:145-154
  - id: F003
    severity: note
    category: nfr-alignment
    summary: "Full-universe NFR amortization (criterion 8's last clause) is correctly left to slice 167, but the task file doesn't say so"
    location: project-documents/user/tasks/168-tasks.cagg-freshness-assertion-for-derived-data-readers.md:232-239
  - id: F004
    severity: pass
    category: uncategorized
    summary: "All eight success criteria trace to a task"
    location: project-documents/user/tasks/168-tasks.cagg-freshness-assertion-for-derived-data-readers.md:267-301
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Sequencing and scope are sound; no scope creep"
    location: project-documents/user/tasks/168-tasks.cagg-freshness-assertion-for-derived-data-readers.md:70-329
resolution:
  status: addressed
  dateResolved: 20260726
  notes: "F001-F003 all addressed in the task file. No finding waived. See Resolution section."
---

# Review: tasks — slice 168

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] Commit checkpoints are batched at the end instead of distributed per task

The only checklist item that performs a commit is Task 9.4, at the very end of the sequence. Tasks 1 through 8 (constants, verdict type, catalog read, edge probes, core evaluation, TTL cache, consumer wiring, integration tests) have no interstitial commit checkpoints. This is a direct regression from this project's own established convention: slice 162's task file has six distinct `- [x] **Commit**: ...` checklist items interleaved after individual sub-tasks (e.g. lines 70, 128, 149, 178, 188, 207), and slice 163's task file has explicit "Commit checkpoint" tasks after each lettered phase (A6, B5, C7). CLAUDE.md also states "Git add and commit from project root at least once per task." As written, an implementer following this file literally could accumulate all nine tasks' worth of changes before the first commit, which is exactly the batching this project's own precedent and guidelines avoid. Add a `Commit:` checklist line (with a suggested semantic message) after Task 2, Task 4/5, Task 6, Task 7, and Task 8, in addition to the closing 9.4.

### [CONCERN] `_cagg_max`/`_raw_max` statement_timeout discipline has no dedicated test

Task 4.1's own success bullet requires "timeout is set on every path," and the project's standing prod-query-discipline lesson treats missing `statement_timeout` on probe queries as the root cause class of a real prior incident (2026-07-20 crash from an unbounded expression aggregate over a compressed hypertable — see memory `feedback_prod_query_discipline`). No task in the breakdown verifies this directly:
- Task 5.3 tests the *exception-handling* path (an injected `psycopg.Error` → `PROBE_FAILED`), which exercises the `except` branch, not whether a timeout is actually configured on the cursor/connection before the query runs.
- Task 8's integration tests (8.1–8.4) exercise the probes against a real scratch DB but none deliberately induce a slow/hanging query to confirm the configured timeout actually fires and converts to a trip rather than hanging the caller.

Since D3 explicitly calls out "a hung catalog or edge query degrades to a refusal rather than stalling the caller" as a required property, this should have either a unit test asserting the timeout parameter is passed on every code path in `_cagg_max`/`_raw_max`, or an integration test that induces slowness and confirms bounded behavior. Consider folding this into 4.1's own test coverage (currently absent — 4.1 is the only implementation task among 2–7 with no direct paired unit-test subtask; 4.3 tests only `_resolve_threshold` from 4.2).

### [NOTE] Full-universe NFR amortization (criterion 8's last clause) is correctly left to slice 167, but the task file doesn't say so

Success criterion 8 ends with "Repeated calls across a full-universe read amortize to well under the sub-second consumer NFR." Task 6.2's four unit tests cover the cache mechanics in isolation (zero probes when warm, stale-cached-still-refuses, TTL expiry re-probes, distinct-view isolation) but nothing in this task file exercises amortization at full-universe scale, and no `test/load/` task exists here. This is very likely correct scoping — 168 doesn't wire `bars_summary` (167's job), and 167's own slice design already plans the CI-gated load test for this exact NFR (167-slice D5, success criterion 6: "A load test asserts full-universe read latency < 1 s and is CI-gated"). This matches the precedent set in slice 166 (task D2), which explicitly recorded *why* no `test/load/` task was added rather than silently omitting it. 168's task file has no equivalent one-line note. Not a blocking gap given 167 already owns it, but adding a short note (as 166 did) would make the deferral an explicit decision rather than an implicit one for future readers of this task file.

### [PASS] All eight success criteria trace to a task

Criteria 1 (induced staleness) → 8.2; criterion 2 (four D1 signals in isolation) → 5.3; criterion 3 (270-day regression pinned) → 4.3; criterion 4 (healthy pass, no behavior change) → 5.3 + 7.2 + 8.4; criterion 5 (probe overhead) → 8.4; criterion 6 (granularity-agnostic) → 8.3; criterion 7 (163 incident shape reproduced) → 8.2; criterion 8 (TTL cache both directions) → 6.2. No orphaned criteria found.

### [PASS] Sequencing and scope are sound; no scope creep

Task order (constants → verdict type → catalog read → edge probes/threshold → core evaluation → TTL cache wrapping the core → consumer wiring using the cached wrapper → integration tests → close-out) respects real dependencies with no circularity. Every task traces to the slice's declared scope (`assert_cagg_fresh` + cache, the two constants, wiring `build_minute_coverage_index`, induced-staleness tests); explicitly out-of-scope items (auto-remediation, maintenance-path guard, 167's `bars_summary` wiring) are correctly absent. Test-with-implementation pairing is followed everywhere except 4.1 (noted above). Task sizes (effort 1–3) are appropriately grained for independent completion by a junior AI, each with a concrete, checkable success bullet.

## Resolution (20260726)

All three actionable findings addressed in the task file. None waived.

**F001 — commit checkpoints.** Correct call against this project's own
precedent. Six `- [ ] **Commit**: <semantic message>` checklist lines added,
following slice 162's inline form (162 lines 70/128/149/178/188/207) rather than
163's separate lettered-phase tasks: after Task 2 (verdict type + constants),
Task 4 (catalog read + probes + threshold), Task 5 (signal evaluation), Task 6
(TTL cache), Task 7 (consumer wiring), and Task 8 (integration tests), in
addition to the existing 9.4. Each carries a concrete semantic message so the
implementer does not have to invent one. A Notes line records the convention
explicitly.

**F002 — probe timeout discipline untested.** The sharpest finding: Task 4.1
asserted "timeout is set on every path" as a success bullet with nothing
verifying it, and 4.1 was indeed the only implementation task among 2–7 without
a paired test. Closed from both directions:
- **New 4.1a** (unit) — a recording fake captures statement order and asserts
  `statement_timeout` is set *before* the `max()` query on every path including
  early returns; asserts the value comes from the constant, not an inline
  literal; asserts table resolution goes through `GRANULARITY_SOURCE` rather
  than interpolating caller input. Fails if any `SET statement_timeout` is
  removed.
- **New 8.3a** (integration) — deliberately induces a probe that exceeds its
  timeout against the scratch DB and asserts a `PROBE_FAILED` stale verdict
  returns within bounded wall-clock time, with no hung caller and no orphaned
  backend.

Together these separate the two properties Task 5.3 conflated: 5.3 proves the
`except` branch handles an error once raised, 4.1a proves the bound that raises
it is configured, and 8.3a proves it fires in a live database — which is D3's
actual stated requirement ("a hung catalog or edge query degrades to a refusal
rather than stalling the caller").

**F003 — load-test deferral unstated.** New task 8.5 records the decision
explicitly, following the slice-166 D2 precedent. The reasoning now in the file:
criterion 8's amortization clause describes a call path 168 does not create —
its only consumer calls the helper once per daemon cycle, where the uncached
~1 s is already negligible against a ~23 s build. Full-universe repeated calls
begin only when 167 wires `bars_summary`, and 167's D5 / criterion 6 already
specifies the CI-gated < 1 s load test. What 168 owns is the cache mechanism
enabling that amortization, covered by 6.2.

Task file grew from 344 to 411 lines — still within the ~450 guideline, no split
required.
