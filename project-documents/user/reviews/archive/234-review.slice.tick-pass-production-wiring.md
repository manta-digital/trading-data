---
docType: review
layer: project
reviewType: slice
slice: tick-pass-production-wiring
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/234-slice.tick-pass-production-wiring.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261014
dateUpdated: 20261014
reviewedSha: a436419c829c3decc641ee2fe75a8120fae8321b
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 4
durationSeconds: 46.0
runId: run-20261015-p4-4669ddf5
squadronVersion: 0.21.9
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "Hung or wedged run has no detection or handling"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md#TD2 — One verb, one unit, one row"
  - id: F002
    severity: concern
    category: scope
    summary: "Per-batch heartbeat handed over by 225 is declined"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md#Data Flow (step 2)"
  - id: F003
    severity: concern
    category: proportionality
    summary: "Cutover script is heavier than the slice needs"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md#TD4 — The cutover script owns verification"
  - id: F004
    severity: concern
    category: architecture-alignment
    summary: "Architecture statements are superseded in four places, and the Revision Log update is only a late task"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md#Architecture statements superseded"
  - id: F005
    severity: note
    category: architecture-alignment
    summary: "Manual-only cadence answer is explicit and drift-guarded"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md#TD1 — Manual-only cadence, stated in code"
  - id: F006
    severity: note
    category: scope
    summary: "Deviation from the plan's three env keys to five is justified"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md#TD3 — Settings enter production by a one-time root script"
  - id: F007
    severity: note
    category: architecture-alignment
    summary: "Isolation, one-way coupling and accounting match the architecture"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md#Architecture"
  - id: F008
    severity: note
    category: scope
    summary: "Open interest question is carried as the plan requires"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md#Special Considerations"
---

# Review: slice — slice 234

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Hung or wedged run has no detection or handling

The design covers a killed run (SIGTERM, SIGKILL, OOM) well. It covers the overlap of its own cutover with a by-hand run. It does not cover a run that hangs without dying.
- **The cause:** `TimeoutStartSec=infinity`, no `WatchdogSec`, and no per-batch heartbeat are all chosen together. A stuck provider download, a stuck ingest `COPY` or a blocked database call then never ends.
- **What the operator sees:** an open `TICK` row. The abandoned-run closer only closes rows whose pid is gone, so a live hung pid is never closed.
- **Why the new unit makes it worse:** `mt-run tick` and the unit would block any later `systemctl start` of the same unit indefinitely.
- **What the design says:** "Timeout and kill handling: TD2" covers only the kill case. The hang case should be named, with its handling stated. For example, the operator stops the unit and it recovers through the kill path. Or an overall wall-clock bound is added.
- **The advisory locks:** `220_000_001` and `220_000_002` are held across the run. The slice should say what a second `run` does when a by-hand `pass` holds the lock, and how that maps to `TickOutcome` and the exit code.

### [CONCERN] Per-batch heartbeat handed over by 225 is declined

The slice plan's notes record that 225 handed the heartbeat to 234: "One outcome per unit returns to the loop, with no per-batch progress; the heartbeat is handed to 234". This design declines it. That drops scope assigned to this slice.
- **Where the rationale is thin:** the architecture's ingest principle says progress "stays observable during a multi-gigabyte ingest". The slice justifies declining mainly with 226's 3.9 s slowest unit. That measurement doesn't bound the multi-gigabyte units the Standard plan's included year will bring.
- **How it ties to the hang finding:** the heartbeat is the only liveness signal a hung ingest could give. The two decisions should be argued together.
- **What to change:** either implement the minimal heartbeat, or record the deferral explicitly in the plan and Revision Log with a trigger. For example, "when the first unit exceeds N seconds or a watchdog is adopted".
- **What already holds:** the "Architecture statements superseded" list does include the decision.

### [CONCERN] Cutover script is heavier than the slice needs

The nine-step cutover with a generalised `cutover_common.migrate(track, id)` is mostly justified. Two parts are not.
- **Connection-budget step (6):** it is a pure arithmetic function with a multi-worker model (workers × 2 × `TICK_API_POOL_MAX`) and a 1-versus-4-worker unit test. Production runs `mt-serve` with one worker ("With no `--workers` it is 1"). Cut it to a direct check at the installed worker count against `max_connections` minus reserved. Leave general worker-count arithmetic to a slice that changes workers.
- **Runbook 100 rewrite (Technical Scope 10):** the merge of the two "Adding a source" checklists restates and restructures another document. Defer it, or reduce it to adding the tick row and the five keys.

### [CONCERN] Architecture statements are superseded in four places, and the Revision Log update is only a late task

Four architecture statements are overridden: Cadence, the multi-source "carry on" posture, the escalation rule, and the heartbeat. The design lists them well and schedules a Revision Log entry in Development Approach step 7. Two details need tightening.
- **Cadence:** the architecture is internally inconsistent here. The "Operational state" principle says `PassKind.TICK` lands with its `schedule_for` branch. The Cadence paragraph says no branch. The design's `FiringSchedule.manual()` satisfies the "explicit no timer answer" requirement. Say that it resolves this inconsistency rather than reversing a decision.
- **Health exit code:** the architecture says the multi-source surfaces print a loud line "and carry on". TD6 prints the line and carries on, but also makes health exit 1 for "not configured". That adds a new alerting behaviour for the hourly check. Confirm with the PM that a tick misconfiguration should turn the whole health check red, since the architecture's stated intent was that tick must never silence or disturb minute, daily and Kalshi monitoring.

### [NOTE] Manual-only cadence answer is explicit and drift-guarded

`FiringSchedule.manual()`, the caller checks on `is_manual`, and the drift guard tying "manual" to the absence of `mt-tick-pass.timer` meet the architecture's requirement for an explicit "no timer" answer. The slice also does not build any cadence constant. This matches the plan's "a timer only if the cadence decision calls for one". The realtime-paths check is present and correct.

### [NOTE] Deviation from the plan's three env keys to five is justified

The plan names three keys. TD3 adds `MT_TICK_SPEND_30D_CEILING_USD` and `MT_TICK_ARCHIVE_DIR`, because the architecture says purchase refuses without both ceilings and the preflight needs the archive. Keeping `MT_TICK_MAINTENANCE_URL` out of the service environment respects 913 credential separation. The key stays confined to the unit's environment, consistent with the architecture's "Secrets and spend guards".

### [NOTE] Isolation, one-way coupling and accounting match the architecture

- `PassKind.TICK` lands as a minute-track migration.
- The recorder is best-effort.
- Health reads the tick database through a separate path (`read_line`) so a tick failure only changes tick rows.
- The API keeps its distinct `tick_db` field.
- Tick runs only when an operator starts it, so the 14G `MemoryMax` interim is acceptable; re-deriving it is deferred to issue #34.

These preserve the tick → production dependency direction and the isolation goal. The memory, ingest and retention figures are restated with specific targets (14G, 1800 s wait budget, 30-day window, 7-day warning).

### [NOTE] Open interest question is carried as the plan requires

The plan says 234 asks the PM again about the Standard-plan subscription date if none is recorded. The design asks it and leaves open interest excluded. That is acceptable. The answer should be recorded before task breakdown so it doesn't stay open.

### Run Digest

- Response length: 7707 chars
- Response is newline-free: no
- Tool calls made: 4
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 46.0 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
