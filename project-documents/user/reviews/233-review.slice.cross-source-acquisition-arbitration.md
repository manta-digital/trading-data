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
reviewedSha: c2750ad4605d3150489ae9c2c785344f7af0f63a
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 7
durationSeconds: 60.3
runId: run-20261014-p4-9d59dc12
squadronVersion: 0.21.7
findings:
  - id: F001
    severity: concern
    category: scope
    summary: "Plan-assigned `IOWeight`/`CPUWeight` scope is dropped"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md:249-269"
  - id: F002
    severity: concern
    category: proportionality
    summary: "Measurement and cutover machinery is heavier than the decision needs"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md:94-101"
  - id: F003
    severity: concern
    category: verification
    summary: "After-install verification cannot exercise the failure the bound guards against"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md:507-512"
  - id: F004
    severity: concern
    category: failure-modes
    summary: "Cutover coupling and failure handling are left open"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md:621-639"
  - id: F005
    severity: note
    category: integration
    summary: "234 interface assumes a scheduled pass and a stagger slot"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md:468-474"
  - id: F006
    severity: pass
    category: architecture-alignment
    summary: "Boundaries, dependency direction and failure enumeration are sound"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md#Architecture"
---

# Review: slice — slice 233

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Plan-assigned `IOWeight`/`CPUWeight` scope is dropped

The 220 slice plan entry for 233 (line 112) and the architecture's "Cross-source arbitration" section both assign `IOWeight`/`CPUWeight`/`MemoryMax`. The slice sets only `MemoryMax`. Its reasons are host facts read on 2026-10-14. `manta-acquisition.slice` has no sibling cgroup. Both NVMe schedulers are `none`. `io.cost.qos` is absent. CPU sits at 10–15 % under tick load. The architecture says the choice is made "on that evidence", so the evidence-based deviation is legitimate. Technical Decision 6 records it as a superseded statement and as an architecture Revision Log entry.

Two gaps remain:
- The omission rests on a pre-measurement baseline and on 226's Kalshi-only result. The minute run is not what decided it.
- Nothing shows the PM has confirmed dropping plan-assigned scope. State that confirmation in the slice, or list it as a review question.

### [CONCERN] Measurement and cutover machinery is heavier than the decision needs

The slice adds six scripts, a scratch PostgreSQL server, a rebuild generalisation in 228's code, and a cutover script. The cutover script re-runs the full contention run after install. The effort is re-rated from 2/5 to 3/5. What is delivered is one `MemoryMax=` line, an unchanged mechanism (calendar stagger), and documents. Things that can be cut or deferred:
- **The after-install contention re-run.** It costs three more minute walks and a scratch rebuild. Its only purpose is to show the bound doesn't slow weekday walks, and weekday walks peak at 75–144 M, far below the bound. Verifying the bound is live needs only `systemctl show`, `memory.max` and `memory.events`, which the cutover already reads.
- **The separate `--memory-bound` and `--label` modes.** They could be one derivation function plus the run.
- **The `contention_233_report.py` split.** Fold it into the entry script.
- **The tick term in the bound.** It is derived for a unit (234's) that is deferred and may never get a timer.

Proportionate scope is one measured run, a derivation, the unit-file change with its drift guard, and a verify-only cutover check.

### [CONCERN] After-install verification cannot exercise the failure the bound guards against

Success Criterion 4 treats "none measured" after install, plus flat `memory.events`, as showing the bound works. The only pressure case the design identifies is the Saturday trailing phase, with a 6.2 GiB peak. The weekday walk cannot reach it, and the design excludes the Saturday pass (lines 123-129). A weekday run therefore shows only that the setting is live, not that Saturday's pass fits under it. The 2× headroom argument is reasonable. Either state the criterion as "bound is in force, weekday passes unaffected", or add a defined follow-up check, such as reading `memory.peak` after the first Saturday firing. Today that check is "normal monitoring, not a task".

### [CONCERN] Cutover coupling and failure handling are left open

`install-production.sh` installs the whole checkout. The cutover is therefore the first production code upgrade since v0.19.0, 351 commits behind main. A one-line memory bound is coupled to that upgrade. The design defers the timing to "the PM chooses at slice review". The slice's own completeness shouldn't depend on an undecided option, so resolve it before implementation. The recommended default is to hold the cutover for the release that follows 234, which also lets the tick term be validated.

Failure handling after the install step is also thin:
- If `memory.max` doesn't match, the script "fails and says so", but it names no recovery for a slice file already installed.
- If `memory.events` shows `oom_kill` during the after-install run, a production pass may have been killed. Only the exit code is specified, not the operator response.

### [NOTE] 234 interface assumes a scheduled pass and a stagger slot

234 is deferred until the cadence decision and may add no timer. The "firing time follows the stagger convention" requirement and the "full contention run if scheduled" step apply only if a timer exists. That is conditionally stated, which is acceptable. Also, the drift guard that asserts no `CPUWeight`/`IOWeight` on the slice would block the reopen triggers (Technical Decision 5) until someone edits the test. That is fine if intended. Say so in the guard's comment.

### [PASS] Boundaries, dependency direction and failure enumeration are sound

- **Layering:** the slice touches no `src/` path, so the source-isolation principle (no source → source import) is not engaged. Harness scripts import only the 226 and 228 harness modules.
- **Isolation:** the scratch server keeps throwaway WAL out of the archived tick cluster, consistent with the backup and isolation goals.
- **Failure modes:** the guard table covers memory and disk floors, a walk running past 2× the bound, no clear window, and interrupt or exception cleanup. Production-unreachable handling is stated for preflight and mid-run.
- **Standing 220 obligations:** the Kalshi contract diff, realtime paths and data-correctness contract are addressed.
- **Documents:** the architecture Revision Log and the plan's superseded-statements note are updated in the same slice.

### Run Digest

- Response length: 6424 chars
- Response is newline-free: no
- Tool calls made: 7
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 60.3 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
