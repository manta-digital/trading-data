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
reviewedSha: b0ddf4acd354e3d9d544deb36c7fcf2e79ec8e47
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 8
turns: 9
promptTokens: 383447
cachedTokens: 280708
cacheWriteTokens: 102729
completionTokens: 8872
durationSeconds: 76.8
costUsd: 0.528468
costBasis: provider
runId: run-20261015-p4-227e5f01
squadronVersion: 0.22.0
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Placement and boundaries match the 220 architecture"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#Architecture"
  - id: F002
    severity: pass
    category: scope
    summary: "Deviations from plan entry 16 are named, justified and carried back into the plan"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#Architecture statements superseded"
  - id: F003
    severity: concern
    category: operability
    summary: "The cutover leaves production health failing, with no path to green inside the slice"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md:315-316, 535-564"
  - id: F004
    severity: concern
    category: error-handling
    summary: "Failure handling is unspecified for unit-file discovery and for unexpected exceptions in the health row"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md:221-243, 297-313, 398-399"
  - id: F005
    severity: note
    category: proportionality
    summary: "Proportionality is acceptable; prose can be trimmed"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md#Technical Decisions"
  - id: F006
    severity: note
    category: integration
    summary: "The health unit's hardening permits the `systemctl show` read"
    location: "project-documents/user/slices/235-slice.acquisition-memory-bound-kept-current.md:290-295"
---

# Review: slice — slice 235

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Placement and boundaries match the 220 architecture

- The peak is stored on `pass_runs` through a minute-track migration. That follows "Operational state has one home".
- The row is written only by the pass process, at close, through the shared recorder's best-effort contract. The recorder keeps the rule that the observer never writes rows.
- Dependencies point from the cli and scripts layers into `data/acquisition` and never from one source into another. The tick package imports nothing from Kalshi.
- The slice supersedes the 233 arbitration entry in the architecture ("derived from journal peaks") and commits to a dated slice-235 entry for it.
- `MemoryMax` stays the only knob on `manta-acquisition.slice`. The architecture's "no lock, no scheduler" stance is untouched.

### [PASS] Deviations from plan entry 16 are named, justified and carried back into the plan

- There are three deviations, and each has a reason rooted in a verifiable host fact:
  - Health reads the loaded value through `systemctl show`, because an idle slice has no live cgroup.
  - An idle member keeps its newest recorded peak.
  - The bound goes into a drop-in rather than the shipped slice unit.
- None of them drops scope the plan assigns. The slice commits to rewriting the plan entry and moving effort from 2/5 to 3/5.
- The API item matches the plan: the peak is on `last_run`, and the bound verdict is not served.

### [CONCERN] The cutover leaves production health failing, with no path to green inside the slice

- The walkthrough expects `FAIL acquisition memory bound` at its end (step 5). Step 2 shows the installer printing `NOT RE-DERIVED` until a tick run is recorded.
- Tick is manual, with "no daily history pull" and no cadence (architecture, "Cadence"). The hourly health timer can therefore fail for an unbounded time.
- While tick is unmeasured, a single member also blocks re-derivation of the bound for the three timed members.
- The slice's own Value section says the check should be a signal on which people act. A standing, expected FAIL is the alarm fatigue that TD3 rejects for the idle-month case.
- Integration Requirement 2 ("a release installed after this slice ships its bound") cannot be verified inside the slice for the same reason.
- 234 already fired the tick pass without a purchase, so a no-purchase firing is available. Add one `sudo mt-run tick` firing as a cutover step so the "never measured" gate clears at cutover. TD4 already states that such a peak is unrepresentative, and the issue #34 confirmation stays as written.
- Alternatively, state explicitly that the PM accepts a standing FAIL until the first tick run, and say who runs it and when.

### [CONCERN] Failure handling is unspecified for unit-file discovery and for unexpected exceptions in the health row

- `members(unit_dir)` is a new I/O path over `/etc/systemd/system`. Only the empty result is specified (`no unit declares Slice=…`). An unreadable or missing directory is not.
- Patterns says other errors "propagate with their traceback". TD4's table maps only `UndefinedColumn`, `MemoryBoundError` and the `systemctl` failures to a FAIL row.
- It is not stated whether another exception (database error, `OSError`) in this check fails only this row or aborts `gather` and hides the checks after it. TD4 takes care to isolate the missing-column case, so the same requirement applies here.
- State the handling for both: map them to a FAIL row for this check, or cite the existing `gather` behavior for unexpected check exceptions.

### [NOTE] Proportionality is acceptable; prose can be trimmed

- Each mechanism has a requirement behind it:
  - the migration and cgroup reader
  - the rule module
  - the health row
  - the installer lib
  - the cutover script (a pending migration makes `upgrade_production.py` refuse)
- The `systemd_unit` column is justified against a second kind-to-unit table.
- The rejected-alternative narratives could be shortened at implementation (the `runuser` rejection and "Why a drop-in"). They restate rationale and add no mechanism.
- `SYSTEMCTL_SHOW_TIMEOUT_SECONDS` and `MEMORY_BOUND_DERIVE_TIMEOUT_SECONDS` are the only knobs. Each guards a real hang path.

### [NOTE] The health unit's hardening permits the `systemctl show` read

- `mt-health.service` runs as `manta-trading` with `NoNewPrivileges`, `ProtectSystem=full`, `ProtectHome` and `PrivateTmp`.
- None of these blocks an unprivileged `systemctl show` or a read of `/etc/systemd/system`, so the TD4 claim holds.
- `/opt/manta-trading` is a full checkout, so the installer's `scripts/acquisition_memory_bound.py` path exists on the host.

### Run Digest

- Response length: 5967 chars
- Response is newline-free: no
- Tool calls made: 8
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 9
- Tokens — prompt / cached / cache write / completion / reasoning: 383447 / 280708 / 102729 / 8872 / not computed
- Duration: 76.8 s
- Cost: $0.53 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
