---
docType: review
layer: project
reviewType: slice
slice: cross-source-acquisition-arbitration
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261014
dateUpdated: 20261014
reviewedSha: e1de6fdece9a18d73f71e2eae2d3141f245a5ca7
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 8
durationSeconds: 41.6
runId: run-20261014-p4-9d59dc12
squadronVersion: 0.21.7
findings:
  - id: F001
    severity: concern
    category: scope
    summary: "Plan-assigned CPUWeight/IOWeight and the post-setting re-run are dropped, and the PM has not confirmed it"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md#Technical Decision 1: only `MemoryMax`; no `CPUWeight` or `IOWeight`"
  - id: F002
    severity: concern
    category: consistency
    summary: "Success Criterion 4 claims the bound is in force, but nothing is installed"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md#Functional Requirements (item 4); Technical Decision 7"
  - id: F003
    severity: concern
    category: proportionality
    summary: "The cutover script is built now for an install that will not run in this slice"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md#Technical Scope (Included item 5); Technical Decision 7"
  - id: F004
    severity: concern
    category: consistency
    summary: "The component table contradicts Technical Decision 7 on a contention re-run"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md:196"
  - id: F005
    severity: note
    category: architecture-alignment
    summary: "Scratch tick server deviates from 226's harness, and the design discloses it"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md#Technical Decision 2"
  - id: F006
    severity: note
    category: error-handling
    summary: "Failure modes are enumerated for each new I/O path"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md#Technical Decision 3 (Guards); Technical Decision 7"
  - id: F007
    severity: note
    category: dependencies
    summary: "Dependency direction and the 234 interface are consistent with the architecture"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md#Integration Points"
---

# Review: slice — slice 233

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Plan-assigned CPUWeight/IOWeight and the post-setting re-run are dropped, and the PM has not confirmed it

The architecture ("Cross-source arbitration") and slice plan entry 14 assign this slice three things:
- setting `IOWeight`, `CPUWeight` and `MemoryMax` on `manta-acquisition.slice`;
- choosing among three mechanisms;
- repeating the measurement "to show the chosen setting works".

The design sets only `MemoryMax`. It replaces the re-run with a verify-only check at install. The technical reasoning is sound: the slice has no sibling cgroups, `io.cost` is inactive, and the NVMe schedulers are `none`. But the design says itself (Overview, TD1, TD6) that the PM has not confirmed the change. Dropping plan scope is a CONCERN until the PM signs off. Until then the 220 Revision Log and the plan's superseded-statements note describe a deviation that has not been approved. Record the PM's decision before the design is treated as final.

The design also leaves the mechanism undecided in the "measurable contention" branch. TD5 says the slice "stops after the report and goes to the PM". The plan expects 233 to choose a mechanism, so that branch does not deliver the slice's goal.

### [CONCERN] Success Criterion 4 claims the bound is in force, but nothing is installed

The Overview and TD7 hold the install until the release after 234, and 233 closes without installing. Criterion 4 nevertheless says "the bound is in force, and weekday passes are unaffected". It also says the 2× headroom argument "is not shown". Nothing in this slice verifies enforcement. The `MemoryMax` that this slice sets does not reach production until another slice's release, and the Saturday 6.2 GiB pass has no test against the bound. The architecture assigns 233 the job of setting the limit, and the plan expects the setting to be shown working. Restate criterion 4 as "declared, drift-guarded and cutover-checkable". Add the install-time verification, which sits outside this slice's closing evidence, as an explicit handoff to 234's release.

### [CONCERN] The cutover script is built now for an install that will not run in this slice

This slice delivers `scripts/cutover_233_arbitration.py` with a failure-handling table, a report file and an exit-code contract. It exercises only `--check`. The install-time logic runs at a later release, after 234 has added its tick pass unit and changed the member set that the bound is derived from. `cutover_common` already provides preflight, install and report. Verification reduces to two reads, `systemctl show -p MemoryMax` and `memory.max`, plus `memory.events`. These could be steps in the 234 release cutover, or a short checklist in runbook 100. Building and reviewing an untested `--ref` path now adds code that 234 may need to change. Defer the script, or cut it to the `--check` preflight plus a runbook checklist.

### [CONCERN] The component table contradicts Technical Decision 7 on a contention re-run

The component structure describes `cutover_233_arbitration.py` as "install → verify bound → contention again → report". TD7 and Technical Scope item 5 say the cutover has no contention re-run. The Overview dropped it deliberately. An implementer reading the component table could build the re-run back in. Correct the table line.

### [NOTE] Scratch tick server deviates from 226's harness, and the design discloses it

The architecture's principle that backup is part of done, together with 227's WAL archiving, makes looping the tick rebuild on `17/tick` impractical. A scratch server under `/data/restore-test` with settings copied from `17/tick` is proportionate, and it reuses 226's and 228's modules rather than forking them. The design states the known difference, no `zstd` WAL compression, and bounds it at under one core of 32. The `TRUNCATE` guard has two parts: the database name and a private socket. That satisfies the project's destructive-statement rule.

### [NOTE] Failure modes are enumerated for each new I/O path

The guards table covers:
- memory and disk floors;
- a walk running past twice the ceiling;
- no clear window;
- interrupt or raise, with teardown in `finally`.

Production unreachable at preflight or mid-run is handled with a partial report. Failures after install are tabulated, including a `memory.max` mismatch, `oom_kill`, and `max` without a kill, and the design says the slice is never restarted. No NFR in the architecture governs this path, and the memory-oversubscription concern is addressed through `MemoryMax`.

### [NOTE] Dependency direction and the 234 interface are consistent with the architecture

The slice consumes 226, 228 and 916/921/922, and provides to 234 the rule for placing a tick pass unit and re-deriving the bound. This matches the architecture's deferred-cadence position for 234. The added dependency on 228, beyond the plan's `[226]`, is justified by the scratch-server reuse. The reopen triggers are recorded: a measurable-contention verdict, or a `Type=simple` streaming source. This matches the architecture's realtime-not-precluded stance.

### Run Digest

- Response length: 6662 chars
- Response is newline-free: no
- Tool calls made: 8
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 41.6 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
