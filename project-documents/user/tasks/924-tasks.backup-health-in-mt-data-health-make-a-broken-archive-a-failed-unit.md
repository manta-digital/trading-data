---
docType: tasks
slice: backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit
project: trading-data
lld: user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies:
- 919
- 920
- 227
- 234
interfaces: []
projectState: 'Slice design committed and reviewed (PASS, round 1). `mt data health`
  (919) gathers tick rows before connecting (234). `backup_health_cron.sh` (920) writes
  `ARCHIVE-BROKEN`, `BACKUP-STALE` and `backup-health.log` under each cluster root;
  nothing reads them. `install-production.sh` step 4 never touches an existing `/etc/manta-trading.env`.

  '
dateCreated: 20261022
dateUpdated: 20261022
status: not_started
turns: 17
promptTokens: 987423
cachedTokens: 908019
cacheWriteTokens: 79374
completionTokens: 16214
durationSeconds: 138.9
costUsd: 0.570792
usageRounds: 2
pricedRounds: 2
revision_number: 1
---

# Tasks: Backup Health in `mt data health`

## Context Summary

- Working on slice 924. It makes a broken backup archive a failed
  `mt-health.service`: one verdict check per backup cluster root, in
  `mt data health`.
- Reader only. The cron script, its thresholds and the flag format are not
  changed (design, Excluded).
- It delivers:
  - `cli/commands/health_backup.py` (parse the setting, summarise a flag, judge
    a root, read a root);
  - `Settings.backup_roots` (`MT_BACKUP_ROOTS`) and four constants;
  - `gather_backup` wired into `data_health` before the database connection;
  - `env_set_key` in `deploy/lib/env_add_keys.sh`, and `install-production.sh`
    step 4 writing `MT_BACKUP_ROOTS` from `deploy/backup-clusters.conf`;
  - docs: runbooks 100 and 200, the `mt-health.service` comment, both env
    examples, CHANGELOG.
- Design reference: the slice design (`lld` above). Decisions are cited as D1–D7.
- Effort: 2/5. One task file; one slice branch `924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit`.
- Conventions:
  - Run pytest with `MT_TIMESCALE_TEST_URL` exported from `.env` (quotes stripped).
  - Run mypy with the src, kalshi_support and tests paths in **one** invocation.
  - Format only the files you touched (`ruff format <files>`), then check
    `git diff` for unrelated deletions before each commit.
  - Existing tests for neighbouring code: `test/unit/cli/commands/test_health_tick.py`,
    `test/unit/cli/commands/test_data_health.py`, `test/unit/test_provision_tick_lib.py`,
    `test/unit/test_backup_health.py` (stubbed-checker harness for the cron script).
- Next planned slice: none queued behind this one in the foundation-cleanup series.

---

## 1. Slice Branch

- [ ] **1.1 Create the slice branch**
  - [ ] Read the target: `cf config get git.integration_branch` (empty means `main`).
  - [ ] Confirm the working tree is clean and you are on the target.
  - [ ] `git checkout -b 924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit <target>`
  - [ ] Success: `git branch --show-current` prints the slice branch name.

---

## 2. Constants and the Setting

- [ ] **2.1 Add the constants (D3, D5, D7)** in `src/manta_trading/constants.py`, beside the `HEALTH_*` constants
  - [ ] `BACKUP_ARCHIVE_FLAG = "ARCHIVE-BROKEN"`, `BACKUP_STALE_FLAG = "BACKUP-STALE"`, `BACKUP_HEALTH_LOG = "backup-health.log"`.
  - [ ] `HEALTH_BACKUP_CHECKER_QUIET_AFTER: timedelta = timedelta(hours=2)`. The comment says the cron runs `*/30` (four missed runs) and cites `deploy/cron.d/manta-trading-backup.cluster`.
  - [ ] `BACKUP_ROOTS_SEPARATOR = ","`.
  - [ ] Follow the file's existing style: `#:` or docstring comment under each constant.
  - [ ] Success: `uv run python -c "from manta_trading.constants import BACKUP_ARCHIVE_FLAG, BACKUP_STALE_FLAG, BACKUP_HEALTH_LOG, HEALTH_BACKUP_CHECKER_QUIET_AFTER, BACKUP_ROOTS_SEPARATOR"` exits 0.

- [ ] **2.2 Add `Settings.backup_roots` (D5)** in `src/manta_trading/config/__init__.py`
  - [ ] Field `backup_roots: str | None = None`, with a comment naming `MT_BACKUP_ROOTS` and slice 924. **No validator**: a bad value must fail one health check, not every `mt` command.
  - [ ] Success: `MT_BACKUP_ROOTS=/a,/b` yields `Settings().backup_roots == "/a,/b"`, and an unset variable yields `None`.

- [ ] **2.3 Test the setting**
  - [ ] Add to the existing config tests: set, unset, and a malformed value (`relative/path`) all load without raising.
  - [ ] Success: the new tests pass; the config test module passes.

- [ ] **2.4 Checkpoint**: ruff on the touched files, mypy, then commit `feat: add backup_roots setting and backup health constants`.

---

## 3. Pure Functions in `health_backup.py`

Create `src/manta_trading/cli/commands/health_backup.py`. Module docstring: slice 924, what the module reads, that `read_root` is the only I/O function. Import `HealthCheck`, `CheckSeverity` and `_ago` from `health.py` the way `health_tick.py` does.

- [ ] **3.1 Test fixtures first: real flag bodies**
  - [ ] Create `test/unit/cli/commands/test_health_backup.py` and a fixture directory `test/unit/fixtures/backup_flags/` with these files, each laid out exactly as `backup_health_cron.sh` writes it (title line, `detected: <ISO-8601>`, then the checker output):
    - `archive_broken_2026-10-18.txt` — main root, 2026-10-18 06:00: `archiver_failing`, `unarchived_backlog` (24454157880 bytes, threshold 4294967296), `archive_tmp_leftover`, `FAILED …` and `FLAGS archive=3 stale=…` lines, with `PASS` lines between.
    - `backup_stale_2026-10-18.txt` — `offsite_wal_stale` (2564 min old, limit 180).
    - `archive_wedged_2026-09-06.txt` — the `archive_wedged` body.
  - [ ] Build each file from `scripts/check_backup_health.sh`'s exact output format, the numbers above, and the check names and wording in runbook 200 (its check table and the 2026-09-06 drill row). Do not read production journals. Do not invent check names.
  - [ ] Success: three fixture files exist; each starts with a title line and a `detected:` line.

- [ ] **3.2 `parse_backup_roots(raw) -> list[str] | str` (D5)**
  - [ ] Split on `BACKUP_ROOTS_SEPARATOR`, strip whitespace, drop empty segments, drop duplicates (keep first-seen order).
  - [ ] Each entry must be an absolute path with no `..` segment (looser than the cluster table by design; comment why).
  - [ ] Return the list, or an error string. Unset (`None`), empty and all-blank give an error saying `MT_BACKUP_ROOTS is not set — no backup cluster is judged`. An invalid entry gives an error that names that entry.
  - [ ] Success: callable with no I/O.

- [ ] **3.3 Test `parse_backup_roots`**
  - [ ] Cases: two roots; spaces and a trailing comma; duplicates collapse; `None`; `""`; `" , "`; relative path (error names it); `/data/../etc` (error names it); one bad entry among good ones gives an error, not a partial list.
  - [ ] Success: all pass.

- [ ] **3.4 `summarise_flag(name, text) -> str` (D2)**
  - [ ] Output: `<name> since <detected>: <check>: <text>; <check>: <text>`, built from each line starting with `FAIL `, split into check name and text at the first `:`.
  - [ ] Lenient parsing: strip `\r` and surrounding whitespace; find the `detected:` line by prefix anywhere in the file.
  - [ ] No `detected:` line: `<name> (no detected time): …`, rest unchanged.
  - [ ] Present but no `FAIL ` line: the detail is the first non-empty line, or `(empty)` for an empty file.
  - [ ] Success: callable with no I/O.

- [ ] **3.5 Test `summarise_flag`**
  - [ ] Real fixtures from 3.1: the 10-18 archive body names `archiver_failing`, `unarchived_backlog` and `archive_tmp_leftover` with their numbers and the detected time; the stale body names `offsite_wal_stale`; the wedged body names `archive_wedged`.
  - [ ] Variations: CRLF line endings; leading spaces; `detected:` not on line 2; no `detected:` line; hand-written flag with no `FAIL` line; empty file.
  - [ ] Success: all pass.

- [ ] **3.6 `RootObservation` and `check_backup_root(obs, now, threshold) -> HealthCheck` (D1, D3)**
  - [ ] `RootObservation` is a frozen dataclass: `root`, `read_error: str | None`, `archive_flag: str | None`, `stale_flag: str | None`, `log_mtime: datetime | None`. Flag fields are the file text, `None` when absent.
  - [ ] Check name `backup <root>`; severity VERDICT.
  - [ ] Problems, in this order, joined with ` | `: unreadable root (`read_error`); archive flag (via `summarise_flag`); stale flag; checker quiet.
  - [ ] Checker quiet: log absent gives `no backup-health.log — backup_health_cron.sh has never run here`; log older than threshold gives `checker last ran <age> ago; limit <n> h — backup_health_cron.sh is not running`. Use `_ago` for the age and print the limit in hours.
  - [ ] Healthy detail: `no flags; checker ran <age> ago; limit <n> h`.
  - [ ] An unreadable root reports the read error and skips the flag and liveness judgement (nothing was read).
  - [ ] Success: callable with no I/O; passes `now` explicitly.

- [ ] **3.7 Test `check_backup_root`**
  - [ ] Healthy; archive flag only; stale flag only; both flags (detail has both, archive first); both flags plus a quiet checker (three problems, in order); log absent; log exactly at the threshold (still healthy) and one second past it (quiet); unreadable root.
  - [ ] Assert the 2026-10-18 shape produces a detail starting `ARCHIVE-BROKEN since 2026-10-18T06:00` and containing ` | BACKUP-STALE since`.
  - [ ] Success: all pass.

- [ ] **3.8 Checkpoint**: ruff on touched files, mypy, commit `feat: add backup health verdict functions`.

---

## 4. Reading a Root (the I/O function)

- [ ] **4.1 `read_root(root) -> RootObservation` (D1, Data Flow step 4)**
  - [ ] Check the root is a readable directory; if not, return an observation with `read_error = "cannot read <root>: <strerror>"` and nothing else read.
  - [ ] Read each flag file as text (`None` if `FileNotFoundError`); `stat` the log for its modification time as a UTC-aware `datetime` (`None` if absent).
  - [ ] Any other `OSError` is captured into `read_error` as `cannot read <path>: <strerror>` and never propagates. Add the comment the design asks for: swallowing is correct because the error is reported as a failed verdict.
  - [ ] Success: never raises; uses the three constants from 2.1, no literal file names.

- [ ] **4.2 Test `read_root`** (real `tmp_path` layouts)
  - [ ] Empty root; root with each flag; root with both and a log; missing root (`read_error` set); root path is a file; a flag file with mode 000 (skip if running as root); a flag file that is a directory (read error captured).
  - [ ] Success: all pass; no test depends on the process user except the mode-000 case, which skips cleanly.

- [ ] **4.3 Writer-to-reader test (Technical Requirements)**
  - [ ] Using the stub harness pattern in `test/unit/test_backup_health.py` (stubbed `check_archive_health.sh`/`psql` on `PATH`), run the real `scripts/backup_health_cron.sh` into a temp root three ways: all-pass, a failing archive check, a failing stale check.
  - [ ] Read each root with `read_root` and judge it with `check_backup_root`. Assert: healthy root passes; the archive case fails with the stubbed `FAIL` name in the detail; the stale case likewise.
  - [ ] Success: passes, proving the reader consumes the writer's real output. If the harness is not importable from the new test module, move the shared helper into `test/unit/conftest.py` or a small support module rather than copying it.

- [ ] **4.4 D7 parity test**
  - [ ] Read `deploy/cron.d/manta-trading-backup.cluster`; find the `backup_health_cron.sh` line.
  - [ ] Assert it contains `--flag @CLUSTER_ROOT@/<BACKUP_ARCHIVE_FLAG>`, `--stale-flag @CLUSTER_ROOT@/<BACKUP_STALE_FLAG>` and `--log @CLUSTER_ROOT@/<BACKUP_HEALTH_LOG>`, using the constants.
  - [ ] Success: passes; renaming a constant makes it fail (verify once by hand, then revert).

- [ ] **4.5 Checkpoint**: ruff, mypy, commit `feat: read backup roots and test against the cron writer`.

---

## 5. Wire into `mt data health`

- [ ] **5.1 `gather_backup(settings, *, now) -> list[HealthCheck]` (D5, D6)**
  - [ ] Parse `settings.backup_roots`. On an error string, return one failing VERDICT check named `backup` with that string as detail.
  - [ ] Otherwise return `check_backup_root(read_root(root), now, HEALTH_BACKUP_CHECKER_QUIET_AFTER)` for each root, in setting order.
  - [ ] Success: no database access; one check per root.

- [ ] **5.2 Call it from `data_health`** in `health.py`
  - [ ] Use the same deferred import as `gather_tick`, immediately beside it and **before** `psycopg.connect`.
  - [ ] Include the backup checks in `checks` (counted in the verdict and in `--json`).
  - [ ] In the `OperationalError`/`QueryCanceled` branch, print the backup rows with the tick rows before the exit-2 error (success criterion 7). Print them once, in one `render_lines` call.
  - [ ] Update `data_health`'s docstring list of checks.
  - [ ] Success: `health.py` stays under ~330 lines (`wc -l`); if it would exceed, move the combined pre-connect gathering into `health_backup.py` rather than growing `health.py`.

- [ ] **5.3 Test the wiring** in `test/unit/cli/commands/test_data_health.py`, following its existing tick-row tests
  - [ ] Healthy roots: one `OK backup <root>` row each, exit 0 when the rest pass.
  - [ ] A flag present: `FAIL backup <root>` row, exit 1; `--json` shows the check in `checks[]` with `severity: "verdict"`.
  - [ ] `MT_BACKUP_ROOTS` unset: one `FAIL backup` row, exit 1.
  - [ ] Database unreachable (`OperationalError`): backup rows print before the error, exit 2.
  - [ ] Success: new tests pass; existing `test_data_health.py`, `test_health_tick.py` and `test_health_accounting_pass_runs.py` pass unchanged.

- [ ] **5.4 Checkpoint**: ruff, mypy, unit tier for `test/unit/cli`, commit `feat: judge backup roots in mt data health`.

---

## 6. `env_set_key` and the Install Step (D4)

- [ ] **6.1 Extract the shared atomic write in `deploy/lib/env_add_keys.sh`**
  - [ ] Move the common tail into a private helper (name it with a leading underscore, e.g. `_env_commit`): create the 0600 temp file in the target's directory, copy the current content, ensure a trailing newline, and after the caller edits it, `chmod 600`, `chown <owner>:`, `mv` over the target; trap cleanup as today.
  - [ ] `env_add_keys` calls the helper. Its behaviour, output (`<count>`) and arguments are unchanged.
  - [ ] Success: all existing `env_add_keys` tests in `test/unit/test_provision_tick_lib.py` pass **unchanged**.

- [ ] **6.2 Add `env_set_key <file> <owner> KEY=value`**
  - [ ] Replaces an existing `^KEY=` line in place, or appends one if absent. Other lines are byte-identical.
  - [ ] Prints `unchanged` when the value already matches, else `set`. Never prints the value.
  - [ ] Keeps the mode/owner enforcement of `env_add_keys` (0600 temp, chown, `mv`); the install script re-asserts `0640`.
  - [ ] Values containing `&`, `\`, `/` or `|` must survive: do not build the replacement with `sed` substitution of the raw value; rebuild the file line by line (e.g. `while read` or `awk` with the value passed through the environment).
  - [ ] Update the file header comment to describe both functions.
  - [ ] Success: `shellcheck deploy/lib/env_add_keys.sh` is clean (`~/.local/bin/shellcheck`).

- [ ] **6.3 Test `env_set_key`** in `test/unit/test_provision_tick_lib.py`, beside the `env_add_keys` tests (use the `_bash` helper)
  - [ ] Replace (value changed, output `set`); append when absent (`set`); identical value (`unchanged`, file byte-identical); other lines, including a credential line, byte-identical and in order; no trailing newline in the original; special characters in the value; temp file left behind on failure is none (target is an unwritable dir); the value never appears on stdout or stderr.
  - [ ] Success: new and existing tests in the module pass.

- [ ] **6.4 `backup_roots_env_set` in `deploy/lib/backup_roots_env.sh`** (new file; the install step's logic lives here so it can be tested without root)
  - [ ] `backup_roots_env_set <env-file> <owner> <group> <conf>`: source `backup_clusters.sh` and `env_add_keys.sh` from the same directory; call `backup_clusters_load <conf>` and return non-zero printing the table's error if it fails, **before** the env file is opened for writing.
  - [ ] Join `BC_ROOT` with `,` using one shell variable for the separator, defined at the top of the file with a note that it must match `BACKUP_ROOTS_SEPARATOR` in `constants.py` (task 6.5 holds them together).
  - [ ] Call `env_set_key <env-file> <owner> MT_BACKUP_ROOTS=<joined>` and relay its `set`/`unchanged` output; on failure return non-zero.
  - [ ] Then set the file to `<owner>:<group>` mode `0640` (success criterion 8), on every run including `unchanged`.
  - [ ] Success: `shellcheck` and `bash -n` clean.

- [ ] **6.5 Wire the install script and test the function**
  - [ ] In `deploy/install-production.sh` step 4, after the existing create-or-leave logic: source the lib from `${INSTALL_DIR}/deploy/lib/`, call `backup_roots_env_set "${ENV_FILE}" root "${SERVICE_USER}" "${INSTALL_DIR}/deploy/backup-clusters.conf"`; on non-zero `die` (table error: name it; write failure: `could not set MT_BACKUP_ROOTS in /etc/manta-trading.env`). Print `MT_BACKUP_ROOTS set` or `MT_BACKUP_ROOTS unchanged`.
  - [ ] Replace the comment "Never overwrite, never merge" with "never touch a key you do not manage; this script manages exactly `MT_BACKUP_ROOTS`" (D4); update the script header and the `sha256` echo so they still make sense. Keep the existing `chown`/`chmod 0640` lines.
  - [ ] Test `backup_roots_env_set` in `test/unit/test_provision_tick_lib.py` (or a sibling module), against the real `deploy/backup-clusters.conf` and a temp env file owned by the current user:
    - result line is `MT_BACKUP_ROOTS=/data/backup,/data/backup/17-tick`;
    - every other line byte-identical;
    - a second run prints `unchanged`;
    - **mode is `0640` and owner/group are the ones passed, after the first run and after the `unchanged` run** (criterion 8);
    - malformed table (temp copy with a bad row) returns non-zero and the env file is byte-identical;
    - the joined value parses cleanly through `parse_backup_roots` (separator parity).
  - [ ] Success: all pass; `shellcheck deploy/install-production.sh` and `bash -n` clean.

- [ ] **6.6 Checkpoint**: shellcheck, ruff, the unit tier for `test/unit/test_provision_tick_lib.py`, commit `feat: install writes MT_BACKUP_ROOTS from the cluster table`.

---

## 7. Docs

- [ ] **7.1 Runbook 200** (`project-documents/user/runbooks/200-backup-and-restore.md`)
  - [ ] In the flag section (around the `ARCHIVE-BROKEN` bullet), say the flags are now read by `mt data health`: a flag, a quiet checker or an unreadable root fails `mt-health.service`. Name the row format `backup <root>`.
  - [ ] Add how to clear it: fix the cause named in the detail; the cron removes the flag within 30 minutes; the next hourly health run goes green.
  - [ ] Success: the section is accurate against the design's example output.

- [ ] **7.2 Runbook 100** (`project-documents/user/runbooks/100-*.md`, "Is anything wrong? — the health check")
  - [ ] Add the backup rows to the list of what the check covers, and `MT_BACKUP_ROOTS` to the environment-file notes (written by `install-production.sh`).
  - [ ] Success: the check list matches `data_health`'s docstring.

- [ ] **7.3 Unit comment and env examples**
  - [ ] `deploy/systemd/mt-health.service`: extend the comment above `ExecStart` to include backup flags and checker liveness.
  - [ ] `.env_sample` and `deploy/manta-trading.env.example`: document `MT_BACKUP_ROOTS` (comma-separated absolute roots; unset is a health failure; the install script manages it in `/etc/manta-trading.env`). Leave it commented out in the examples, with no example path value that could be mistaken for a default.
  - [ ] Success: `systemd-analyze verify deploy/systemd/mt-health.service` reports no new warnings (unit comments only).

- [ ] **7.4 CHANGELOG**
  - [ ] Under `## [Unreleased]`, add an `### Added` entry in the user's voice: a broken backup archive, a stale offsite push, a dead checker or an unreadable root now fails the hourly health check; `MT_BACKUP_ROOTS` is written by the install script. No version bump (patch-level; release is the PM's call).
  - [ ] Success: entry present under Unreleased only.

- [ ] **7.5 Checkpoint**: commit `docs: document backup rows in mt data health`.

---

## 8. Final Validation

- [ ] **8.1 Full local gate**
  - [ ] `ruff format --check` and `ruff check` on the touched files; mypy per the kalshi_support invocation note; shellcheck on both shell files.
  - [ ] Unit tier passes (`uv run pytest test/unit`, with `MT_TIMESCALE_TEST_URL` exported). Pre-existing failures listed in the migration 051/052 note are not regressions; confirm any failure also occurs at the slice base before dismissing it.
  - [ ] `wc -l src/manta_trading/cli/commands/health.py` is under ~330 and `health_backup.py` is under ~300.
  - [ ] `git diff <target>... --stat` touches only files named in the design's Technical Scope; no unrelated deletions from formatting.
  - [ ] Success: all of the above hold.

- [ ] **8.2 Walk the design's success criteria**
  - [ ] Run the Verification Walkthrough steps 3–6 (scratch root, flag, dead checker, missing setting) with `uv run mt data health` against a `mktemp -d` root. They need no production access. Capture the output in the commit message or a note under the slice's task file.
  - [ ] Check each of success criteria 1–7 against a test from tasks 3–5 and criterion 8 (including mode and owner) against task 6.5; list any criterion without a test.
  - [ ] Success: every criterion maps to a passing test or a walkthrough output. Walkthrough steps 1–2 and the design's manta9000 integration requirement need the released tag installed on the host; they are deferred to issue #54.

- [ ] **8.3 Final commit** of any leftover changes: `chore: finalize slice 924` (skip if the tree is clean).
