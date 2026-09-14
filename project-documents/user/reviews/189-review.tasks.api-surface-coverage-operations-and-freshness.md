---
docType: review
layer: project
reviewType: tasks
slice: api-surface-coverage-operations-and-freshness
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/189-tasks.api-surface-coverage-operations-and-freshness.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260914
dateUpdated: 20260914
reviewedSha: dcc868da6c680078a814c5df94a3ec55b820860b
findings:
  - id: F001
    severity: concern
    category: test-coverage
    summary: "SC2 (\"no second computation of derivation\") has no task that produces verifiable evidence"
    location: "project-documents/user/tasks/189-tasks.api-surface-coverage-operations-and-freshness.md:339-352"
  - id: F002
    severity: concern
    category: test-coverage
    summary: "SC3's literal \"byte-identical responses\" claim is verified only at the OpenAPI-schema level, not against live request output"
    location: "project-documents/user/tasks/189-tasks.api-surface-coverage-operations-and-freshness.md:328-337"
  - id: F003
    severity: pass
    category: sequencing
    summary: "Commit checkpoints are distributed, not batched at the end"
    location: "project-documents/user/tasks/189-tasks.api-surface-coverage-operations-and-freshness.md:97,157,223,253,301,366"
  - id: F004
    severity: pass
    category: sequencing
    summary: "Test-with-implementation pattern is followed throughout"
    location: "project-documents/user/tasks/189-tasks.api-surface-coverage-operations-and-freshness.md"
  - id: F005
    severity: pass
    category: test-coverage
    summary: "Load-tier NFR coverage and CI-gating question"
    location: ".github/workflows/ci.yml:1-26"
---

# Review: tasks — slice 189

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] SC2 ("no second computation of derivation") has no task that produces verifiable evidence

SC2 requires: "`grep` finds no second computation of running/last-run/next-firing in `api_server/`." No task runs this grep, and no automated test asserts it structurally (e.g. that `routes/operations.py` contains no derivation logic). The only coverage is prose admonition repeated across the file ("if a task seems to need a second computation... stop and raise it," lines 40-42) plus Task 3.1's loose success bullet "the module contains no SQL and no derivation" (line 182), which is not tied to SC2 explicitly and isn't a checkable assertion. Task 5.4 promises "every SC1–SC12 has named evidence" from the walkthrough (line 351), but the design's own Verification Walkthrough (steps 1-11) has no step that produces SC2 evidence — so Task 5.4 as written cannot fulfill its own success condition without an ad hoc addition. Add an explicit grep-based check (either a repo-hygiene test or an explicit walkthrough/task step) so SC2 evidence actually exists.

### [CONCERN] SC3's literal "byte-identical responses" claim is verified only at the OpenAPI-schema level, not against live request output

SC3 states: "`/api/v1/status` and `/api/v1/health` responses are byte-identical to their pre-slice output **for the same request**" (slice design line 449-450) — a claim about actual HTTP response bodies. Task 5.3, the only task tied to SC3, instead asserts "every pre-existing path is byte-identical to the previous committed artifact" (line 334) — i.e. the `openapi.json` schema/path listing, not a live-request diff of `/api/v1/status` or `/api/v1/health`. No task in Section 3 or 5 calls either route before/after and diffs the actual response bytes. This mirrors 188's SC1 wording ("schema entries are byte-identical" — `188-slice...md:599`), which was schema-scoped by design, but 189's SC3 is phrased more strongly ("responses... for the same request"), and the task breakdown silently downgrades it to the weaker schema-level check without calling that out. Given no code in this slice touches `status.py`/`health.py`, runtime risk is low, but the stated success criterion is not actually being tested as written — either the task should add a live response-body diff, or the design's SC3 wording should be reconciled with the schema-level check being the intended verification.

### [PASS] Commit checkpoints are distributed, not batched at the end

Six commit checkpoints are spread across all five sections (1.3, 2.4, 3.4, 3.5, 4.3, 5.5), consistent with the "commit at each section checkpoint" convention and with distributing commits throughout rather than batching them at the close of the slice.

### [PASS] Test-with-implementation pattern is followed throughout

Each implementation block is immediately followed by its test task: 1.2 (split) → 1.3 (test split); 2.1-2.3 (models) → 2.4 (model unit tests); 3.1-3.3 (routes) → 3.4 (route unit tests) → 3.5 (integration tests); 4.1-4.2 (load bounds) → 4.3 (prove bounds fail correctly); 5.1-5.2 (docs/artifact) → 5.3 (pin schema properties).

### [PASS] Load-tier NFR coverage and CI-gating question

Both NFRs restated by the slice design (D8's latency bound and contention bound, SC10/SC12) have dedicated `test/load/` tasks (4.1, 4.2). No task wires these into CI, but this repo's only CI workflow (`ci.yml`) runs solely on tag push for PyPI publishing — there is no test-running CI pipeline at all, for any tier, in this project. Absence of CI gating here is consistent with existing project convention (tiers are run manually per Task 5.5 and the "Tiers... run separately" note), not a gap specific to this slice.
