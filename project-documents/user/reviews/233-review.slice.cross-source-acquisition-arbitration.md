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
reviewedSha: 0f51e3fcd8169f291a0c3e67a7621d20d6bb5b77
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 7
durationSeconds: 45.6
runId: run-20261014-p4-9d59dc12
squadronVersion: 0.21.7
findings:
  - id: F001
    severity: concern
    category: scope
    summary: "Plan-assigned scope (weights, post-setting re-run) dropped without PM confirmation"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md#Technical Decision 1: only `MemoryMax`; no `CPUWeight` or `IOWeight`"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Verdict can read \"none measured\" when the tick load was not actually running during the walks"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md#Technical Decision 3: the minute load is production's own unit, fired on a weekday"
  - id: F003
    severity: concern
    category: architecture-alignment
    summary: "Slice-wide `MemoryMax` weakens the architecture's tick-versus-other-sources isolation"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md#Technical Decision 4: the `MemoryMax` rule"
  - id: F004
    severity: concern
    category: dependencies
    summary: "Slice specifies another slice's cutover script behavior and leans on it for its core claim"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md#Technical Decision 7: the install and its check ride 234's cutover"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Design is heavier than the expected \"no contention\" outcome needs"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md#Component Structure"
  - id: F006
    severity: note
    category: error-handling
    summary: "Contention run does not exclude the backup window on `nvme1n1`"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md#Data Flow"
  - id: F007
    severity: pass
    category: architecture-alignment
    summary: "Alignment with the architecture's two-step arbitration structure"
    location: "project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md#Overview"
---

# Review: slice — slice 233

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Plan-assigned scope (weights, post-setting re-run) dropped without PM confirmation

The architecture ("Cross-source arbitration", line 159) and the Universe-expansion entry (line 197) say the steady-state slice "sets `IOWeight`/`CPUWeight`/`MemoryMax` on the slice from those numbers". The slice plan entry also asks for the measurement to be repeated with the setting in force. The slice drops both and replaces the re-run with a verify-only check. The technical reasons are credible: the slice has no sibling cgroup, the NVMe schedulers are `none`, and `io.cost` is absent. But the doc itself says "PM confirmation required, not yet given." Dropping assigned scope is a CONCERN until the PM confirms it. The PM gate at Development Approach step 5 is the right mechanism, but the confirmation should be obtained before implementation starts. As written, the prove-first measurement (steps 1–4) is built and run before the decision that shapes the unit file.

### [CONCERN] Verdict can read "none measured" when the tick load was not actually running during the walks

The verdict rule compares only overlapped walk duration against `max(control, 30-day walk max)`. The guard table covers memory, disk, a stuck walk, no clear window, and interruption. It does not cover these cases:
- the `TickLoop` thread dying or stalling before or during an overlapped walk;
- the scratch server crashing or being OOM-killed;
- the tick loop running below the "tick alone" rate.

The architecture requires the contention to be measured with the tick ingest running concurrently. If the load collapsed mid-walk, a "none measured" verdict (exit 0) would settle the arbitration on evidence that never exercised contention. Add a validity condition to the verdict: the overlapped tick rate must stay within a stated fraction of the alone rate across each walk window. Otherwise the result is "invalid run" with exit 2.

The blocking `sudo systemctl start` also needs a stated client-side timeout. The 2× walk-bound guard says the pass is left alone, but not how the blocking call is abandoned.

### [CONCERN] Slice-wide `MemoryMax` weakens the architecture's tick-versus-other-sources isolation

The architecture's parity goal requires that a tick pass failing cannot affect any other source. The slice justifies the bound as "a runaway ... is killed inside its own slice". But a slice-level limit makes the minute, daily, Kalshi and future tick units share one cgroup budget. When the bound is hit, the kernel OOM killer picks the largest process in the cgroup, which need not be the leaking unit. For example, a tick runaway could kill the Saturday minute pass, which holds a 6.2 GiB peak. The doc should either:
- state this cross-victim behavior as an accepted tradeoff, or
- say why a per-unit `MemoryMax` is not preferred. It would isolate each pass and avoid summing worst-case peaks.

The stated rule `2 × Σ max-peaks` also provides no protection until the bound is actually hit, because the sum is larger than any single pass's peak.

### [CONCERN] Slice specifies another slice's cutover script behavior and leans on it for its core claim

233 closes without the bound being in force and delegates the verification to 234's cutover script (issue #33). It prescribes that script's behavior in a three-row table of exit codes and operator actions, and 234's design does not appear to own those requirements. This is a hidden dependency: the success claim "the bound is in force" rests on work that 234 may not pick up. It is also scope that belongs in 234's design. Reduce the table to one line stating the requirement (read `memory.max` and `memory.events` after install, fail on mismatch or `oom_kill`), and record it in 234's slice plan entry so it is traceable. Also make explicit that, until 234 ships, the unit file's `MemoryMax` is declared but inert, so the architecture's "set `MemoryMax`" is satisfied only at declaration level.

### [CONCERN] Design is heavier than the expected "no contention" outcome needs

The design adds four scripts, a scratch PostgreSQL lifecycle, a generalisation of 228's `rebuild_commands`, and a drift-guard test. It also adds journal parsing for the memory bound and a document set spanning the runbook, 900 plan, 220 architecture log, slice plan and CHANGELOG. All of this serves a verdict the doc itself expects to be "none measured", plus one memory number. Candidates to cut or defer:
- Split `contention_233_run.py` from `contention_233.py`. The pure verdict and guard logic can live in one module.
- The `--check` mode duplicates the full run's preflight. Keep it only if it is cheap.
- The drift guard that forbids `CPUWeight`/`IOWeight` outright. It blocks future legitimate edits and duplicates the reopen-trigger prose, so a comment in the unit file is enough.
- The failure table for 234's cutover (see the previous finding).

The scratch server itself is justified: a loop on `17/tick` would ship throwaway WAL to B2.

### [NOTE] Contention run does not exclude the backup window on `nvme1n1`

The preflight excludes the 00:35/12:35 daily and 13:05 minute firings by time of day. The doc later cites a backup window that shares `nvme1n1` (in the 234 stagger guidance), but the contention run itself does not exclude it. Backup I/O would add to the scratch tick load on the same device. A backup overlapping the run could inflate the tick-alone and overlapped figures, or confound a "measurable contention" verdict.

### [PASS] Alignment with the architecture's two-step arbitration structure

The slice measures minute × tick before choosing a mechanism. It evaluates the three mechanisms from the 900 plan and keeps calendar stagger on stated evidence. It cites 226's Kalshi result, 24 recorded Kalshi × heavy-minute firings, and the provider-quota reason for the existing stagger. It names reopen triggers, including the realtime `Type=simple` source. It adds no product surface (no CLI verb, endpoint or schema). It keeps dependencies pointing at foundation and prior slices only, and 234 inherits a decided rule.

### Run Digest

- Response length: 7564 chars
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
- Duration: 45.6 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
