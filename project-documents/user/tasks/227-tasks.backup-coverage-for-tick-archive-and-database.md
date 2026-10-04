---
docType: tasks
slice: backup-coverage-for-tick-archive-and-database
project: trading-data
lld: user/slices/227-slice.backup-coverage-for-tick-archive-and-database.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [226]
interfaces: [228, 230, 231, 232, 234]
projectState: >
  Slice design committed and reviewed (CONCERNS, findings addressed). 226 is
  merged: the tick cluster 17/tick (port 5433, data /data/postgresql/17/tick,
  database trading_tick) holds the archive rebuilt in 212.5 s, with no
  backup of any kind. /data/tick-archive is already in nightly restic (223).
  Production backups run from this checkout via /etc/cron.d. archive_mode is
  off on 17/tick.
dateCreated: 20261004
dateUpdated: 20261004
status: not_started
---

# Tasks: Backup Coverage for Tick Archive and Database

## Context Summary

- Working on slice 227. It extends the 915/920 backup tooling to a second
  cluster through one checked-in table, and enrols `17/tick` through one
  PM-run cutover.
- It delivers:
  - `deploy/backup-clusters.conf` with a bash parser and a Python parser;
  - three cron wrappers taking `--url-key`, and `--replication-host` /
    `--remote` where needed;
  - the cron template split into a host block and a per-cluster block;
  - `deploy/setup-backup.sh` looping over the table, data directory from
    `pg_lsclusters`, `--cluster` removed;
  - the tick replication grant, `pg_hba` line and parent-directory modes;
  - `scripts/cutover_227_tick_backup.py`;
  - runbook 200 and 210 updates.
- **Production `17/main` is never restarted** and its backup paths and B2
  prefixes never move. Its cron lines change only by gaining the new
  explicit arguments, which carry today's hard-wired values (TD3).
- **This checkout is live.** `/etc/cron.d/manta-trading-backup` runs scripts
  from this checkout. A wrapper change on main reaches production's next
  firing even though the installed cron file still has today's arguments.
  So every wrapper change must keep working with the old cron lines until
  the cutover re-renders them. See task 2.0.
- **No root.** `sudo -n` fails. Every root step lives in the cutover, which
  the PM runs. Agent-side host checks are `--check` runs as manta.
- **Destructive SQL:** none in this slice. Tests use fake `psql`/`rclone`
  stubs and temp directories as the existing backup tests do.
- Design: read TD1–TD10, the failure table under TD8, and Success Criteria
  before starting. Tasks cite them as "TD n".
- Next slice: 228 (tick restore drill), which reads paths from the table.

**Test environment.** Run tiers through the reviewed runner:
`uv run python scripts/run_tests.py unit | integration [-- <path>]`. Never
export `.env` into a test run. Run mypy on the src kalshi paths and the tests
in one invocation. Known baseline failures are not regressions
(`test_cli_lists` priority1 x2, `test_migration_051_052` x2,
`test_policy_advances_head` unaided x2). Shellcheck is
`~/.local/bin/shellcheck -x`. Scope `ruff format` to touched files and check
`git diff` for unrelated deletions before each commit.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 1 — Cluster table (TD2)

- [ ] **1.1 Write `deploy/backup-clusters.conf` (effort 1)**
  - [ ] Header comment naming each column and what `-` means, then the two
        rows exactly as TD2 shows them (`17/main`, `17/tick`).
  - [ ] Columns: `cluster url_key backup_root remote_subpath
        replication_host metadata_cron weekly_base_cron`. The two cron
        columns hold five whitespace-separated fields each, so a row has 15
        whitespace tokens.
  - [ ] Success: the file exists; the `17/main` schedules (`0 2 * * *`,
        `0 3 * * 0`) match the installed cron file's metadata and weekly lines.

- [ ] **1.2 Bash parser `deploy/lib/backup_clusters.sh` (effort 2)**
  - [ ] A sourced library with one function that reads the table and fills
        parallel arrays (one per column), or prints one row per line for a
        `while read` loop. Pick whichever `setup-backup.sh` consumes more
        simply.
  - [ ] Skips blank lines and `#` comments; tolerates any run of spaces or
        tabs between fields and trailing whitespace.
  - [ ] Hard errors (non-zero, message naming the line number): wrong token
        count; a duplicate cluster; `backup_root` not absolute or not under
        `/data`; `url_key` not matching `^MT_[A-Z0-9_]+$`; an empty table.
  - [ ] The `pg_lsclusters` membership check is not here; it is TD4's (3.2).
  - [ ] Success: shellcheck clean; sourcing it defines functions only, no
        side effects.

- [ ] **1.3 Bash parser tests (effort 2)**
  - [ ] New `test/unit/test_backup_clusters.py`, bash cases: parse the real
        `deploy/backup-clusters.conf` and assert both rows field by field.
  - [ ] Malformed cases, each its own test: short row, long row, duplicate
        cluster, relative root, root outside `/data`, bad url key, only
        comments. Each exits non-zero and names the line.
  - [ ] Whitespace variants: tabs, multiple spaces, trailing spaces parse
        identically to the real file.
  - [ ] Success: tests pass.

- [ ] **1.4 Python parser `scripts/backup_clusters.py` (effort 2)**
  - [ ] A frozen dataclass per row (the seven columns; `-` becomes `None`
        for `remote_subpath` and `replication_host`) and
        `load_clusters(path) -> list[BackupCluster]`, plus
        `cluster(path, name)` that raises on an unknown name.
  - [ ] Same rules and errors as 1.2 (raise `ValueError` naming the line).
  - [ ] Importable by scripts the way `cutover_common.py` is.
  - [ ] Success: ruff and mypy clean.

- [ ] **1.5 Python parser tests and parity (effort 2)**
  - [ ] In `test_backup_clusters.py`: the real file; each malformed case
        from 1.3; whitespace variants.
  - [ ] Parity test: both parsers read the real file and produce the same
        field values row by row.
  - [ ] Success: tests pass.
  - [ ] Commit: `feat: add backup cluster table and parsers`.

## Section 2 — Wrapper arguments (Scope item 4)

- [ ] **2.0 Compatibility rule for this section (effort 1)**
  - [ ] Read the installed `/etc/cron.d/manta-trading-backup` (readable as
        manta). Production calls the three wrappers with today's arguments
        until the cutover re-renders the file.
  - [ ] Rule for 2.1–2.3: the new cron lines always pass the new
        arguments, but the old installed lines don't, and the health job
        fires every 30 minutes between the merge and the cutover. So an
        **absent** `--url-key` reads `MT_TIMESCALE_MAINTENANCE_URL` and an
        absent `--remote` reads `MT_BACKUP_S3_BUCKET`, exactly as today. A
        comment on each says the absent form exists only for the pre-227
        cron file. 7.3 removes it after the cutover.
  - [ ] Success: the rule is noted in each wrapper's header comment.

- [ ] **2.1 `backup_health_cron.sh --url-key` (effort 1)**
  - [ ] Read the DB URL from `env_value "$ENV_FILE" "$URL_KEY"`. The
        `cannot_check` message names the key actually used.
  - [ ] Validate the key shape (`^MT_[A-Z0-9_]+$`); a bad key exits 2.
  - [ ] Success: shellcheck clean.

- [ ] **2.2 `cron_nightly_metadata.sh --url-key --remote` (effort 2)**
  - [ ] `--url-key` as in 2.1. Switch the URL read to the shared
        `env_value` helper the other wrappers use, replacing the
        `grep | sed | tr` read.
  - [ ] `--remote <rclone-path>` is the offsite prefix; the script appends
        `/metadata` the way it appends to the bucket today. Production's
        value is `b2:<bucket>`, tick's `b2:<bucket>/17-tick`.
  - [ ] Success: shellcheck clean.

- [ ] **2.3 `cron_weekly_backup.sh --url-key --replication-host` (effort 2)**
  - [ ] `--url-key` as in 2.1.
  - [ ] `--replication-host <host>` replaces the URL's host, whatever it
        is, before the port. This replaces the hard-coded
        `@192.168.1.144:` → `127.0.0.1` rewrite. Without the flag the URL is
        used as written (tick's case, `-` in the table).
  - [ ] Production's rendered line passes `--replication-host 127.0.0.1`,
        so the result for production is unchanged. Until the cutover the old
        cron line passes no flag, so for 2.0 an absent flag **with an absent
        `--url-key`** keeps today's `@192.168.1.144:` rewrite; an absent flag
        with `--url-key` given means "as written". 7.3 removes the old path.
  - [ ] Success: shellcheck clean.

- [ ] **2.4 Wrapper tests (effort 2)**
  - [ ] Extend `test/unit/test_backup_cron_glue.py` (or the file that
        already covers each wrapper; `git grep -n cron_weekly_backup test/`).
  - [ ] Per wrapper: the key named by `--url-key` is the one read (an env
        file holding two keys proves it); a bad key exits 2; the absent form
        reads `MT_TIMESCALE_MAINTENANCE_URL` (2.0). Weekly with no new
        arguments still rewrites `@192.168.1.144:`; metadata with no
        `--remote` still uses `MT_BACKUP_S3_BUCKET`.
  - [ ] Weekly: `--replication-host 127.0.0.1` rewrites a
        `user:pw@manta9000:5433/db` URL to `@127.0.0.1:5433`; without the
        flag the URL reaches the stubbed `backup_prod.sh` unchanged.
  - [ ] Metadata: `--remote b2:x/17-tick` makes the stubbed rclone receive
        `b2:x/17-tick/metadata`.
  - [ ] Success: tests pass; existing wrapper tests still pass.
  - [ ] Commit: `feat: add url-key and remote arguments to backup wrappers`.

## Section 3 — Cron render per cluster (Scope item 3, TD3)

- [ ] **3.1 Capture production's current lines as a fixture (effort 1)**
  - [ ] Copy the six job lines of the installed
        `/etc/cron.d/manta-trading-backup` into
        `test/unit/fixtures/cron_main_pre227.txt` (no secrets: the file
        holds paths and the bucket name only; check before committing).
  - [ ] Success: the fixture matches the installed file's job lines exactly.

- [ ] **3.2 Split the template (effort 3)**
  - [ ] `deploy/cron.d/manta-trading-backup` keeps the header and the host
        block (the two restic lines).
  - [ ] New `deploy/cron.d/manta-trading-backup.cluster` holds the four
        per-cluster lines (health, WAL push, metadata, weekly), with new
        tokens for the url key, remote prefix, replication-host argument,
        metadata schedule and weekly schedule. `--system-stamp` in the
        health line points at the **host** root's
        `system-backup.stamp` (Scope item 2).
  - [ ] Each rendered cluster block starts with a comment line naming the
        cluster, so a reader of `/etc/cron.d` sees which block is which.
  - [ ] `deploy/lib/render_cron.sh` renders the host template once and the
        cluster template once per row, into one output file. Order: header,
        the `17/main` block, the `17/tick` block, then the host block. Keep
        its existing refusal of unfilled placeholders and unescaped `%`.
  - [ ] Success: shellcheck clean.

- [ ] **3.3 Render tests (effort 3)**
  - [ ] In `test_setup_backup.py`'s `TestRenderCron`: render from the real
        table.
  - [ ] Production lines: strip the three new argument pairs
        (`--url-key MT_TIMESCALE_MAINTENANCE_URL`,
        `--replication-host 127.0.0.1`, `--remote b2:<bucket>`) from the
        rendered `17/main` and host lines; the result equals
        `cron_main_pre227.txt` line for line.
  - [ ] No line of the `17/main` block or the host block contains
        `17-tick`.
  - [ ] Tick block: every path is under `/data/backup/17-tick`, the remote
        is `b2:<bucket>/17-tick`, the url key is `MT_TICK_MAINTENANCE_URL`,
        no `--replication-host`, schedules `15 2 * * *` and `30 2 * * 0`,
        `--system-stamp /data/backup/system-backup.stamp`.
  - [ ] Existing render tests (interval, re-render identical, placeholder,
        percent) still pass, adjusted only for the new arguments.
  - [ ] Success: tests pass.
  - [ ] Commit: `feat: render backup cron blocks per cluster`.

## Section 4 — `setup-backup.sh` per cluster (Scope item 2, TD4, TD10)

- [ ] **4.1 Loop over the table (effort 4)**
  - [ ] Source `deploy/lib/backup_clusters.sh`; parse the table before any
        step, so a malformed row stops the run before any change.
  - [ ] Remove `--cluster` from arguments, usage and header. `--backup-root`
        stays and now means the host root only (header comment says so).
  - [ ] Per row: directories (`base wal metadata` under the row's root),
        WAL directory owner/mode, ACL, and the PostgreSQL settings with that
        row's `archive_command`. Report items prefixed with the cluster
        (`17/tick dir-wal`), so `--check` output says which cluster drifted.
  - [ ] Host once: package, `system` directory under the host root, cron
        render, timeshift, restic, leftover crontab.
  - [ ] Arm file: reported per row from the row's root.
  - [ ] Success: shellcheck clean.

- [ ] **4.2 Data directory from `pg_lsclusters` (TD4) (effort 2)**
  - [ ] For each row read the data directory and config path with
        `pg_lsclusters` (parse its fields by column, not by fixed widths;
        use `--no-header` or skip the header line explicitly).
  - [ ] A cluster not listed is a hard error before any change.
  - [ ] Remove `PG_DATA_ROOT`; `PG_CONF_ROOT` goes too if the config path
        comes from the same read.
  - [ ] Success: shellcheck clean.

- [ ] **4.3 B2 lifecycle check (TD10) (effort 3)**
  - [ ] Read rclone's B2 backend source (`backend/b2/b2.go`, the
        `lifecycle` command; context7 or `gh api`) and record in a comment:
        whether the command can add one prefix's rule without replacing the
        bucket's existing rules.
  - [ ] `--check` and apply both read the rules with
        `rclone backend lifecycle b2:<bucket>` and report, per row,
        `OK lifecycle <prefix>` or `MISSING lifecycle <prefix>` against the
        rule production's prefixes have (30 days from hiding to deleting).
  - [ ] Apply adds the missing rule only if the source read shows it is
        additive. Otherwise apply reports `MISSING lifecycle <prefix> (add in
        the B2 console: <rule>)` and does not act.
  - [ ] Success: shellcheck clean; the comment cites the rclone source file
        and version read.

- [ ] **4.4 Setup tests (effort 3)**
  - [ ] Update `test_setup_backup.py`: argument tests drop `--cluster`;
        the fake host gains a stub `pg_lsclusters` printing both clusters.
  - [ ] Scratch-root check mode reports MISSING per cluster with the
        cluster prefix and never applies.
  - [ ] A malformed table row stops before any step runs (no step headers
        printed).
  - [ ] A cluster missing from `pg_lsclusters` is a hard error before any
        change.
  - [ ] Lifecycle: stubbed rclone output with only production's rules gives
        `MISSING lifecycle 17-tick`; with both gives OK.
  - [ ] The tick row's `archive_command` names `/data/backup/17-tick/wal`.
  - [ ] Success: tests pass; `test_system_backup.py` and `test_wal_offsite.py`
        still pass.

- [ ] **4.5 Live read-only check (effort 1)**
  - [ ] Run as manta: `deploy/setup-backup.sh --check --checkout "$PWD"
        --env-file "$PWD/.env" --backup-root /data/backup`.
  - [ ] Expect: `17/main` items OK where readable without root; `cron.d`
        DRIFT (the new arguments); `17/tick` items MISSING; lifecycle
        MISSING for `17-tick`. Anything else is a bug to fix before going on.
  - [ ] Paste the output into the commit message body.
  - [ ] Commit: `feat: make setup-backup per cluster from the table`.

## Section 5 — Tick roles and access (Scope item 5, TD5)

- [ ] **5.1 `provision_tick_roles.sql -v with_replication=1` (effort 1)**
  - [ ] Copy `provision_roles.sql`'s `with_replication` block and its
        comment, granting REPLICATION to the tick migrate role.
  - [ ] Success: running without the variable is unchanged.

- [ ] **5.2 `provision_tick_cluster.sh` changes (effort 3)**
  - [ ] Pass `-v with_replication=1` to the roles script.
  - [ ] Add `host replication <migrate role> <src>/32 scram-sha-256` to
        the rendered `pg_hba` content, from the source address it already
        computes.
  - [ ] Split `DATABASES` into the databases created (`trading_tick`) and
        the databases allowed in `pg_hba` (`trading_tick`,
        `trading_tick_drill`). Remove `trading_tick_proof` from both.
  - [ ] Remove the `MT_PROOF_226_*` entries from the URL-key map and the
        header comment's proof wording.
  - [ ] Parent directories: `/data/postgresql` and `/data/postgresql/17`
        become 0755 owned by postgres. The data directory stays 0700.
        Change `PG_DIR_MODE` usage so the two modes are separate named
        constants.
  - [ ] `--check` reports each change as `WOULD …` and changes nothing.
  - [ ] Success: shellcheck clean.

- [ ] **5.3 Provisioning tests (effort 2)**
  - [ ] Extend `test/unit/test_provision_tick_lib.py`: the rendered
        `pg_hba` holds the replication line and `trading_tick_drill`, and no
        `trading_tick_proof`; the parent-mode constant is 755 and the data
        mode 700; the roles call carries `with_replication=1`.
  - [ ] Success: tests pass.

- [ ] **5.4 Live read-only check (effort 1)**
  - [ ] Run `scripts/provision_tick_cluster.sh --check` as manta.
  - [ ] Expect `WOULD` lines for the replication grant, the `pg_hba` line
        and the two parent modes, and nothing touching `17/main`.
  - [ ] Commit: `feat: grant tick replication for base backups`.

## Section 6 — Cutover (TD6, TD7, TD8)

- [ ] **6.1 Script skeleton and report (effort 2)**
  - [ ] `scripts/cutover_227_tick_backup.py`, using `cutover_common`'s
        `start_log`, `say`, `run`, `out`. No package install: cron runs from
        this checkout.
  - [ ] Each step prints expected against seen. On the first failed step
        it stops, still writes the report, and exits non-zero.
  - [ ] Report path `project-documents/user/notes/<date>-227-cutover.md`
        with frontmatter, one section per step.
  - [ ] Read paths from `backup_clusters.cluster(..., "17/tick")`; no tick
        path is a literal in the script.
  - [ ] Constants: `WAL_SWITCH_WAIT_S = 120`, the tick unit name, the
        advisory lock key imported from where the tick pass defines it
        (`git grep -n advisory src/` to find it).

- [ ] **6.2 Steps 1–5: guards, provision, setup, restart (effort 3)**
  - [ ] Step 1: SHA-256 of production's `postgresql.conf` and
        `postgresql.auto.conf` (paths from `pg_lsclusters`; read with sudo);
        tick advisory lock free (`pg_try_advisory_lock` then unlock).
  - [ ] Step 2: `setup-backup.sh --check` (sudo). Every `17/main` item OK;
        `cron.d` DRIFT is expected. Any other non-OK `17/main` item stops.
        Save a copy of the installed cron file for step 4's comparison.
  - [ ] Step 3: `sudo scripts/provision_tick_cluster.sh`.
  - [ ] Step 4: `sudo deploy/setup-backup.sh`. Then compare the new cron
        file with the saved copy using the 3.3 rule (strip the new argument
        pairs from the production lines; they must equal the old ones).
  - [ ] Step 5: if `pending_restart` is true for `archive_mode` on tick,
        re-check the advisory lock, then `sudo systemctl restart
        postgresql@17-tick`; confirm `archive_mode=on`. A pending restart on
        `17/main` stops the cutover.

- [ ] **6.3 Steps 6–13: switch, health, weekly, metadata, verify (effort 3)**
  - [ ] Run the tick jobs by extracting their command lines from the
        installed `/etc/cron.d` tick block (the comment line from 3.2 marks
        it), so the cutover runs exactly what cron runs.
  - [ ] Step 6: `pg_switch_wal()` on tick; poll the tick WAL directory for
        that segment's `.zst` up to `WAL_SWITCH_WAIT_S`.
  - [ ] Step 7: the health line once; its log's last line is `PASS`.
  - [ ] Step 8: the weekly line once, then create the arm file only if its
        final check passed (TD7). Skip both when `base/<today>` and the arm
        file already exist (re-run on the same day).
  - [ ] Step 9: the metadata line once.
  - [ ] Step 10: the health line again; last line `PASS … FLAGS archive=0
        stale=0`.
  - [ ] Step 11: `rclone check --one-way` of tick `base/`, `wal/`,
        `metadata/` against the row's remote.
  - [ ] Step 12: production hashes unchanged; production health log's last
        line still `PASS`.
  - [ ] Step 13: write the report; exit 0 only if every step passed.
        Include the lifecycle line from step 4's output.
  - [ ] Success: ruff and mypy clean.

- [ ] **6.4 Cutover tests (effort 3)**
  - [ ] New `test/unit/test_cutover_227.py`, following
        `test_cutover_921.py`'s approach to stubbing `run`/`out`.
  - [ ] Cases: the cron-line extraction from a rendered file; the 3.3
        strip-and-compare accepting the expected change and refusing any
        other; a failed step stops later steps and still writes the report;
        the arm file is not created when the weekly final check fails; the
        same-day re-run skips step 8; a pending restart on `17/main` stops.
  - [ ] Success: tests pass.
  - [ ] Commit: `feat: add slice 227 tick backup cutover`.

## Section 7 — Runbooks and close-out

- [ ] **7.1 Runbook 200 (effort 2)**
  - [ ] Placement row for the tick cluster; the per-cluster cron table
        (both blocks plus the host block); tick retention (keep-days 7, B2
        lifecycle); the TD9 exclusions table.
  - [ ] Timeshift: record that its snapshots do not reach `/data` (the
        2026-10-01 snapshot holds an empty `/data` mount point), so no
        exclude is added.
  - [ ] Update every `setup-backup.sh` invocation to the new arguments.
  - [ ] The `cutover_227_tick_backup.py` command and what its report shows.

- [ ] **7.2 Runbook 210 (effort 1)**
  - [ ] Step 7's bootstrap command without `--cluster`.
  - [ ] The restic include list matches `cron_system_backup.sh`'s
        `INCLUDE_PATHS` (it gained `/data/tick-archive` in 223).
  - [ ] Commit: `docs: add tick backup coverage to backup runbooks`.

- [ ] **7.3 Remove the pre-227 absent forms (effort 1)**
  - [ ] Only after the cutover report shows step 4 passed (the installed
        cron file carries the new arguments): make `--url-key` and
        `--remote` required in the three wrappers, remove the weekly
        wrapper's `@192.168.1.144:` path, and drop the 2.4 absent-form
        tests. A missing required argument exits 2 with usage.
  - [ ] If the cutover has not run when the rest of the slice is done,
        leave this unchecked and tell the Project Manager it is the one item
        remaining.
  - [ ] Commit: `refactor: require url-key in backup wrappers`.

- [ ] **7.4 Full validation (effort 2)**
  - [ ] Unit tier, then integration tier, separately. Only known baseline
        failures.
  - [ ] ruff, mypy, `shellcheck -x` clean on every touched file.
  - [ ] `cf check` shows no new findings for 227.
  - [ ] Commit any fixes.

## Section 8 — Cutover run (PM) and acceptance

- [ ] **8.1 [PM] Run the cutover (effort 1)**
  - [ ] The PM runs `uv run python scripts/cutover_227_tick_backup.py`
        from the checkout root after the code review. The agent hands over
        that single command.
  - [ ] If the report names a B2 console rule (TD10), the PM adds it.

- [ ] **8.2 Read the report and check acceptance (effort 2)**
  - [ ] Every step passed; commit the report.
  - [ ] Check Success Criteria 1–10 against the report and the
        Verification Walkthrough commands (as manta); record any gap.
  - [ ] Run `setup-backup.sh --check` again as manta: every row OK apart
        from items that need root to read.
  - [ ] Then do 7.3.
