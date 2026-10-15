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
reviewedSha: b88cb22f6b72bc05b157c58e84490bdb42f134ec
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 10
durationSeconds: 47.9
runId: run-20261015-p4-4669ddf5
squadronVersion: 0.21.9
findings:
  - id: F001
    severity: concern
    category: scope-omission
    summary: "225's per-batch progress heartbeat is not picked up or disclaimed"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md#Data Flow"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Unit timeout and hang handling are unspecified for the new oneshot"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md#Technical Scope (item 4)"
  - id: F003
    severity: concern
    category: proportionality
    summary: "Scope the plan does not assign: TD7 import guard and the 10-step cutover"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md#TD7 — Minute, daily and Kalshi passes never read the tick setting"
  - id: F004
    severity: concern
    category: scope-creep
    summary: "Design adds `mt data tick run` and a composite outcome model not in the plan or architecture"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md#TD2 — One verb, one unit, one row"
  - id: F005
    severity: concern
    category: architectural-alignment
    summary: "Architecture's Cadence text conflicts with this slice and is not recorded for the Revision Log"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md#Overview"
  - id: F006
    severity: note
    category: nfr
    summary: "Retention finding window is a reasonable manual-regime answer"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md#TD5 — Retention finding with a manual pass"
  - id: F007
    severity: note
    category: boundaries
    summary: "Boundaries and dependency directions hold"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md#Architecture"
---

# Review: slice — slice 234

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] 225's per-batch progress heartbeat is not picked up or disclaimed

Slice 225 states that "234 owns the per-batch progress heartbeat that 225 omits … because its systemd timeouts are what the heartbeat serves". It also lists this as a superseded architecture statement ("only progress returns to the loop"). The 234 design never mentions the heartbeat. Data Flow step 2 says phase progress goes through `recorder.progress`, ending at a single `ingest` phase. Ingest returns one outcome per unit with no per-batch progress. The architecture requires that "progress stays observable during a multi-gigabyte ingest". The design needs to do one of two things:
- Name where the heartbeat is built (composed in `run` or added to the ingest pass) and state what it feeds, whether the `pass_runs` progress or 922's abandoned-run detection.
- Explicitly decline it with a reason, and record that against the 225 hand-off.

### [CONCERN] Unit timeout and hang handling are unspecified for the new oneshot

Item 4 lists `Slice=` and `UMask=0002` for `mt-tick-pass.service`. It does not state `TimeoutStartSec`. The existing pass units set `TimeoutStartSec=infinity` because the 90 s systemd default kills a long pass mid-run (minute, daily, kalshi and accounting units). A tick run can wait up to 1,800 s in acquisition and then ingest multi-gigabyte units. The design also does not say what happens when the process is killed (OOM, SIGKILL, `TimeoutStopSec`) after `recorder.open`. TD4 step 8 only checks `oom_kill` after a clean firing. The design names the unexpected-exception path (row closes `FAILED`) but not the killed-process path. It should state:
- the timeout settings and why;
- which mechanism closes an orphaned `TICK` row (presumably 922's abandoned-run closer, and whether it needs a tick-specific threshold);
- peer-disconnect behaviour for the paid-delivery window (a killed run leaves `SUBMITTED` units, which the retention check then covers).

### [CONCERN] Scope the plan does not assign: TD7 import guard and the 10-step cutover

TD7 adds an import-walking test for a rule the design itself says "has held by convention". No requirement in the plan or the architecture is left unmet without it. The architecture only requires that those passes "never read it", and no change in this slice affects that. It is a cut candidate. If it stays, the design should say what regression it prevents. The cutover script's step 10 (re-derive the bound and record derived versus declared) goes beyond the plan entry, which asks only that the cutover install and verify 233's bound. Step 10 reads like 233's closing work and could be deferred to 233's own follow-up. The remaining cutover steps (archive chgrp, connection-budget function with a unit test, estimate-only guard) are each tied to a plan bullet or a stated risk and are proportionate.

### [CONCERN] Design adds `mt data tick run` and a composite outcome model not in the plan or architecture

The plan entry assigns a pass unit running the existing passes. The architecture's pass form is "`mt` command → `mt-{source}-pass.service`". The design instead invents:
- a new verb, `mt data tick run`;
- a severity ordering of tick outcomes;
- `PASS_RUN_OUTCOME_BY_TICK_OUTCOME`;
- an `acquire=… ingest=…` detail;
- a `--json` shape.

The TD2 reasoning, that a single unit and a single `pass_runs` row beat two `ExecStart=` lines, is sound and I accept it. The design should still say the plan said one pass unit and name this as a deliberate extension. It should also note that the new outcome precedence (for example `REFUSED` above `IN_FLIGHT`) is a new contract the 230 exit-code vocabulary must agree with. No requirement forces this to be heavier than TD2 needs, but the composite is the largest unassigned mechanism in the slice.

### [CONCERN] Architecture's Cadence text conflicts with this slice and is not recorded for the Revision Log

The architecture's Cadence section says "no `schedule_for` branch, no `PassKind.TICK` firing calendar". The same paragraph, and "Operational state has one home", say a manual-only pass needs an explicit "no timer" answer in `schedule_for`. The design takes the second reading, which is correct because `schedule_for` is exhaustive. Earlier slices flagged such conflicts "for the architecture's Revision Log" (224, 225). This slice supersedes the first sentence, and it also supersedes:
- the architecture's "tick: not configured" wording for health, which becomes a failing row with exit 1 (TD6);
- the fixed 7-day retention window, which replaces the architecture's "before the next scheduled firing" test.

The design does not list these as superseded statements, so the architecture and slice will disagree. Add a short "architecture statements superseded" note and a task to update the Revision Log.

### [NOTE] Retention finding window is a reasonable manual-regime answer

The architecture's escalation rule is "deadline before the next scheduled firing". With no firing, TD5 uses a fixed 7-day window, and it switches to `schedule_for` once a timer exists. The 30-day retention NFR is respected, and the design is internally consistent.

### [NOTE] Boundaries and dependency directions hold

- `PassKind.TICK` lands as a minute-track migration (`060`), as the architecture requires.
- Health reaches the tick database only through `tick_composed.read_line`, with no new pool.
- A tick failure changes only the tick rows.
- Run accounting uses the recorder's existing best-effort contract.
- `MT_TICK_MAINTENANCE_URL` is kept out of the service environment, consistent with the 913 separation.
- The API question ("does this belong in the API?") and the realtime-paths check are both answered, as the architecture requires.

### Run Digest

- Response length: 7339 chars
- Response is newline-free: no
- Tool calls made: 10
- Tool calls failed: 1
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 47.9 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
