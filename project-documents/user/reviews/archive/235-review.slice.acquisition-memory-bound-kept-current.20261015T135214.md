---
docType: review
layer: project
reviewType: slice
slice: acquisition-memory-bound-kept-current
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261015
dateUpdated: 20261015
reviewedSha: ea30915b8dceb8989c45bb80113294f365c671e2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 7
turns: 8
promptTokens: 294274
cachedTokens: 191655
cacheWriteTokens: 102611
completionTokens: 4900
durationSeconds: 46.4
costUsd: 0.479367
costBasis: provider
runId: run-20261015-p4-227e5f01
squadronVersion: 0.22.0
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Boundaries and dependency direction match the architecture"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#Architecture"
  - id: F002
    severity: note
    category: plan-alignment
    summary: "Deliberate deviations from the plan entry are justified"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#TD4, #TD5"
  - id: F003
    severity: concern
    category: architecture-alignment
    summary: "Manual-only tick cadence makes the health FAIL recurring, and the question is left open"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#TD4, #Special Considerations"
  - id: F004
    severity: concern
    category: error-handling
    summary: "Failure modes of the new I/O paths are only partly enumerated"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#TD4, #TD5, #Technical Decisions"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Design is heavy for a Low-risk, 2/5-effort slice"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#Technical Scope"
  - id: F006
    severity: concern
    category: documentation
    summary: "The 220 architecture's cross-source arbitration entry becomes stale and is not listed for update"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#Architecture statements superseded"
  - id: F007
    severity: note
    category: risk
    summary: "Known limitation: the bound cannot learn from OOM-killed runs"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#Special Considerations"
---

# Review: slice — slice 235

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Boundaries and dependency direction match the architecture

The per-run peak goes on `pass_runs` through a minute-track migration (061). That follows the 220 principle "Operational state has one home", which keeps run accounting in the production database's minute track for every source, including tick. Recording stays in the shared recorder, and the recorder contract (best-effort, never aborts a pass) is kept. Health, the installer and the script all depend on one rule module, `memory_bound.py`. No source imports another source, and the tick database is not touched. Dependencies [233, 234] match the plan entry. The API note (peak on `last_run`, bound verdict not served) matches the plan.

### [NOTE] Deliberate deviations from the plan entry are justified

The plan says health compares against "the live slice cgroup's `memory.max`" and that the install "writes it to the slice unit". The design instead reads `systemctl show` and writes a drop-in. TD4 explains why: an idle slice has no cgroup, so an hourly check would usually find nothing. TD5 explains why the shipped unit stays identical to the repo. Both reasons hold and both intents are met. I'd record the deviations in the plan entry when the slice closes.

### [CONCERN] Manual-only tick cadence makes the health FAIL recurring, and the question is left open

The 220 architecture defers cadence, so tick runs only on demand. It also says "no daily history pull is assumed". An idle month is therefore the normal state for `mt-tick-pass.service`. TD4 FAILs the check whenever a member has no peak in the 30-day window. That puts `mt-health.service` in a failed state hourly after every idle month. The FAIL also exits `mt data health` with code 1, so a real regression in any other check is hard to tell apart from this one. The design follows the plan entry. It still leaves a PM decision unresolved in a design that should settle it, with the alternative (use the newest peak of any age, FAIL only when never measured) described but not chosen. Resolve it before tasking. I recommend the alternative. It keeps the "never measured" failure that keeps #34 visible and drops the false recurring alarm. If the plan's behavior is kept, say so as a decision, not an open question.

### [CONCERN] Failure modes of the new I/O paths are only partly enumerated

The design handles the cgroup read failure, a missing peak, a script exit of 2 and a script exit other than 0 or 2. The following are not covered:
- **`systemctl show` failure in health.** TD4's table has no row for a non-zero exit, a missing binary or a hang. `loaded_memory_max` parses output, but nothing says whether the check FAILs, errors, or hides behind the table's first matching row. It needs a timeout and an explicit verdict.
- **Health on a database without migration 061.** The script's "missing column" case is mapped to exit 2. The health row's behavior is not stated, and the window between deploying code and migrating is exactly when it would run.
- **`systemd-run --wait` hanging.** A DB connect that stalls blocks the installer indefinitely. The recorder has `PASS_RUN_DB_CONNECT_TIMEOUT_SECONDS`, but no timeout is stated for the derivation. The installer should bound it and treat a timeout as a failure.
- **Drop-in write failure.** Examples are a full disk or a failed rename. The "anything else" branch calls `die` before the reload, which probably covers it, but the design should say so.

### [CONCERN] Design is heavy for a Low-risk, 2/5-effort slice

The plan entry sizes this at Risk Low, Effort 2/5. The design has ten included items, a new migration, a `systemd-run`-as-service-user installer library with its own shell unit test, a cutover script, an API field and a rewritten script. Candidates to cut or defer:
- **The `--dropin` flag and separate render path.** This could be a single code path in the script that prints the value, with the installer formatting the drop-in. Only the one `MemoryMax=<N>G` line is validated before writing.
- **The CHECK constraint with its drop/re-add DDL.** Two nullable columns written by one code path rarely need a cross-column constraint. If kept, it needs a stated reason.
- **The "Architecture statements superseded" section and most of the verification walkthrough.** Both restate content from 233 and the plan and can be shortened.
- **The all-kinds recording justification.** `mt-health` and `mt-accounting` rows get a peak "for uniformity". That is an acceptable cost, but it adds nothing the slice requires.
The cutover script is justified, because a pending migration blocks `upgrade_production.py`. Nothing the plan assigns to this slice is dropped.

### [CONCERN] The 220 architecture's cross-source arbitration entry becomes stale and is not listed for update

The 220 architecture's 2026-10-14 entry for slice 233 says `MemoryMax` is "derived from journal peaks by `scripts/acquisition_memory_bound.py`". This slice replaces that with `pass_runs` peaks plus a drop-in. The design lists the superseded 233 design statements but not the architecture entry, and Technical Scope item 10 lists no architecture update. Add a dated entry to the architecture document, as the 233 and 234 slices did.

### [NOTE] Known limitation: the bound cannot learn from OOM-killed runs

A pass killed by the slice's OOM killer never reaches close, so its peak is never recorded. The design states this openly and keeps the kill visible through the unit's `Result=oom-kill`. Leaving it out of scope is reasonable for this slice.

### Run Digest

- Response length: 6784 chars
- Response is newline-free: no
- Tool calls made: 7
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 8
- Tokens — prompt / cached / cache write / completion / reasoning: 294274 / 191655 / 102611 / 4900 / not computed
- Duration: 46.4 s
- Cost: $0.48 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
