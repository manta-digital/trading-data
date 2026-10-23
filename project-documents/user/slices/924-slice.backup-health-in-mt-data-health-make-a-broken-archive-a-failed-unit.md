---
docType: slice-design
slice: backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies:
- 919
- 920
- 227
interfaces: []
dateCreated: 20261022
dateUpdated: 20261022
status: complete
turns: 32
promptTokens: 2471030
cachedTokens: 2364393
cacheWriteTokens: 106583
completionTokens: 24019
durationSeconds: 233.0
costUsd: 1.806469
usageRounds: 2
pricedRounds: 2
revision_number: 1
---

# Slice Design: Backup Health in `mt data health` — Make a Broken Archive a Failed Unit

## Overview

From October 16 to 19, 2026, the backup tier was broken and the system had already detected it. `scripts/backup_health_cron.sh`
runs every half hour per cluster. It wrote `BACKUP-STALE` from Oct 16, because the hourly offsite WAL push had stopped, and
`/data/backup/ARCHIVE-BROKEN` on Oct 18. On Oct 18 at 06:00 the main cluster's flag read `archiver_failing`,
`unarchived_backlog` (24.4 GB of WAL waiting) and `archive_tmp_leftover`. Nothing read either file. A flag file that
nobody is obliged to read is the same kind of silent failure slice 919 removed for data freshness.

`mt data health`, run hourly by `mt-health.timer`, is the established answer to "does anything need a human?" A failing
check there is a failed unit, which shows in `mt-run status` and `systemctl --failed`. This slice adds one verdict check per
backup cluster root to that command. The check fails when:

- `ARCHIVE-BROKEN` or `BACKUP-STALE` exists. The detail line shows the flag's own `FAIL` lines, which name the failing tests.
- the flag-writing cron has gone quiet. A checker that has stopped running must not read as healthy.
- the list of roots is unset or malformed, or a root cannot be read.

There is no new CLI command and no new unit. The list of roots comes from one new setting, `MT_BACKUP_ROOTS`. The install
script writes that setting from `deploy/backup-clusters.conf`, so the cluster table remains the only place roots are defined.

## Value

- **Operator:** a broken archive becomes a failed `mt-health.service` within about an hour of the flag appearing. It is
  reported through the same channel as every other breach, so nobody needs a habit of reading `/data/backup`.
- **Safety:** the WAL archive is the restore path. Three days of an unnoticed broken archive is three days of exposure
  to a restore that cannot reach the present. This slice closes that gap for every cluster in the table, including
  clusters added later.
- **Integrity of the signal:** a dead checker, a missing setting and an unreadable root each fail explicitly. Health can
  only pass after something actually looked.

## Technical Scope

**Included**

- New module `src/manta_trading/cli/commands/health_backup.py`:
  - pure functions that parse the setting, summarise a flag file and judge one root;
  - one I/O function that reads each root's two flag files and the checker log's modification time.
- `health.py`: gather the backup checks before the database connection is opened, as the tick rows already are. They are
  counted in the verdict, and they still print when the primary database is down.
- `config/__init__.py`: new field `backup_roots: str | None` (`MT_BACKUP_ROOTS`).
- `constants.py`:
  - `HEALTH_BACKUP_CHECKER_QUIET_AFTER`;
  - the three file names the check reads (`ARCHIVE-BROKEN`, `BACKUP-STALE`, `backup-health.log`);
  - the setting's separator.
- `deploy/install-production.sh`: in its environment-file step, set `MT_BACKUP_ROOTS` from the pinned checkout's
  `deploy/backup-clusters.conf`. Every other key is left untouched.
- `deploy/lib/env_add_keys.sh`: a sibling function `env_set_key` that replaces one key's value, or appends it if absent.
  It shares the file's existing atomic write (see D4) rather than duplicating it.
- Tests:
  - unit tests on real flag-file contents;
  - a parity test that holds the Python file names to the cron template;
  - a shell test for `env_set_key`.
- Docs:
  - runbook 200: the flags are now read by `mt data health`;
  - runbook 100: the health check list;
  - the `mt-health.service` comment;
  - `.env_sample` and `deploy/manta-trading.env.example`;
  - CHANGELOG.

**Excluded**

- Any change to `check_backup_health.sh`, `backup_health_cron.sh`, its thresholds, or the flag format. This slice is a
  reader only.
- Pushing alerts to a person (mail, chat). Delivery remains a failed unit.
- An API endpoint. Health is not served by the API today, and this slice adds no new data that a client would query.
  Backup state belongs to the operator surface. If the API gains a health route, it will pick these checks up from
  `gather`, as it would any other check.
- Restic or system-backup checks of their own. `system_backup_stale` already reaches each cluster's `BACKUP-STALE` file.

## Dependencies

### Prerequisites

- **919**: `mt data health`, `HealthCheck`, `mt-health.timer`/`.service`. These are complete.
- **920**: `backup_health_cron.sh`, which writes `ARCHIVE-BROKEN`, `BACKUP-STALE` and `backup-health.log` under each
  cluster root. This is complete.
- **227**: `deploy/backup-clusters.conf` and its two parsers (`deploy/lib/backup_clusters.sh`,
  `scripts/backup_clusters.py`). This is complete.
- **234**: the "gather before connecting" pattern for rows that must print when the primary database is down
  (`health_tick.gather_tick`). This is complete.

### Interfaces Required

- **The flag files.** `backup_health_cron.sh` writes each flag file as:
  - line 1: the title;
  - line 2: `detected: <ISO-8601>`;
  - then the full checker output: `PASS`/`FAIL ...` lines, an optional `FAILED archive=… stale=…` line and a final
    `FLAGS archive=<n> stale=<m>` line.

  The file is removed when its count returns to 0.
- **The checker log.** `backup_health_cron.sh` appends one line to `<root>/backup-health.log` on every run, whatever the
  result. Its modification time is therefore the time of the last run.
- **File access.** Roots are `drwxrwxr-x manta`. The flag files and the log are `rw-rw-r--`. The service user
  `manta-trading` can read them, and `ProtectSystem=full`/`ProtectHome=true` do not cover `/data`.

## Architecture

### Component Structure

```
mt-health.timer ─▶ mt-health.service ─▶ mt data health (health.py: data_health)
                                          ├─ gather_tick(settings)       (234; informational + retention)
                                          ├─ gather_backup(settings)     (NEW, health_backup.py; no DB)
                                          │    ├─ parse_backup_roots(raw)       pure
                                          │    ├─ read_root(root) → RootObservation   I/O: 2 reads + 1 stat
                                          │    └─ check_backup_root(obs, now, threshold)   pure
                                          └─ gather(conn, settings)      (919; DB checks)

install-production.sh ── step 4 ──▶ backup_clusters_load($INSTALL_DIR/deploy/backup-clusters.conf)
                                    └─ env_set_key /etc/manta-trading.env MT_BACKUP_ROOTS=<root>,<root>
```

`health_backup.py` imports `HealthCheck` from `health.py`, as `health_tick.py` does. `health.py` imports
`gather_backup` inside `data_health`, using the same deferred import `health_tick` uses, to avoid the cycle.

### Data Flow

1. `install-production.sh --ref <tag>` loads the cluster table from the pinned checkout. It joins the `backup_root`
   column with `,` and sets `MT_BACKUP_ROOTS` in `/etc/manta-trading.env`. A malformed table stops the script before the
   environment file is touched.
2. systemd loads the environment file. `Settings.backup_roots` holds the raw string.
3. `gather_backup` parses the string:
   - unset or empty gives one failing check, `backup`;
   - a relative or otherwise invalid entry gives one failing check that names the entry;
   - otherwise, there is one observation per root.
4. For each root, `read_root` produces a `RootObservation`. It records:
   - whether the root is a readable directory;
   - the text of each flag file, or `None` if the file is absent;
   - the checker log's modification time, or `None` if the log is absent.

   Any `OSError` on a read is captured into the observation as its message. It never propagates.
5. `check_backup_root` turns the observation into one `HealthCheck` named `backup <root>`, with severity VERDICT.
6. The checks join the rest. `is_healthy` and `verdict_line` count them like any other check. A failure gives exit 1, and
   the unit fails.

### State Management

This slice adds no state. It reads three files per root. The only new persistent item is the `MT_BACKUP_ROOTS` line in
`/etc/manta-trading.env`, which every install run re-derives from the table.

## Technical Decisions

### D1 — One verdict check per root, with all of its problems in one line
Status: decided
- Name: `backup <root>`, for example `backup /data/backup/17-tick`. The root path is the label because it is what the
  operator `cd`s to, and it needs no second mapping.
- The check fails if any of these holds:
  - the root is unreadable;
  - `ARCHIVE-BROKEN` is present;
  - `BACKUP-STALE` is present;
  - the checker is quiet.

  The detail joins every problem found, in that order, with ` | `. A dead checker that left a stale flag behind is
  therefore reported as both.
- Considered and rejected:
  - Separate `backup flags` and `backup checker` checks per root. That doubles the rows without adding a decision, because
    either way the operator opens the root.
  - One check for all roots. That hides which cluster is broken.

### D2 — The detail shows the flag's FAIL lines, not the whole file
Status: decided
- The flag file is often 5–10 lines: title, detected, `PASS` lines, the `FAILED` and `FLAGS` lines.
- The detail is `<FLAG> since <detected>: <name>: <text>; <name>: <text>`, built from each line that starts with `FAIL `.
  This is the checker's own wording, which names the test and the number.
- Lenient parsing:
  - line endings and surrounding whitespace are ignored;
  - the `detected:` line is found by prefix anywhere in the file.
- If there is no `detected:` line, the detail reads `<FLAG> (no detected time): …`, and the rest is unchanged.
- If a flag file is present but holds no `FAIL ` line (a hand-written or truncated flag), the detail is its first
  non-empty line, or `(empty)`, and the check still fails. A flag's presence decides the verdict; its content is only
  the explanation.

### D3 — Checker liveness is the log's modification time
Status: decided
- `backup_health_cron.sh` appends to `--log` on every run before it acts on the flags, including a `cannot_check` run.
  The log's modification time is the time of the last run.
- Quiet when the time is older than `HEALTH_BACKUP_CHECKER_QUIET_AFTER = timedelta(hours=2)`. The cron runs `*/30`, so
  this is four missed runs. The constant's comment cites the cron template line.
- An absent log fails: "the checker has never run here".
- Considered and rejected: adding a stamp file to the cron script. The log already does that job, and this slice does
  not change the writer.

### D4 — `MT_BACKUP_ROOTS` is written by `install-production.sh`, not `setup-backup.sh`
Status: decided
- **Deviation from the original slice plan entry, which named `setup-backup.sh`; the entry is now amended to match.**
  - `setup-backup.sh --env-file` is the dev checkout's `.env`, which holds the backup credentials. The health unit reads
    `/etc/manta-trading.env`, a different file.
  - `setup-backup.sh` is a root step outside the operator's sudoers list. `install-production.sh` is inside it
    (`MT_INSTALL`).
  - With the install script writing the key, a release install delivers the setting with no PM host step. The value is
    derived from the cluster table at the pinned ref, so the table stays the single source.
- `install-production.sh`'s environment step changes from "never merge" to "never touch a key you do not manage". It
  manages exactly one key, `MT_BACKUP_ROOTS`. The PM's credentials still survive every run.
- `env_set_key <file> <owner> KEY=value` lives in `deploy/lib/env_add_keys.sh` beside `env_add_keys`. The tail of
  `env_add_keys` moves into a private helper both functions call: create the 0600 temp file in the same directory, chown
  it, `mv` it over the target. `env_set_key` differs only in the content it writes: it replaces an existing `^KEY=` line
  or appends one. It prints `unchanged` or `set` and never prints the value. `env_add_keys`'s behaviour and its existing
  tests are unchanged.

  Install step 4 then re-asserts `root:manta-trading 0640`, as it already does.
- **Failure behaviour.** `install-production.sh` runs under `set -euo pipefail`, and the new work happens inside step 4,
  before the units are copied (step 5) and systemd is reloaded (step 6):
  - an unreadable or malformed cluster table: `die` naming the table's error. The environment file is not opened.
  - a failed `env_set_key` (temp file, chown or `mv`): `die "could not set MT_BACKUP_ROOTS in /etc/manta-trading.env"`.
    The `mv` is the only write, so the old file is whole.

  In both cases the script stops before steps 5 and 6. The new venv is built, but the old units are still the ones
  loaded. Recovery is the script's existing rule: fix the cause and re-run. If the key was never written, the health
  check fails `backup … MT_BACKUP_ROOTS is not set` (D5) rather than passing.
- Considered and rejected: having the service read `deploy/backup-clusters.conf` directly. `/opt/manta-trading` is a full
  checkout, so the file is present. However, that couples the installed package to a deploy-tree path, which is a
  surprise for any non-`/opt` run (the dev checkout, tests). An explicit setting is consistent with every other path the
  service reads.

### D5 — The setting is a raw string, parsed by the check
Status: decided
- `backup_roots: str | None` in `Settings`, with no validator. A validation error in `Settings` would stop every `mt`
  command, including the acquisition passes. A bad value must fail one health check, not production.
- `parse_backup_roots` does the parsing:
  - it splits on `,` (`BACKUP_ROOTS_SEPARATOR`);
  - it strips whitespace and drops empty segments;
  - it requires each entry to be an absolute path with no `..` segment. This is looser than the table, which also requires `/data/` and a restricted
    character set: the setting is only read here, never rendered into a root cron line;
  - it drops duplicates.

  It returns either the roots or an error string.
- Unset, empty or invalid gives one failing check named `backup`, whose detail says what is wrong and that no cluster
  is being judged.

### D6 — Backup checks are counted verdicts, gathered before the database
Status: decided
- They are `CheckSeverity.VERDICT`. The whole point of the slice is that a broken archive fails the unit.
- They are gathered beside `gather_tick`, before `psycopg.connect`. When the primary database is unreachable, the run
  still exits 2, but the backup rows print first, as the tick rows do. During an outage, the archive is exactly what
  the operator needs to see.

### D7 — File names are constants, held to the cron template by a test
Status: decided
- `constants.py` gains:
  - `BACKUP_ARCHIVE_FLAG = "ARCHIVE-BROKEN"`;
  - `BACKUP_STALE_FLAG = "BACKUP-STALE"`;
  - `BACKUP_HEALTH_LOG = "backup-health.log"`.
- The cron template renders these names into the `backup_health_cron.sh` line. A parity test reads
  `deploy/cron.d/manta-trading-backup.cluster` and asserts `--flag @CLUSTER_ROOT@/<ARCHIVE>`, `--stale-flag
  @CLUSTER_ROOT@/<STALE>` and `--log @CLUSTER_ROOT@/<LOG>` on that line. If either side is renamed, the test fails.

### Patterns and Conventions

- Pure verdict functions take values and return a `HealthCheck`. `read_root` is the only I/O function, following 919's
  rule.
- Times are formatted with the existing `_ago` helper. Limits are printed in hours, matching the Kalshi rows.
- An `OSError` is not swallowed silently. It becomes the failing detail (`cannot read <path>: <strerror>`). Swallowing is
  correct here, and the code comment says so: the error is reported as a failed verdict, so it is not lost.

## Implementation Details

### API Contracts

There is no new command. Here is the new output of `mt data health`, abbreviated.

Healthy:

```
OK   backup /data/backup          no flags; checker ran 0.3 h ago; limit 2 h
OK   backup /data/backup/17-tick  no flags; checker ran 0.3 h ago; limit 2 h
```

On 2026-10-18 at 06:50 local time, the main root would have read:

```
FAIL backup /data/backup          ARCHIVE-BROKEN since 2026-10-18T06:00:01-06:00: archiver_failing: the most recent archive attempt failed (see pg_stat_archiver.last_failed_wal); unarchived_backlog: 24454157880 bytes of WAL awaiting archive (threshold 4294967296); archive_tmp_leftover: … | BACKUP-STALE since …: offsite_wal_stale: push stamp /data/backup/wal-offsite.stamp is 2564 min old (limit 180 min); checker ran 0.8 h ago; limit 2 h
```

Explicit failures:

```
FAIL backup                       MT_BACKUP_ROOTS is not set — no backup cluster is judged
FAIL backup /data/backup          checker last ran 3.2 d ago; limit 2 h — backup_health_cron.sh is not running
FAIL backup /data/backup/17-tick  cannot read /data/backup/17-tick: [Errno 13] Permission denied
```

In `--json`, each check appears in `checks[]` with `severity: "verdict"`. The JSON shape is unchanged.

### Database / Storage Schema

None.

## Integration Points

### Provides to Other Slices

- Backup state in the health verdict. This means it also appears in `mt data overview`'s recorded health detail
  (slice 922 reads `verdict_line`), and in `pass_runs`.
- `env_set_key` (in `env_add_keys.sh`), for any later managed key in the service environment file.

### Consumes from Other Slices

- **920's flag files and log, read-only.** If the writer changes its file names, the D7 parity test fails. If it changes
  the flag body, D2's fallback still fails the check, with a less precise detail.
- **227's table**, through `deploy/lib/backup_clusters.sh` in the install script. A malformed table stops the install
  before the environment is written.
- **919/234's health framework**: `HealthCheck`, `CheckSeverity`, `_ago`, and the gather-before-connect ordering.

## Success Criteria

### Functional Requirements

1. With `MT_BACKUP_ROOTS` set to both roots and both healthy, `mt data health` prints one `OK backup <root>` row per root.
   Exit status is unaffected.
2. With `ARCHIVE-BROKEN` or `BACKUP-STALE` present under a root, that root's row is `FAIL`. The detail names the flag, its
   `detected` time, and every `FAIL` line's check name and text. The command exits 1.
3. A flag file with no `FAIL` line still fails the row, and shows its first non-empty line.
4. With `backup-health.log` absent, or older than `HEALTH_BACKUP_CHECKER_QUIET_AFTER`, the row fails and says the checker
   is not running.
5. If `MT_BACKUP_ROOTS` is unset, empty, holds a relative path, or holds a `..` segment, there is one `FAIL backup` row
   naming the problem.
6. An unreadable or missing root fails its row with the OS error. The command does not crash.
7. When the primary database is unreachable, the backup rows print before the exit-2 error.
8. `sudo deploy/install-production.sh --ref <tag>` sets `MT_BACKUP_ROOTS=/data/backup,/data/backup/17-tick` in
   `/etc/manta-trading.env`:
   - every other line is byte-identical;
   - the mode and owner stay `0640 root:manta-trading`;
   - a second run reports `unchanged`.

### Technical Requirements

- Unit tests for `parse_backup_roots`, the flag summary and `check_backup_root`, with flag fixtures built from the real
  production text:
  - the 2026-10-18 06:00 `ARCHIVE-BROKEN`/`BACKUP-STALE` body;
  - the 2026-09-06 `archive_wedged` body;

  each laid out exactly as `backup_health_cron.sh` writes it (title, `detected:`, output lines).
- A test that runs `backup_health_cron.sh` with stubbed checkers into a temp root, then reads that root with `read_root`.
  This proves the reader consumes the writer's real output, not a hand-written imitation.
- D7 parity test against the cron template.
- Shell test for `env_set_key` beside the existing `env_add_keys` tests in `test/unit/test_provision_tick_lib.py`: replace, append, unchanged, preserve other
  lines/owner/mode, value never printed. `env_add_keys`'s existing tests pass unchanged.
  `shellcheck` clean.
- `health.py` stays under ~330 lines. The new code lives in `health_backup.py`.
- ruff, mypy (per the kalshi_support invocation note) and the unit tier pass.

### Integration Requirements

- `mt-health.service` on manta9000 runs the new checks as `manta-trading`, and both rows read `OK`.
- Runbook 200's flag section says the flags now fail `mt-health.service`. Runbook 100's health list includes the backup
  rows.

### Verification Walkthrough

These commands run on manta9000 from the dev checkout after the release is installed. The command that would have caught
the outage does not exist until this slice ships.

1. Install the release, which writes the setting:
   ```bash
   sudo /home/manta/source/repos/manta/trading-data/deploy/install-production.sh --ref <release-tag>
   # … MT_BACKUP_ROOTS set   (a second run: MT_BACKUP_ROOTS unchanged)
   sudo cat /etc/manta-trading.env | grep '^MT_BACKUP_ROOTS='
   # MT_BACKUP_ROOTS=/data/backup,/data/backup/17-tick
   ```
2. Healthy run, through the real unit:
   ```bash
   sudo systemctl start --no-block mt-health.service   # or wait for :50
   journalctl -u mt-health.service -n 40 --no-pager | grep backup
   # OK   backup /data/backup          no flags; checker ran 0.x h ago; limit 2 h
   # OK   backup /data/backup/17-tick  no flags; checker ran 0.x h ago; limit 2 h
   ```
3. Raise a flag on a scratch root, without touching production:
   ```bash
   R=$(mktemp -d /tmp/bk924.XXXX); touch "$R/backup-health.log"
   printf 'A BACKUP TIER IS STALE — see the backup-and-restore runbook\ndetected: %s\nFAIL offsite_wal_stale: push stamp X is 999 min old (limit 180 min)\nFLAGS archive=0 stale=1\n' "$(date -Is)" > "$R/BACKUP-STALE"
   MT_BACKUP_ROOTS="$R" uv run mt data health; echo "exit $?"
   # FAIL backup /tmp/bk924.…  BACKUP-STALE since …: offsite_wal_stale: push stamp X is 999 min old (limit 180 min) …
   # exit 1
   ```
4. Dead checker: `touch -d '-3 hours' "$R/backup-health.log"; rm "$R/BACKUP-STALE"`, then re-run. The row fails with
   `checker last ran 3.0 h ago; limit 2 h`.
5. Missing setting: `MT_BACKUP_ROOTS= uv run mt data health` gives `FAIL backup  MT_BACKUP_ROOTS is not set …`, exit 1.
6. Clean up: `rm -r "$R"`.

**Caveats found in implementation:**

- From the dev checkout, `mt data health` records a `pass_runs` row in whatever database `MT_TIMESCALE_DB_URL`
  names. That is production on manta9000, so a dev-checkout run writes a health verdict that `mt data overview` shows until the
  next hourly run. To exercise only the backup rows, point the database URL at a dead port. The backup rows still print
  (D6), and the run exits 2 instead of 1:
  ```bash
  export MT_TIMESCALE_DB_URL=postgresql://nobody@127.0.0.1:1/none MT_TICK_DB_URL=
  ```
- Each flag's detail lists only its own class's failures. Both flag files hold the whole checker output, so the
  `ARCHIVE-BROKEN` file also carries `offsite_wal_stale`. `summarise_flag` narrows each flag to its class using the
  checker's `FAILED archive=… stale=…` line, so the 2026-10-18 example above reads as designed. With no `FAILED` line
  (`cannot_check`), it shows every `FAIL` line.
- `detected:` is the time of the cron run that last wrote the flag (the writer rewrites the file every 30 minutes
  while it stays raised), not when the flag first appeared. So "since" names the latest confirmation.

**Run on manta9000, 2026-10-22, from the slice branch (steps 3–6, with the dead-port database URL above):**

```
# step 3 (stale flag)
FAIL backup /tmp/bk924.7oLp  BACKUP-STALE since 2026-10-22T19:38:56-06:00: offsite_wal_stale: push stamp X is 999 min old (limit 180 min) …
Error: health check could not run: connection failed: …        # exit 2 (dead database URL)
# step 4 (dead checker)
FAIL backup /tmp/bk924.7oLp  checker last ran 3.0 h ago; limit 2 h — backup_health_cron.sh is not running
# touch the log again (healthy)
OK   backup /tmp/bk924.7oLp  no flags; checker ran 0.0 h ago; limit 2 h
# step 5 (MT_BACKUP_ROOTS=)
FAIL backup         MT_BACKUP_ROOTS is not set — no backup cluster is judged
# real roots, read-only: MT_BACKUP_ROOTS=/data/backup,/data/backup/17-tick
OK   backup /data/backup          no flags; checker ran 0.1 h ago; limit 2 h
OK   backup /data/backup/17-tick  no flags; checker ran 0.1 h ago; limit 2 h
```

Steps 1–2 need the released tag installed by `install-production.sh`. They are tracked in issue #54.

## Implementation Notes

### Development Approach

1. Constants and the `backup_roots` setting.
2. `health_backup.py`, pure functions first. Unit tests use the real flag bodies from the 2026-10-18 and 2026-09-06 log
   lines.
3. `read_root`, and the writer-to-reader test through `backup_health_cron.sh`.
4. Wire `gather_backup` into `data_health`, before the connection. Add a test that the rows print on `OperationalError`.
5. D7 parity test.
6. Extract the shared atomic write in `env_add_keys.sh`, add `env_set_key` and its test, then the install step 4 change.
7. Docs: runbooks 100 and 200, the unit comment, the env examples, CHANGELOG.

This is one task file. Effort: 2/5.

### Special Considerations

- **Cutover:** `install-production.sh --ref <tag>` is the only host step, and it is in the operator's sudoers list.
  `mt-health.timer` picks up the new code and setting at its next firing. No PM host step is needed.
- **The dev checkout's `.env`** does not get `MT_BACKUP_ROOTS` automatically. An interactive `mt data health` there
  reports `FAIL backup … not set` until the operator adds the key. This is intended: unset is a failure, not a pass.
  `.env_sample` documents the key.
- **Security:** the check reads files only. `env_set_key` never prints values, and it keeps `/etc/manta-trading.env` at
  0640 throughout, because the temp file is created at 0600 and moved into place.
