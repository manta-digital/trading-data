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
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20261005
dateUpdated: 20261005
reviewedSha: 94beba1bfe23301eea332f01cdb3b941ab5c68f0
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
diffTruncated: false
durationSeconds: 43.8
squadronVersion: 0.18.4
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "Provisioning drops the proof database that the slice 226 proof harness still uses"
    location: "scripts/provision_tick_cluster.sh:79-93"
  - id: F002
    severity: concern
    category: safety
    summary: "The cutover arms reconcile after a run that skipped reconcile"
    location: "scripts/cutover_227_tick_backup.py:step_weekly"
  - id: F003
    severity: concern
    category: error-handling
    summary: "Missing arguments silently select production's URL and host rewrite"
    location: "scripts/cron_weekly_backup.sh:98-118"
  - id: F004
    severity: concern
    category: design
    summary: "Structure is parsed out of comment labels"
    location: "scripts/cutover_227_helpers.py:block_lines"
  - id: F005
    severity: concern
    category: security
    summary: "`backup_root` check accepts `..` traversal"
    location: "deploy/lib/backup_clusters.sh:BC_TOKEN_RE"
  - id: F006
    severity: concern
    category: testing
    summary: "Tests assert on script source text"
    location: "test/unit/test_provision_tick_lib.py:177-258"
  - id: F007
    severity: note
    category: testing
    summary: "The placeholder assertion tolerates `@192`"
    location: "test/unit/test_backup_cron_glue.py:TestRenderCron.test_hourly_interval"
  - id: F008
    severity: note
    category: maintainability
    summary: "The base-backup date format is duplicated"
    location: "scripts/cutover_227_tick_backup.py:step_weekly"
  - id: F009
    severity: pass
    category: testing
    summary: "Parser parity, validation and test coverage"
    location: "test/unit/test_backup_clusters.py"
---

# Review: code — slice 227

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Provisioning drops the proof database that the slice 226 proof harness still uses

The diff removes `trading_tick_proof` from the databases the script creates and from the pg_hba admission list. It also removes the `MT_PROOF_226_DB_URL` and `MT_PROOF_226_MAINTENANCE_URL` keys that the script wrote to `.env`. `scripts/proof_226/common.py:21-22`, `scripts/proof_226/cli.py`, `scripts/proof_226/guard.py`, `scripts/proof_226_tick.py` and `src/manta_trading/data/tick/constants.py:288` (`TICK_PROOF_DB_NAME`) still depend on that database and those keys. On a freshly provisioned host the harness has no database, no `.env` keys and no pg_hba rule, so it fails. The new test `test_hba_admits_the_drill_database_and_not_the_proof_one` also asserts `trading_tick_proof` appears nowhere in the script, which locks the removal in. If the harness is meant to be retired, remove it in the same change. If it is not, restore its provisioning or document how it is created now.

### [CONCERN] The cutover arms reconcile after a run that skipped reconcile

`step_weekly` runs the weekly job unarmed. It accepts the log line `=== weekly backup done (unarmed)`, which the test shows follows `reconcile skipped: not armed`. It then touches `RECONCILE-ARMED`. The header comments in `setup-backup.sh` say the arm file is "created by hand after the watched first reconcile". Here the first reconcile, which prunes and can delete data, will run unwatched from cron on its next Sunday. If that is the intended TD7 design, the `setup-backup.sh` wording and the runbook should say the cutover arms it. Otherwise the cutover should leave arming to a person.

### [CONCERN] Missing arguments silently select production's URL and host rewrite

`backup_health_cron.sh` and `cron_nightly_metadata.sh` have the same pre-227 default. When `--url-key` is absent, these scripts fall back to `MT_TIMESCALE_MAINTENANCE_URL`. `cron_weekly_backup.sh` also falls back to the hard-coded `@192.168.1.144:` to `@127.0.0.1:` rewrite. CLAUDE.md prohibits silent fallback values and hard-coded magic defaults. A tick cron line that loses `--url-key` would back up and health-check production without any error. The code comments say this is temporary and goes after the cutover, so the debt is tracked. Remove the fallback in the follow-up slice and give it a deadline.

### [CONCERN] Structure is parsed out of comment labels

The cutover finds cluster blocks by matching the comment line `# cluster <name>` in `/etc/cron.d`, and the tests do the same. A person editing the file can break it silently, which CLAUDE.md warns about ("never use user-accessible labels as logical structure"). The file is generated and `--check` reports drift, so the risk is contained. The cutover could instead re-render per cluster from the table and compare, rather than parsing the installed text.

### [CONCERN] `backup_root` check accepts `..` traversal

The `BC_TOKEN_RE` charset in the bash parser and `_TOKEN_RE` in `scripts/backup_clusters.py` both allow `.` and `/`. The root check is only a `/data/` prefix, so `/data/../etc/x` passes. `setup-backup.sh` runs as root and does `mkdir`, `chown` and `chmod` on that path. The file is checked in, so exposure is low, but the claim in the table's comments that the rows are validated is stronger than what is actually enforced. Reject `..` segments in both parsers and add a case to `_MALFORMED`.

### [CONCERN] Tests assert on script source text

The new tests extract function bodies and assignments from the shell script by regex. They then compare literal source strings such as `'-v with_replication=1 -f - < "$ROLES_SQL"' in body` and `PG_PARENT_DIRS=("$DATA_ROOT" ...)`. These tests break on harmless reformatting and don't exercise the behaviour. `hba_content` is at least executed, which is better. The other checks should be dry-run (`--check`) output assertions, as `test_setup_backup.py` does.

### [NOTE] The placeholder assertion tolerates `@192`

The assertion is `"@" not in text.replace("@192", "")`. Nothing in the render should contain `@192` now that the host rewrite lives in the script, so the exemption looks like a leftover. It weakens the unsubstituted-placeholder check, so tighten it or comment why it is there.

### [NOTE] The base-backup date format is duplicated

`strftime("%Y%m%d")` hard-codes the base backup directory naming that the backup scripts own. Define it once, or read it from the script that creates the directory.

### [PASS] Parser parity, validation and test coverage

The table has a Python and a bash parser. The tests run both on the real `deploy/backup-clusters.conf`, on whitespace variants and on ten malformed cases, and they check line-numbered errors. Both parsers have a parity test. This follows the CLAUDE.md parsing rules. Exception handling in `run_steps` is a documented process-boundary handler (clause c). The new `--url-key` and `--replication-host` options are covered with real-shape URLs, including an `@` in the password. The bash 5.2 `&` replacement trap is both quoted and tested.

### Run Digest

- Response length: 5764 chars
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
- Duration: 43.8 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
