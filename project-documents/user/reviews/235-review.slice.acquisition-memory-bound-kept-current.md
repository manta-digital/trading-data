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
reviewedSha: 1860a036a1362c38cf8720660248459c1a1464e6
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 5
turns: 6
promptTokens: 244729
cachedTokens: 140606
cacheWriteTokens: 104117
completionTokens: 5401
durationSeconds: 50.6
costUsd: 0.485292
costBasis: provider
runId: run-20261015-p4-227e5f01
squadronVersion: 0.22.0
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Layering, dependency direction and ownership of run accounting"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#Architecture"
  - id: F002
    severity: pass
    category: error-handling
    summary: "Failure modes enumerated for each new I/O path"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#TD1, TD4, TD5"
  - id: F003
    severity: concern
    category: under-specification
    summary: "A no-purchase tick run satisfies the \"never measured\" gate with an unrepresentative peak"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#TD3"
  - id: F004
    severity: concern
    category: scope
    summary: "Proportionality: heavier than a Low-risk, Effort 2/5 slice requires"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#Technical Scope"
  - id: F005
    severity: concern
    category: documentation-consistency
    summary: "Deviations from the slice plan entry are recorded only in the architecture document"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#TD3, TD4, TD5"
  - id: F006
    severity: note
    category: accuracy
    summary: "Recorded peak is cumulative for multi-cycle daemons, which weakens the \"per-run\" claim"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#TD1"
  - id: F007
    severity: note
    category: edge-case
    summary: "Stale member unit files can pin the never-measured FAIL"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#TD2"
---

# Review: slice — slice 235

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Layering, dependency direction and ownership of run accounting

- The peak is written to `pass_runs` by the pass process itself, at close. This is consistent with the 220 principle "Operational state has one home". It adds no tick-track dependency, and `pass_runs` stays on the minute track.
- Dependencies flow from the recorder and the CLI into one rule module. The derive rule has a single owner.
- Each pass's own close path records its measurement, and no observer writes rows. That keeps 922's rule.
- The "Realtime paths" check is present and correct.

### [PASS] Failure modes enumerated for each new I/O path

- The cgroup read cannot abort a pass. It logs with `logger.exception` and closes the row with NULL peak columns.
- `systemctl show` has a timeout, a missing-binary case and a non-zero-exit case.
- The installer's `systemd-run` has a runtime cap, an exit-code taxonomy (0, 2, other) and a regex on stdout. Writes go through a temp file and one `mv`, so the old drop-in is left intact on failure.
- A database outage at close and the OOM-kill case are named as accepted losses.

### [CONCERN] A no-purchase tick run satisfies the "never measured" gate with an unrepresentative peak

- The Overview and the Excluded list reject backfilling the journal peak because 234's no-purchase firing "is not representative".
- TD3 counts any run of any outcome toward the peak, and `fetch_peaks` ignores only NULL rows.
- After the cutover, `sudo mt-run tick` with nothing to buy will record the same kind of small, unrepresentative peak.
- That single row clears the never-measured FAIL, makes the next install write a bound that omits real tick ingest memory, and lets issue #34 close. The walkthrough says #34 closes "once a real tick run is recorded", but nothing in the mechanism checks that the run was real.
- Because the tick unit has no timer (`FiringSchedule.manual()`), the next real ingest could be far away. The OOM-kill path then becomes the first signal, and the design says the bound cannot learn from a killed run.
- Either define "representative" using a column the design already has, such as a tick run that ingested units, or state explicitly that #34 closes by operator attestation and not by the check. The first option needs the `pass_runs` outcome or detail that the design already reads.

### [CONCERN] Proportionality: heavier than a Low-risk, Effort 2/5 slice requires

- The plan entry has three mechanisms: record the peak, add a health check, and re-derive at install. The design adds ten included items. These include a new cgroup parser module, a rule module with four constants and four functions, a `systemd-run` wrapper with an exit-code protocol, a drop-in file layout, a fixed-regex stdout validator, a dedicated cutover script, and a shell-lib unit test.
- Candidates to cut or defer:
  - **`mt data overview --json`.** The plan places the peak on the API only, and the design admits the JSON output is included only because it shares the API's shape.
  - **`systemd_unit` column.** The doc keeps it so peaks join by unit name. A fixed kind-to-unit mapping would be a heavier option, so keep the column, but justify it explicitly against the plan's "no new mechanism" framing.
  - **`systemd-run` plus `EnvironmentFile`.** Check whether `runuser` or `sudo -u` with the env file sourced would meet the same need with fewer moving parts. The doc rejects no lighter alternative.
  - **`cutover_235_memory_bound.py`.** Justify it against extending `cutover_common` pieces, because 234's pattern is reused almost whole.
- If all of this is kept, the plan's Effort 2/5 estimate should be revisited.

### [CONCERN] Deviations from the slice plan entry are recorded only in the architecture document

- The design deliberately departs from the parent slice plan entry in three places:
  - The health check reads `systemctl show` instead of the live cgroup `memory.max`.
  - An idle member keeps its newest peak instead of failing when it has "no recorded peak in the window".
  - The bound goes into a drop-in, not "the slice unit".
- The reasoning for each is sound. For example, the slice cgroup does not exist while idle.
- The design commits only to a dated slice-235 entry in the 220 architecture document, under "Architecture statements superseded". The slice plan entry (item 16 in `220-slices…`) is left asserting the old behavior, and the architecture's slice-233 entry will contradict it too.
- Add the slice plan entry to the list of documents updated, so the two parents do not disagree.

### [NOTE] Recorded peak is cumulative for multi-cycle daemons, which weakens the "per-run" claim

- The doc correctly shows the cumulative cgroup counter gives the right maximum over a window.
- The Value section and the API field, however, call the figure "per-run memory". For `--stop-when-done` daemons that close several rows in one process, later rows carry the process's peak so far, not their own cycle's.
- The `last_run.memory_peak_bytes` description in TD6 should say "process peak at close".

### [NOTE] Stale member unit files can pin the never-measured FAIL

- Members are discovered from `/etc/systemd/system/*.service` files that declare the slice.
- The installer copies unit files in and the design does not say it removes retired ones. A unit that leaves the slice, or is renamed, could stay on disk. It would then be a member that never records a peak, which fails health and blocks every drop-in write.
- No such case exists today. If it does arise, the failure message names the unit, so the operator can see it. Say in the doc whether install removes retired units or the operator does.

### Run Digest

- Response length: 6905 chars
- Response is newline-free: no
- Tool calls made: 5
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 6
- Tokens — prompt / cached / cache write / completion / reasoning: 244729 / 140606 / 104117 / 5401 / not computed
- Duration: 50.6 s
- Cost: $0.49 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
