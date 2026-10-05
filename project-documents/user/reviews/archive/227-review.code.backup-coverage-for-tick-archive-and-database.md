---
docType: review
layer: project
reviewType: code
slice: backup-coverage-for-tick-archive-and-database
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/227-slice.backup-coverage-for-tick-archive-and-database.md
aiModel: claude-opus-5-5
status: complete
dateCreated: 20261004
dateUpdated: 20261004
reviewedSha: 85fee397cbbe12ca5b74bb38fb77cf1165b5ba1c
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
diffTruncated: false
durationSeconds: 64.4
squadronVersion: 0.18.4
findings:
  - id: F001
    severity: fail
    category: correctness
    summary: "Cutover re-run always fails step 4: the production-diff guard strips only one side"
    location: "scripts/cutover_227_helpers.py:91-99"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Exceptions outside StepFailed/CalledProcessError skip the report"
    location: "scripts/cutover_227_tick_backup.py#run_steps"
  - id: F003
    severity: concern
    category: design
    summary: "Replication host literal duplicated in the strip helper"
    location: "scripts/cutover_227_helpers.py:87"
  - id: F004
    severity: concern
    category: error-handling
    summary: "Push job's exit status ignored in the offsite step"
    location: "scripts/cutover_227_tick_backup.py#step_offsite"
  - id: F005
    severity: concern
    category: correctness
    summary: "Setup now checks production's /data/backup for manta:manta 775"
    location: "deploy/setup-backup.sh#ensure_dir"
  - id: F006
    severity: note
    category: style
    summary: "Python style nits in the new modules"
    location: "scripts/backup_clusters.py#_row_error"
  - id: F007
    severity: note
    category: security
    summary: "Some table columns are not validated"
    location: "deploy/lib/backup_clusters.sh#backup_clusters_load"
  - id: F008
    severity: note
    category: maintainability
    summary: "Pre-227 compatibility shims need a tracked removal"
    location: "scripts/cron_weekly_backup.sh"
  - id: F009
    severity: pass
    category: testing
    summary: "Parser parity and real-format fixtures"
    location: "test/unit/test_backup_clusters.py"
  - id: F010
    severity: pass
    category: correctness
    summary: "Render safety and the TD3 production-line equivalence"
    location: "deploy/lib/render_cron.sh"
  - id: F011
    severity: pass
    category: security
    summary: "Lifecycle check is read-only and passes credentials through the environment"
    location: "deploy/lib/b2_lifecycle.sh"
  - id: F012
    severity: pass
    category: security
    summary: "REPLICATION grant is opt-in and idempotent"
    location: "scripts/provision_tick_roles.sql"
---

# Review: code — slice 227

**Verdict:** CONCERNS
**Model:** claude-opus-5-5

## Findings

### [FAIL] Cutover re-run always fails step 4: the production-diff guard strips only one side

`production_changes` strips the new arguments only from `new`. `before = production_lines(old)` is used as is.

- **First run:** `old` is the pre-227 file, so this works.
- **Any re-run after step 4 has installed the new cron.d:** `step_check` (`scripts/cutover_227_tick_backup.py`, `ctx.old_cron = CRON_FILE.read_text()`) captures the already-rendered file. `before` then holds the 17/main lines with `--url-key … --replication-host 127.0.0.1 --remote b2:…`, while `after` has those removed. Every main line differs, and `step_apply` stops with "production cron lines changed beyond the new arguments".

The module docstring promises "Recovery is always: fix the cause, re-run the whole script… a re-run reports what is already done". That promise is broken for every failure in steps 5–12 (WAL switch timeout, weekly failure, offsite mismatch, and so on).

Fix: apply `strip_new_args` to both sides, or skip the comparison when `old` already contains `# cluster 17/main` and equals `new`. Add a unit test that calls `production_changes(rendered, rendered, …)` and expects `[]`.

### [CONCERN] Exceptions outside StepFailed/CalledProcessError skip the report

`run_steps` catches only `StepFailed` and `CalledProcessError`. Several ordinary host conditions raise other exceptions, which escape `main()` before `report.write_text`:

| Code | Condition | Exception |
|---|---|---|
| `step_production` | main `backup-health.log` missing | `FileNotFoundError` |
| `step_production` | main `backup-health.log` empty | `IndexError` |
| `tick_locks_held` | non-integer psql output | `ValueError` from `int(...)` |
| `conf_sums` | 17/main row absent | `StopIteration` from `next(...)` |
| `tick_commands` | `CRON_FILE.read_text()` fails | `OSError` |

This breaks the documented contract that "the first failure stops the run and the report is still written". The run_health path already handles a missing log; `step_production` should use the same guard and go through `require`.

The top-level handler should also catch `OSError`, `ValueError`, `IndexError` and `StopIteration`, record them on the step, and still write the report. That counts as a documented process-boundary handler under the exception rule.

### [CONCERN] Replication host literal duplicated in the strip helper

`strip_new_args` hard-codes `" --replication-host 127.0.0.1"`, which repeats the `replication_host` column of `deploy/backup-clusters.conf` for 17/main. The test helper `_NEW_MAIN_ARGS` in `test/unit/test_setup_backup.py` repeats it a third time. Changing the table would make the guard fail with a misleading "production lines changed".

Pass `ctx.main.replication_host` in alongside `url_key` (this is CLAUDE.md's "never scatter comparison values").

### [CONCERN] Push job's exit status ignored in the offsite step

The result of `shell(tick_commands()["push"], step)` is discarded, unlike in `step_metadata` and `step_weekly`. The later `rclone check` usually catches a failed push, but the report never records the push's own exit status or stderr, which makes diagnosis harder. `record()` the result and `require` returncode 0.

Separately, `shell()` runs cron commands with no timeout. If the weekly or push job hangs, the cutover blocks forever with no report.

### [CONCERN] Setup now checks production's /data/backup for manta:manta 775

For 17/main, `ROOT` is `/data/backup`, which is also the host root holding `system/` and restic's root-written stamp, log and lock. `ensure_dir "$C dir-root"` now requires it to be `manta:manta 775`, and apply mode will `chown`/`chmod` it.

The comment says this is "production's shape since 915", but I couldn't verify the live state. If it differs, cutover step 2 (`CHECK_ALLOWED` admits only 17/tick and cron.d items) stops before anything is changed, which is safe but surprising. Confirm the live owner and mode before the cutover.

### [NOTE] Python style nits in the new modules

- **Single-letter names:** `f` (the token list) and `n` (the line number) in `_row_error`/`load_clusters`. The rules allow single letters only in short comprehensions.
- **`assert` for narrowing in runtime code:** `cutover_227_helpers.block_commands` and `cutover_227_tick_backup.run_health`. Asserts disappear under `-O`.
- **Step status strings:** "not run", "PASS" and "FAIL" are scattered literals and would suit a `StrEnum`.
- **Untyped empty lists:** `block = []` and `bad = []` will surface as `list[Unknown]` under strict pyright if `scripts/` is in pyright's include list.

### [NOTE] Some table columns are not validated

`replication_host`, `remote_subpath`, `cluster` and the cron fields are passed straight into rendered root-installed cron lines without validation; only `url_key` and `backup_root` are checked. The table is checked in, so the risk is low. A conservative charset check (e.g. `^[A-Za-z0-9._/-]+$`) in both parsers would close it and keep them in parity.

### [NOTE] Pre-227 compatibility shims need a tracked removal

`PRE227_URL_KEY`, `PRE227_HOST_FROM=@192.168.1.144:`, and the bucket fallback in `cron_nightly_metadata.sh` are silent defaults, kept on purpose so the currently installed cron file keeps working. They are well commented and tested. Make sure a task removes them after the cutover so the implicit fallback doesn't stay.

### [PASS] Parser parity and real-format fixtures

Both parsers are tested against the real `deploy/backup-clusters.conf`, a whitespace variant (tabs, doubled spaces, no trailing newline) and parametrized malformed tables that must name the line. This matches the lenient-parsing and real-fixture rules.

### [PASS] Render safety and the TD3 production-line equivalence

Quoted replacements guard against `patsub_replacement`, and an explicit test asserts every job ends in `2>&1`. The renderer refuses a missing `@CLUSTER_BLOCKS@` line, any unfilled placeholder or a missing `--pgdata` for a row. `test_production_lines_gain_only_the_new_arguments` holds the 17/main and host lines to the pre-227 fixture, line for line.

### [PASS] Lifecycle check is read-only and passes credentials through the environment

The B2 key reaches rclone through `RCLONE_B2_*` environment variables rather than argv. The script only ever calls `backend lifecycle` with no `-o`, which is verified by `test_never_sets_a_rule`. The comment explains why setting a rule would wipe production's rules.

### [PASS] REPLICATION grant is opt-in and idempotent

`ALTER ROLE … REPLICATION` is guarded by `\if :{?with_replication}` and a `NOT EXISTS` check, so test fixtures never confer it. The pg_hba replication line is limited to `tick_migrate` from the client source /32 address.

### Run Digest

- Response length: 8251 chars
- Response is newline-free: no
- Tool calls made: 2
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 64.4 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 12
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 12
- Finding-shaped matches — surviving validation: 12
