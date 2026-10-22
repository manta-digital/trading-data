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
reviewedSha: b4a76c728f58c589acda26a7942a1ed8d6def183
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 39
turns: 20
promptTokens: 1074628
cachedTokens: 851427
completionTokens: 96523
reasoningTokens: 92544
durationSeconds: 2096.1
costUsd: 0.081629
costBasis: alias_pricing
runId: run-20261022-p5-064cabd0
squadronVersion: 0.23.8
findings:
  - id: F001
    severity: concern
    category: uncategorized
    summary: "Task 6.4 creates `deploy/lib/backup_roots_env.sh`, a file the design's Technical Scope does not name"
    location: "project-documents/user/tasks/924-tasks.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md"
  - id: F002
    severity: concern
    category: uncategorized
    summary: "An integration success criterion has no in-slice verification task"
    location: "project-documents/user/tasks/924-tasks.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md"
  - id: F003
    severity: note
    category: uncategorized
    summary: "Remaining coverage is complete and proportionate"
    location: "project-documents/user/tasks/924-tasks.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md"
---

# Review: tasks — slice 924

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash

## Findings

### [CONCERN] Task 6.4 creates `deploy/lib/backup_roots_env.sh`, a file the design's Technical Scope does not name

Task 6.4 adds a new module (`deploy/lib/backup_roots_env.sh`) to hold the install step's logic "so it can be tested without root". The slice design's Technical Scope lists exactly two shell changes — `deploy/install-production.sh` and "`deploy/lib/env_add_keys.sh`: a sibling function `env_set_key`" — and its Architecture diagram shows step 4 calling `backup_clusters_load(...)` and `env_set_key` directly. No new lib file is authorized. The deviation also makes task 8.1's own gate self-contradictory: "`git diff <target>... --stat` touches only files named in the design's Technical Scope" would fail for a file the design never names. Additionally the task file's Context Summary enumerates the deliverables and omits `backup_roots_env.sh`, so the summary and task 6.4 disagree with each other. Either inline the logic in `install-production.sh` as designed, or amend the design's Technical Scope (and the task-file summary) to name the new file. This is not a functional gap — task 6.4 traces to criterion 8 — but it is an unauthorized structural addition that a reviewer of the diff will flag.

### [CONCERN] An integration success criterion has no in-slice verification task

The design's Integration Requirements include "`mt-health.service` on manta9000 runs the new checks as `manta-trading`, and both rows read `OK`", and its Verification Walkthrough steps 1–2 are the install-plus-journalctl proof of it. Task 8.2 defers exactly those steps to issue #54, leaving the criterion unmapped to any task in this file (8.2 only checks criteria 1–8 against tasks 3–6 and the local walkthrough steps 3–6). The rest of the walkthrough is well covered, so this is a tracking gap rather than a functional one — but the design does not authorize deferring its integration requirement, and nothing in this task file records who owns issue #54 or when it must close. Add a task that names the follow-up explicitly (issue number, owner, what "both rows read OK" will be read from) rather than a sentence in 8.2, or record the PM's agreement that this criterion ships unverified.

### [NOTE] Remaining coverage is complete and proportionate

Each design success criterion maps to at least one task: criteria 1–3 and 7 to tasks 3.4–3.7 and 5.3; criterion 4 to 3.6/3.7 and 4.2; criteria 5–6 to 3.3 and 4.2; criterion 8 to 6.4/6.5; the technical requirements (real-text fixtures, writer-to-reader test, D7 parity, `env_set_key` shell tests, `health.py` line budget) to 3.1, 4.3, 4.4, 6.2/6.3 and 5.2/8.1 respectively; the docs list to 7.1–7.4. No task traces to nothing, sequencing respects the design's dependency order (constants → pure functions → I/O → wiring → install → docs), checkpoints are spread across all five working sections, and one task file with five checkpoints is the right size for a 2/5 slice — the test volume matches the design's explicit Technical Requirements rather than exceeding them. The slice states no NFR that would require a `tests/load/` task or CI gating, so none is missing.

### Run Digest

- Response length: 4079 chars
- Response is newline-free: no
- Tool calls made: 39
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 343760
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / cache write / completion / reasoning: 1074628 / 851427 / not computed / 96523 / 92544
- Duration: 2096.1 s
- Cost: $0.08 (alias_pricing)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 3
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 3
- Finding-shaped matches — surviving validation: 3
