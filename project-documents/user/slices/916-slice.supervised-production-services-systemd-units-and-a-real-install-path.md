---
docType: slice-design
slice: supervised-production-services-systemd-units-and-a-real-install-path
project: trading-data
parent: ../architecture/900-slices.foundation-cleanup.md
dependencies: [128, 908]
interfaces: []
dateCreated: 20260822
dateUpdated: 20260822
status: not_started
---

# Slice Design: Supervised Production Services — systemd Units and a Real Install Path

## Overview

Nothing on the production host survives a reboot and nothing restarts on crash —
verified on manta9000 (192.168.1.144) 2026-08-18, and demonstrated live by the
2026-08-19 crash: PostgreSQL and the backup cron came back; acquisition did not,
because no unit exists to bring it back. Production is currently the developer
checkout at `~/source/repos/manta/trading-data`, run by hand.

This slice gives production a real install path (a dedicated, pinned checkout
under `/opt/manta-trading` owned by a service account) and systemd supervision
for the three things that need it: the daily acquisition pass, the minute
acquisition pass, and the `mt serve` API server. It closes the runbook's three
"Not yet documented" gaps — where the env file lives, what re-invokes the
acquisition passes, and the restart-after-reboot procedure — by making the
answers real, then documenting them.

## Value

- **Operational**: a reboot or crash no longer silently stops acquisition. The
  answer to "is production running?" becomes `systemctl status` /
  `systemctl list-timers` instead of querying `acquisition_state` by hand.
- **Deployment integrity**: production stops meaning "whatever state the dev
  checkout happens to be in." A `git pull` or dirty working tree in
  `~/source/repos/...` can no longer change what production runs.
- **Documentation**: `production-deploy.md` becomes procedure instead of
  honest-but-incomplete description; its "Future target" section disappears
  because the target now exists.

## PM Decisions (2026-08-22) — the design follows from these

The slice plan entry recorded eight open decisions. The Project Manager resolved
the gating ones on 2026-08-22; the rest are resolved here as design decisions.

1. **Unattended acquisition: yes.**
2. **Form: oneshot passes fired by systemd timers** — *not* the long-running
   looping daemon the slice-128 templates assumed. `mt data daemon run
   {--daily|--minute} --stop-when-done` (one pass, then exit) **remains the
   production invocation**, now invoked by a timer instead of a human. The
   looping `--forever` form stays available for manual/debug use but is not
   deployed.
3. **Install shape: dedicated install** at `/opt/manta-trading` — a second git
   checkout at a pinned ref with its own `uv sync`-managed `.venv`, owned by a
   dedicated service account. (Slice 908's `uv tool install` failure does not
   apply: this is a checkout + `uv sync`, the same mechanism the dev checkout
   already uses, not a PyPI tool install.)
4. **Slice 915's three backup cron entries stay on cron.** No timer migration.
   The two documents that point at slice 916 by name (the crontab comment and
   `backup-and-restore.md`) get updated to record that the question is answered:
   they stay.
5. **The rclone GoogleDrive mount is excluded.** Investigated 2026-08-22: it is
   a personal mount of the login user (dead since ~Feb 2025 from an expired
   OAuth token); no project code, script, or runbook reads `~/GoogleDrive`, and
   offsite backups go directly to B2. Personal host configuration is PM-owned
   and does not route through slice ownership.

## Technical Scope

**In scope**

- Service account `manta-trading` (system account, `nologin`), owning
  `/opt/manta-trading`.
- Dedicated install: `git clone` at a pinned ref into `/opt/manta-trading`,
  `uv sync` into its `.venv`.
- Environment file `/etc/manta-trading.env` (mode 0640, `root:manta-trading`).
- systemd units (concrete files, replacing the never-installed slice-128
  `.tmpl` templates in `deploy/systemd/`):
  - `mt-daily-pass.service` + `mt-daily-pass.timer`
  - `mt-minute-pass.service` + `mt-minute-pass.timer`
  - `mt-serve.service` (long-running API server)
- The existing journald drop-in `journald-manta-trading.conf` (2 GiB /
  200 MiB caps), installed.
- A single PM-run install script `deploy/install-production.sh` (every root
  step on manta9000 is PM-executed; one reviewed script beats a page of
  copy-paste, which multi-line zsh pastes have already shown to be unreliable).
- One explicit, reversible cutover step; the old checkout stays runnable.
- Code change: `serve.py` `--workers` help text stops citing deprecated slice
  155 as "the future supervised launcher".
- Documentation: rewrite `production-deploy.md` (procedure, not description;
  delete "Future target"; close the three "Not yet documented" items); update
  `backup-and-restore.md`'s cron-vs-timer pointer; update the crontab comment
  wording via the runbook (crontab itself is PM-owned host config).

**Explicitly not in scope**

- Changing `mt update` — it stays the interactive-user path by slice 908's
  decision (D7: production runs from a checkout, not a PyPI install).
- Migrating the backup cron entries to timers (PM decision above).
- The rclone GoogleDrive mount (PM decision above).
- Test-cluster/production separation (slice 917, complete) and CI (slice 907).
- Automatic migrations. `mt data migrate apply` stays operator-run with the
  maintenance credential; units never run DDL.

## Dependencies

### Prerequisites
- Slice 128 (complete): produced the original unit templates and journald
  drop-in this slice supersedes/installs.
- Slice 908 (complete): settled that production is checkout-based, not
  PyPI-installed; its D7 constrains `mt update` out of scope here.
- Host: manta9000, no passwordless sudo — **every root step is PM-executed**.
- `uv` present on the host (used at deploy time only, never at service
  runtime).

### Interfaces Required
- `mt data daemon run {--daily|--minute} --stop-when-done` — pass semantics
  from slices 145/146/912: exits when scope is drained; waits for the daily
  gate (00:00 UTC + `DAILY_CYCLE_START_OFFSET`, 30 min) rather than exiting
  early; re-running after completion is cheap ("no actionable work", no
  provider call); an interrupted pass resumes where it stopped.
- `daemon_id` needs no injection — resolved in code as fixed constants
  `DAILY_DAEMON_ID = "daily-acquisition"` / `MINUTE_DAEMON_ID =
  "minute-acquisition"` (`data/acquisition/daemon/types.py`). Separate daily
  and minute services keep those identities and their failures separate.
- `Settings` (pydantic-settings): reads `MT_*` from the process environment
  and `.env` from the working directory. Units inject via `EnvironmentFile=`;
  the `.env`-in-CWD path remains a dev-checkout convenience only.

## Architecture

### Install tree

```
/opt/manta-trading/            git checkout at a pinned ref (tag or commit),
                               owner manta-trading:manta-trading
/opt/manta-trading/.venv/      created by `uv sync` at deploy time
/etc/manta-trading.env         MT_* runtime config, root:manta-trading 0640
/etc/systemd/system/           mt-daily-pass.{service,timer},
                               mt-minute-pass.{service,timer}, mt-serve.service
/etc/systemd/journald.conf.d/  manta-trading.conf (2G/200M caps)
```

No `.env` file is placed in `/opt/manta-trading` — configuration reaches the
services only through `/etc/manta-trading.env`, so there is exactly one source
(fails explicit if a variable is missing, per project rules).

### Unit inventory and key settings

**`mt-daily-pass.service`** (analogous for minute):

```ini
[Service]
Type=oneshot
User=manta-trading
WorkingDirectory=/opt/manta-trading
EnvironmentFile=/etc/manta-trading.env
ExecStart=/opt/manta-trading/.venv/bin/mt data daemon run --daily --stop-when-done
# A pass legitimately runs for a long time (gate wait up to 30 min, then the
# fetch itself). The 90s DefaultTimeoutStartSec would kill it mid-pass.
TimeoutStartSec=infinity
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
```

Deliberate differences from the slice-128 templates: `ExecStart` invokes the
venv's `mt` entry point directly — `uv` is a deploy-time tool, not a runtime
dependency, and `uv run` at service start could resolve/sync at the wrong
moment. `Type=oneshot` + `--stop-when-done` per the PM's form decision. No
`Restart=` on the pass units — recovery is the next timer firing, which the
pass semantics make safe and cheap. No `[Install]` section on pass services;
the timers are what get enabled. The stale
`Documentation=https://github.com/manta-trading/trading` URL (predates the repo
rename) is corrected in all units.

**`mt-daily-pass.timer`** (analogous for minute, staggered):

```ini
[Timer]
# After the 00:30 UTC daily gate; second firing is a same-day catch-up —
# a drained pass exits without provider calls, an interrupted one resumes.
OnCalendar=*-*-* 00:35:00 UTC
OnCalendar=*-*-* 12:35:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

Minute timer fires at 01:05 and 13:05 UTC — staggered so both passes never
start simultaneously (both would compete for the same provider quota bucket).
`Persistent=true` fires a missed schedule at boot, which is what makes a
post-reboot catch-up automatic. systemd never starts a service whose previous
activation is still running, so an overlong pass simply absorbs the next
firing. Exact times are operator-tunable in the unit; they are constrained
below by the gate (daily must fire after 00:30 UTC) and are otherwise not
load-bearing.

**`mt-serve.service`**: conventional long-running service — `Type=simple`,
same `User=`/`EnvironmentFile=`/hardening block, `ExecStart=
/opt/manta-trading/.venv/bin/mt serve`, `Restart=on-failure`, `RestartSec=10s`,
`StartLimitBurst=5` / `StartLimitIntervalSec=300s`, `WantedBy=multi-user.target`.
This is the unit the `kill -9` success criterion exercises.

### Environment file contents

`/etc/manta-trading.env` carries exactly what the services need:

- `MT_TIMESCALE_DB_URL` — the DML-only application credential.
- `MT_EODHD_API_KEY`.
- Optional operator tuning (`MT_LOG_LEVEL`, `MT_DAILY_CYCLE_RETRY_MINUTES`).

**Deliberately absent:** `MT_TIMESCALE_MAINTENANCE_URL`. The DDL credential
(slice 913's separation) stays out of service environments; migrations remain
an operator action from an interactive shell.

The repo gains `deploy/manta-trading.env.example` with placeholder values
(never real credentials, per project rules); the PM fills the real file on the
host.

## Implementation Details

### Migration plan (cutover)

The host is simultaneously production, the dev checkout, and the backup cron's
home — the risk is in the cutover, not the units. The sequence keeps every step
reversible and the old checkout runnable throughout:

1. **Install (inert)** — PM runs `deploy/install-production.sh` with sudo:
   creates the `manta-trading` account, clones the repo into
   `/opt/manta-trading` at the ref passed as an argument, runs `uv sync` as the
   service account, installs the env-file skeleton, unit files, and journald
   drop-in, then `systemctl daemon-reload`. **Enables nothing.** The script is
   idempotent (safe to re-run) and refuses to proceed if `/opt/manta-trading`
   exists with local modifications.
2. **Configure** — PM fills `/etc/manta-trading.env` (values come from the dev
   checkout's `.env`; the file itself is never copied).
3. **Verify without cutover** — `systemctl start mt-daily-pass.service` once,
   watch the journal, confirm a normal pass against production data. This is
   the same pass the operator runs by hand today, from a different directory —
   not a behavior change.
4. **Cutover (the one explicit step)** — `systemctl enable --now` both timers
   and `mt-serve.service`. From this instant production is the `/opt` install.
   Operator practice change: acquisition passes and `mt serve` are no longer
   started by hand from the dev checkout.
5. **Rollback** — `systemctl disable --now` the same three; the dev checkout
   was never modified and the manual procedure works exactly as before.

**Update procedure after cutover** (replaces the runbook's current one for
production; the dev checkout keeps its own): PM-executed
`git -C /opt/manta-trading fetch` + `git -C /opt/manta-trading checkout <ref>`
+ `uv sync` (as the service account) + `systemctl restart mt-serve` — pass
units pick the new code up at their next firing. Migrations, when a release
has one, stay a separate operator step with the maintenance credential.

### Code changes

Minimal, by design (this slice is deployment + documentation):

- `src/manta_trading/cli/commands/serve.py` — `--workers` help text: replace
  "Run the daemon in a separate terminal; slice 155 adds supervised launch"
  with a reference to the `mt-serve` systemd unit.
- New files under `deploy/`: five unit files, `manta-trading.env.example`,
  `install-production.sh`. The two `.service.tmpl` files are deleted —
  superseded, and the fixed service account removes the need for templating.
  `journald-manta-trading.conf` is kept as-is (it already says what it needs
  to; only its install becomes real).

### Documentation changes

- `production-deploy.md`: rewritten around the `/opt` install as current
  reality — the update procedure above, the env-file location, what re-invokes
  the passes (the timers, with their schedule and the gate interaction), the
  restart-after-reboot procedure ("nothing — that is the point", plus how to
  verify), and status checking via `systemctl` alongside the existing
  `acquisition_state` queries. The "Future target — /opt + systemd" section is
  deleted. The timing-constraints section (gate wait, retry interval,
  cheap re-runs) survives — it now explains timer behavior.
- `backup-and-restore.md`: the "916 will decide cron vs timers" pointer becomes
  "decided 2026-08-22: backups stay on cron."
- Slice plan entry 17 in `900-slices.foundation-cleanup.md`: gets its decision
  outcomes recorded (or a pointer here) so the entry stops reading as open.

## Integration Points

**Provides:** supervised, reboot-surviving production for every later slice —
notably 907 (CI), whose deploy target this defines. `systemctl`-visible
liveness for operators and runbooks.

**Consumes:** pass semantics from 145/146/912 unchanged; credential separation
from 913 unchanged; 908's checkout-based-production decision unchanged.

## Success Criteria

Restated from the slice plan entry in timer terms where the form decision
changed them:

1. Units and timers installed and enabled; `systemctl list-timers` shows both
   pass timers with correct next-fire times.
2. A deliberate `kill -9` of the `mt serve` process is followed by an
   automatic restart (visible restart count in `systemctl status mt-serve`).
3. A reboot brings back `mt-serve` and both timers with no operator action;
   `Persistent=true` fires any schedule missed while down.
4. `systemctl status mt-daily-pass` / `list-timers` answers "is production
   acquiring?" — last result, last run, next run — without querying
   `acquisition_state`.
5. A pass started via `systemctl start mt-daily-pass.service` completes
   identically to today's manual invocation (same log shape, same
   `acquisition_state` effect) running as `manta-trading` from `/opt`.
6. journald honors the 2 GiB / 200 MiB caps
   (`journalctl --header` / disk-usage check).
7. `/etc/manta-trading.env` exists at mode 0640 `root:manta-trading`; no
   credential appears in any tracked file; the maintenance URL is absent from
   the service environment.
8. `production-deploy.md` has no "Future target" and no "Not yet documented"
   section — all three items are procedure now.
9. Rollback demonstrated once: disable the three units, run a manual pass from
   the dev checkout, re-enable.
10. `serve.py --help` no longer references slice 155.

### Verification Walkthrough

Draft demo script — refined at end of Phase 6. PM executes all `sudo` steps.

```bash
# 1. Install (inert — enables nothing)
sudo deploy/install-production.sh --ref <tag-or-sha>
sudo install -o root -g manta-trading -m 0640 /dev/null /etc/manta-trading.env
sudoedit /etc/manta-trading.env          # fill MT_TIMESCALE_DB_URL, MT_EODHD_API_KEY

# 2. One supervised pass, no cutover yet
sudo systemctl start mt-daily-pass.service
journalctl -u mt-daily-pass.service -f    # normal pass output, exits 0
systemctl status mt-daily-pass.service    # "inactive (dead)" + "status=0/SUCCESS"

# 3. Cutover — the one explicit step
sudo systemctl enable --now mt-daily-pass.timer mt-minute-pass.timer mt-serve.service
systemctl list-timers 'mt-*'              # both timers, sane next-fire times
curl -s localhost:8100/...                # API answers (exact path per API docs)

# 4. Crash supervision
sudo kill -9 "$(systemctl show -p MainPID --value mt-serve.service)"
sleep 15; systemctl status mt-serve.service   # active again, restart count +1

# 5. Reboot survival (PM-scheduled moment, executed immediately — not a
#    wall-clock-waiting task)
sudo reboot
# after boot:
systemctl list-timers 'mt-*'              # timers back with no operator action
systemctl is-active mt-serve.service      # active

# 6. Rollback rehearsal
sudo systemctl disable --now mt-daily-pass.timer mt-minute-pass.timer mt-serve.service
cd ~/source/repos/manta/trading-data && uv run mt data daemon run --daily --stop-when-done
sudo systemctl enable --now mt-daily-pass.timer mt-minute-pass.timer mt-serve.service
```

Timer *firing* is verified with `systemctl start` (same unit the timer
activates) plus `list-timers` for the schedule — no task waits for a
wall-clock event, per standing PM rule.

## Risk Assessment

- **Two identical-looking installs on one box.** After cutover, the dev
  checkout and `/opt` both exist and both can run `mt`. Mitigations: the
  runbook states plainly which is production; the services' journal identity
  (`_SYSTEMD_UNIT`) makes supervised runs distinguishable from manual ones;
  the install script pins a ref, so `/opt` never tracks a moving branch
  implicitly.
- **Env drift between `.env` (dev) and `/etc/manta-trading.env` (prod).**
  Single-source rule: services read only the latter; the runbook's update
  procedure lists it as the one place to change production config.
- **A failed pass is quieter under timers than under a crashed service.**
  A oneshot failure does not page anyone; it shows as `failed` in `systemctl`
  and is retried at the next firing. Accepted for this slice — alerting is
  future work and is *not* smuggled in here.

## Implementation Notes

- Suggested order: unit files + env example → install script → serve.py help
  text → runbook rewrite → host install/verify (PM) → cutover (PM) →
  backup-and-restore pointer + slice-plan entry update.
- Task breakdown must mark every root/host step as PM-executed (the 917
  pattern); stage all files in the repo — multi-line pastes into the host zsh
  are unreliable.
- The install script is the only shell of substance; keep it plain (no
  templating engine — the fixed account name removed the need) and idempotent.
- Effort: 3/5, unchanged from the plan entry. Risk: Med, concentrated in the
  cutover as the entry states.
