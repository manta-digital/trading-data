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
reviewedSha: 366a7c8e4a4159fd9d27d5ed08f741e83ef70050
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 8
durationSeconds: 61.9
runId: run-20261015-p4-4669ddf5
squadronVersion: 0.21.9
findings:
  - id: F001
    severity: concern
    category: architecture-alignment
    summary: "Health exit-1 for \"tick not configured\" reverses an architecture rule and is left as an open default"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md:216-220"
  - id: F002
    severity: concern
    category: failure-modes
    summary: "The \"buys nothing\" cutover firing is guarded by a check-then-act gap"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md:196-199, 366-372"
  - id: F003
    severity: concern
    category: scope
    summary: "Proportionality: the cutover script and several one-off mechanisms are heavy for a manual-only, no-timer slice"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md:46-56, 179-209"
  - id: F004
    severity: concern
    category: failure-modes
    summary: "Orphaned-row recovery and a \"second start refused\" behaviour are asserted, not verified"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md:157, 162"
  - id: F005
    severity: note
    category: architecture-alignment
    summary: "Cadence and escalation changes are correctly scoped as supersessions of the architecture"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md:237-241"
  - id: F006
    severity: pass
    category: architecture-alignment
    summary: "Layering, dependency direction and isolation match the architecture"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md#architecture"
---

# Review: slice — slice 234

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Health exit-1 for "tick not configured" reverses an architecture rule and is left as an open default

The architecture (Operational state has one home) says multi-source surfaces print a loud `tick: not configured (MT_TICK_DB_URL unset)` line and "carry on", so a tick misconfiguration can never silence or disturb minute, daily or Kalshi monitoring. TD6 makes every non-OK tick state a failing row, which turns the hourly health verdict to exit 1. Superseded item 2 and Special Considerations both admit this is new alerting behaviour that waits on a PM answer. The design ships the architecture-contradicting behaviour as its default while the decision is open. It also puts Functional Requirement 5 and the TD6 text at risk of rework. Resolve the question before the design is finalised, or make the architecture-conformant informational line the default and promote it to a failure only on PM approval. The fallback sketched in TD6 already describes that behaviour.

### [CONCERN] The "buys nothing" cutover firing is guarded by a check-then-act gap

Step 7 runs `--estimate-only` and step 8 then fires `mt-tick-pass.service`, which runs the full universe with production spend ceilings. `run` has no options, so the firing cannot be restricted to non-purchasing behaviour. The provider's availability edge advances about every 8 hours. A session that becomes available between the two steps would make the firing buy, and the design then relies only on the spend ceilings. The Risk section names the purchase risk but treats step 7 as full mitigation. Either tighten the window, for example by re-checking the estimate immediately before the firing and failing the step if cost is non-zero. Or state explicitly that the ceilings are the accepted bound on this race.

### [CONCERN] Proportionality: the cutover script and several one-off mechanisms are heavy for a manual-only, no-timer slice

The slice delivers a unit, a `mt-run` kind, a recorder kind and settings. Beyond that it adds:
- a new verb with an outcome-severity fold and map;
- an ingest heartbeat callback;
- a 9-step cutover with connection arithmetic on two clusters;
- an archive chgrp/chmod step;
- a root env script;
- a new `units_near_deadline` read;
- a generalised `cutover_common.migrate`.

The design admits `run` and the five keys extend the plan entry, and both are justified. Things that can be cut or deferred:
- Step 6 (connection arithmetic) could reduce to a fixed assertion against 231's recorded budget; the design already says worker-count arithmetic is left to a later slice.
- The "Does this belong in the API" and "Realtime paths" sections mostly restate decisions already in the architecture and 231.
- The drift guard could merge into the existing `test_firing_schedule` tests rather than add a separate mechanism.

Trim these or state why each is needed now.

### [CONCERN] Orphaned-row recovery and a "second start refused" behaviour are asserted, not verified

TD2 relies on two things it never confirms:
- that 922's abandoned-run closer needs no per-kind threshold and will close a `TICK` row by pid;
- that systemd "refuses" a second start of an active oneshot unit.

In practice `systemctl start` on an activating oneshot waits rather than refusing. If the closer does need a threshold, a killed run would leave a stale open row on the overview indefinitely. Tie both to a test or a cutover step, or restate them as assumptions to verify at implementation.

### [NOTE] Cadence and escalation changes are correctly scoped as supersessions of the architecture

The architecture's Cadence paragraph says there is no `schedule_for` branch, and the same paragraph requires an explicit "no timer" answer. `FiringSchedule.manual()` resolves that inconsistency. The fixed 7-day retention window in place of "before the next firing" is also a documented supersession, and TD5 makes it revert when a timer exists. The slice carries the Revision Log update as a task, which is the right place for it.

### [PASS] Layering, dependency direction and isolation match the architecture

- `PassKind.TICK` lands as a minute-track migration, and `pass_runs` stays on the primary DB.
- The recorder keeps its best-effort contract.
- Health reaches the tick DB only through the second pool (`read_line`), and a tick failure changes only the tick rows.
- `MT_TICK_MAINTENANCE_URL` is kept out of the service environment.
- The standing 220 obligations (Kalshi contract diff, mapping rows, realtime-paths check) are all present.
- Timeouts, killed-run recovery and the 233 memory bound are stated with explicit handling and targets.

### Run Digest

- Response length: 5543 chars
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
- Duration: 61.9 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
