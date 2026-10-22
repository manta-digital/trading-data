---
docType: review
layer: project
reviewType: tasks
slice: backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/924-tasks.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md
aiModel: deepseek/deepseek-v4.1-flash
aiProfile: openrouter
status: complete
dateCreated: 20261022
dateUpdated: 20261022
reviewedSha: ba6f23082371ee253d664c50e4a5f993702fe82c
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 48
turns: 20
promptTokens: 1289377
cachedTokens: 973957
completionTokens: 83366
reasoningTokens: 78770
durationSeconds: 1194.3
costUsd: 0.087606
costBasis: alias_pricing
runId: run-20261022-p5-064cabd0
squadronVersion: 0.23.8
findings:
  - id: F001
    severity: concern
    category: testing
    summary: "Criterion 8's mode/owner requirement has no verification task"
    location: "project-documents/user/tasks/924-tasks.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md"
  - id: F002
    severity: concern
    category: scope-creep
    summary: "Tasks 6.4 and 6.5 give conflicting placement for the install-step logic"
    location: "project-documents/user/tasks/924-tasks.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md"
  - id: F003
    severity: note
    category: documentation
    summary: "Fixture sourcing in task 3.1 points at production journals that the in-repo runbook already covers"
    location: "project-documents/user/tasks/924-tasks.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md"
  - id: F004
    severity: note
    category: verification
    summary: "The design's integration requirement for manta9000 is deferred out of this slice"
    location: "project-documents/user/tasks/924-tasks.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md"
---

# Review: tasks — slice 924

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash

## Findings

### [CONCERN] Criterion 8's mode/owner requirement has no verification task

Success criterion 8 states three properties of the resulting file: "every other line is byte-identical; the mode and owner stay `0640 root:manta-trading`; a second run reports `unchanged`." The design's Technical Requirements list the same three for the `env_set_key` shell test ("preserve other lines/owner/mode"). Task 6.5's case list covers the value, byte-identical other lines, `unchanged`, malformed table and separator parity — but never asserts the resulting mode or owner. Task 6.3's case list likewise omits mode/owner (it checks only that no temp file is left behind on failure). Tasks 6.2/6.4 task the *implementation* of the enforcement ("Keeps the mode/owner enforcement of `env_add_keys`"; "Re-assert `root:${SERVICE_USER}` `0640` after"), so the behaviour is written but nothing proves it. Add one assertion to 6.3 or 6.5: after `env_set_key` and after the install-step function, `stat -c '%a %U:%G' <env file>` is `640 root:manta-trading`, and it stays so when the second run reports `unchanged`.

### [CONCERN] Tasks 6.4 and 6.5 give conflicting placement for the install-step logic

Task 6.4 instructs the implementer to put the new work inline in `deploy/install-production.sh`: source the two libs, call `backup_clusters_load …`, join `BC_ROOT`, call `env_set_key …`, re-assert `0640`, and replace the "Never overwrite, never merge" comment. Task 6.5 then opens with "Look at how existing tests exercise `install-production.sh` … If none can run it without root, test the step through a thin sourced function: move the 'derive and set `MT_BACKUP_ROOTS`' lines into a function in `deploy/lib/` (e.g. `backup_roots_env_set <file> <owner> <conf>`)". The fallback moves lines 6.4 just placed in `install-production.sh` into a new `deploy/lib/` function — a file and symbol the design's Technical Scope does not list — and leaves 6.4's checklist items describing a layout that no longer exists. Decide the placement before starting 6.4: either fix the logic as an inline block in `install-production.sh` and verify it by inspecting the generated diff (no new lib function), or state up front that the step is implemented as `backup_roots_env_set` in `deploy/lib/` and adjust 6.4's bullets to "call the function from step 4". As written, a junior AI cannot tell which structure to commit.

### [NOTE] Fixture sourcing in task 3.1 points at production journals that the in-repo runbook already covers

Task 3.1 asks to "Source the real text from the production flag history: `grep -rn "ARCHIVE-BROKEN raised\|BACKUP-STALE raised" ` over `journalctl -t manta-backup` and runbook 200's drill row (2026-09-06)." As written the grep has no path/stream argument (it reads as "search journal output", which needs a pipe). Runbook 200 carries the drill evidence in-repo — the check table names `archive_wedged`, `archiver_failing`, `unarchived_backlog`, `archive_tmp_leftover` and `offsite_wal_stale` with their thresholds, and the design's API Contract shows the expected 2026-10-18 detail verbatim. Point 3.1 primarily at runbook 200's check table plus the design's example line, and keep the journal grep as the optional cross-check; that removes the dependency on host access without weakening the "built from the real production text" requirement.

### [NOTE] The design's integration requirement for manta9000 is deferred out of this slice

The design's Integration Requirements say "`mt-health.service` on manta9000 runs the new checks as `manta-trading`, and both rows read `OK`". Task 8.2 runs walkthrough steps 3–6 only and states that steps 1–2 "belong to release installation and are not tasks here". That reading is defensible — the install runs under sudo on the host — but the criterion then has no owner in this task file. It should be carried into the release-install step explicitly (one line naming the check, e.g. `journalctl -u mt-health.service | grep backup` after the install) so the requirement is not silently dropped between the slice and the release.

### Run Digest

- Response length: 5317 chars
- Response is newline-free: no
- Tool calls made: 48
- Tool calls failed: 1
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 293130
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / cache write / completion / reasoning: 1289377 / 973957 / not computed / 83366 / 78770
- Duration: 1194.3 s
- Cost: $0.09 (alias_pricing)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 4
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 4
- Finding-shaped matches — surviving validation: 4
