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
reviewedSha: c9102e7e46550a03753a2de3a8c4849d10acf0ca
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 44
turns: 20
promptTokens: 1014367
cachedTokens: 802816
completionTokens: 91158
reasoningTokens: 86236
durationSeconds: 698.6
costUsd: 0.07717
costBasis: alias_pricing
runId: run-20261022-p5-064cabd0
squadronVersion: 0.23.8
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Every functional success criterion traces to a task, and every task traces to a criterion"
    location: "project-documents/user/tasks/924-tasks.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md"
  - id: F002
    severity: concern
    category: uncategorized
    summary: "The slice's Integration Requirements are deferred to an issue nothing files"
    location: "project-documents/user/tasks/924-tasks.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md:275-280"
  - id: F003
    severity: concern
    category: uncategorized
    summary: "Criterion 8's owner/mode half is not executable by the prescribed test, and the test's wording contradicts it"
    location: "project-documents/user/tasks/924-tasks.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md:229-237"
  - id: F004
    severity: note
    category: uncategorized
    summary: "Context summary says four constants; the task and design define five"
    location: "project-documents/user/tasks/924-tasks.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md:47"
  - id: F005
    severity: note
    category: uncategorized
    summary: "Task 6.5 mixes an untestable install-script edit with the function's test suite"
    location: "project-documents/user/tasks/924-tasks.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md:226-237"
---

# Review: tasks — slice 924

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash

## Findings

### [PASS] Every functional success criterion traces to a task, and every task traces to a criterion

SC1 → task 5.1/5.3; SC2 → 3.4/3.5/3.6/3.7 + 5.3; SC3 → 3.4/3.5; SC4 → 3.6/3.7/4.1/4.2; SC5 → 3.2/3.3/5.1/5.3; SC6 → 4.1/4.2/3.7; SC7 → 5.2/5.3; SC8 → 6.4/6.5. Technical Requirements map to 3.1 (real fixture bodies, incl. the 2026-10-18 and 2026-09-06 shapes), 4.3 (writer-to-reader through the real `scripts/backup_health_cron.sh` with the stubbed `check_archive_health.sh`/`psql` harness in `test/unit/test_backup_health.py`), 4.4 (D7 parity against `deploy/cron.d/manta-trading-backup.cluster`, whose `--flag @CLUSTER_ROOT@/ARCHIVE-BROKEN` / `--stale-flag @CLUSTER_ROOT@/BACKUP-STALE` / `--log @CLUSTER_ROOT@/backup-health.log` tokens I verified exist), 6.3 (`env_set_key` shell tests beside the `env_add_keys` tests in `test/unit/test_provision_tick_lib.py`), and 8.1. Docs tasks (7.1–7.4) trace to the design's Docs list, including both env examples and the `mt-health.service` comment. I found no task without a criterion behind it, and no criterion without a task except SC8's owner half (below). No load-test task exists and none is needed: the slice restates no NFR and adds no new data path, so there is nothing for `test/load/` or CI gating to carry.

### [CONCERN] The slice's Integration Requirements are deferred to an issue nothing files

The design's Integration Requirements are explicit: "`mt-health.service` on manta9000 runs the new checks as `manta-trading`, and both rows read `OK`", and its Verification Walkthrough steps 1–2 spell out the install (`install-production.sh --ref <release-tag>`, then `systemctl start mt-health.service` + `journalctl | grep backup`). Task 8.2 skips both steps with "they are deferred to issue #54". No task in this file files that issue, and a repo-wide search finds `#54` nowhere except this one line (every other deferred item in the project — 233's `#33`, 234's `#34`, 169's `#14`) is named in the design or a sibling task file as well; GitHub's issue list is remote, so I could not verify the issue exists — but the gap does not depend on that. Nothing in this breakdown performs or schedules the one host step the design says the slice needs (`install-production.sh --ref <tag>`, which the design notes is in the operator's sudoers list precisely so a release install delivers it), and the repo's precedent is to carry that step in the task file (see `920-tasks.backup-hardening-and-host-bootstrap-2.md` Section 8 "Production cutover" and `234-tasks.tick-pass-production-wiring-2.md` Tasks 10.1–10.2). As written, criterion 8's production half and the "both rows read OK as `manta-trading`" requirement are left unmet and untracked. Add a cutover/verification task (install the tag, read the two rows out of `journalctl -u mt-health.service` under the service user, `systemctl --failed`), or record the deferral against an issue the slice actually creates.

### [CONCERN] Criterion 8's owner/mode half is not executable by the prescribed test, and the test's wording contradicts it

Criterion 8 requires "the mode and owner stay `0640 root:manta-trading`". Task 6.5 tests `backup_roots_env_set` "against the real `deploy/backup-clusters.conf` and a temp env file owned by the current user" and asserts "mode is `0640` and owner/group are the ones passed". Those two cannot both hold as written: `backup_roots_env_set` (task 6.4) runs `chown <owner>:`/`chmod 0640` on every run, so passing `owner=root` from a non-root test runner fails at `chown`, while passing the current user verifies only "the ones passed", never `root:manta-trading`. The `0640 root:manta-trading` assertion therefore has no executable check in the slice — it falls out only when `install-production.sh` runs as root, which is exactly the deferred `#54` path. Make the task state which owner the test passes (current user, with the `root:manta-trading` half listed explicitly as host-verified in the cutover task), so a junior implementer is not left guessing and criterion 8 does not look covered when it is half covered.

### [NOTE] Context summary says four constants; the task and design define five

The summary line reads "`Settings.backup_roots` (`MT_BACKUP_ROOTS`) and four constants", while task 2.1 defines five (`BACKUP_ARCHIVE_FLAG`, `BACKUP_STALE_FLAG`, `BACKUP_HEALTH_LOG`, `HEALTH_BACKUP_CHECKER_QUIET_AFTER`, `BACKUP_ROOTS_SEPARATOR`) and the design's Technical Scope lists the same five. Cosmetic, but it is the count an implementer will sanity-check against.

### [NOTE] Task 6.5 mixes an untestable install-script edit with the function's test suite

6.5 is the heaviest task in the file: it edits `deploy/install-production.sh` step 4 (root-only, not exercisable locally), rewrites its comments/header/`sha256` echo, and then carries a six-item test list for `backup_roots_env_set`. Splitting it — "6.5 Wire `install-production.sh` step 4", "6.6 Test `backup_roots_env_set` against the table" — would keep the test-with pairing 6.4→6.5 intact and give the shell tests their own checkpoint, matching how sections 3 and 4 are arranged. Not blocking; the current grouping is still completable.

### Run Digest

- Response length: 6655 chars
- Response is newline-free: no
- Tool calls made: 44
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 330767
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / cache write / completion / reasoning: 1014367 / 802816 / not computed / 91158 / 86236
- Duration: 698.6 s
- Cost: $0.08 (alias_pricing)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 5
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 5
- Finding-shaped matches — surviving validation: 5
