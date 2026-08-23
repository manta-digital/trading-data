---
docType: slice-design
slice: supervised-production-services-systemd-units-and-a-real-install-path
project: trading-data
parent: ../architecture/900-slices.foundation-cleanup.md
dependencies: [128, 908]
interfaces: []
dateCreated: 20260822
dateUpdated: 20260823
reviewVerdictsAddressed:
  - 916-review.slice (z-ai/glm-5.2, CONCERNS, F003)
  - 916-review.slice (z-ai/glm-5.2, PASS) — second pass, after the multi-source folds
status: complete
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
  - `manta-acquisition.slice` (grouping only, no resource settings)
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

### Deferred — cross-source arbitration (needs its own slice)

EODHD daily + minute is the beginning, not the end: Kalshi (its own pass) and
Databento tick on a small futures set (few instruments, large volume) are
coming. 916 makes each new source cheap to *add* and cheap to *pause*, and
deliberately stops there. Deferred, with the reason each cannot be settled now:

- **Preemption and priority between sources.** systemd prevents a unit from
  starting only while *that same unit* is still running; two different pass
  units run concurrently with no arbitration. There is no priority and no
  queue, and `Conflicts=` is the wrong primitive because it *kills* the loser
  mid-fetch. Real arbitration is either a shared lock the passes take (keyed by
  resource class) or a scheduler with priorities inside `mt` — the looping form
  the PM declined for 916, which would acquire an actual reason to exist here.
  Deciding between them without knowing Databento's real behavior would be
  guessing.
- **Resource weights on `manta-acquisition.slice`.** The slice unit ships
  empty on purpose. `IOWeight`/`CPUWeight`/`MemoryMax` values are speculative
  tuning until there is measured contention — and the binding constraint is
  expected to be host bandwidth, disk, and DB write throughput, not provider
  quota (separate providers are separate buckets, so 916's stagger reasoning
  does not carry over).
- **Hand-staggered schedules.** The 00:35/01:05 offsets are a two-body
  solution. Somewhere around the third or fourth source, picking
  non-colliding `OnCalendar` times by hand becomes a packing problem that
  wants a coordinator instead.

Recorded as a slice-plan entry so the gap is on the record rather than
rediscovered when Kalshi lands.

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
                               mt-minute-pass.{service,timer}, mt-serve.service,
                               manta-acquisition.slice
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
Slice=manta-acquisition.slice
WorkingDirectory=/opt/manta-trading
EnvironmentFile=/etc/manta-trading.env
ExecStart=/opt/manta-trading/.venv/bin/mt data daemon run --daily --stop-when-done
# A pass legitimately runs for a long time (gate wait up to 30 min, then the
# fetch itself). The 90s DefaultTimeoutStartSec would kill it mid-pass.
TimeoutStartSec=infinity
# The runner traps SIGTERM and exits between symbols, but its sleeps are capped
# at 60s (runner.py), so worst-case clean-stop latency is that cap plus the
# in-flight symbol. The 90s default would SIGKILL a shutdown that is working.
TimeoutStopSec=300
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

**`manta-acquisition.slice`** — every acquisition unit (both pass services, and
every future source's) sets `Slice=manta-acquisition.slice`. The slice unit
itself is installed **with no resource settings**: it is a grouping, not a
tuning decision. The point is placement, not policy — putting units into a
slice later means editing every unit file and restarting production, whereas
adding `IOWeight=`/`CPUWeight=`/`MemoryMax=` to an existing slice later is a
one-file drop-in. Choosing those numbers requires contention that does not
exist yet (see Deferred, below).

```ini
# /etc/systemd/system/manta-acquisition.slice
[Unit]
Description=Manta acquisition passes (resource grouping)
```

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

### Unit naming pattern — adding a source

EODHD daily and minute are the first two of several sources (Kalshi and
Databento tick are known to be coming). The units are therefore named as an
instance of a pattern, not as two one-offs:

    mt-{source-or-cadence}-pass.service   +   .timer     — a bounded pass
    mt-{name}.service                                     — a long-running service

The current units are `mt-daily-pass` / `mt-minute-pass` (EODHD is implicit
because it is the only provider today; a second daily-cadence provider would
force the fuller `mt-{source}-{cadence}-pass` form). Adding a source is then a
documented procedure, not a design conversation: copy a unit pair, change
`ExecStart`, pick a non-colliding `OnCalendar`, set
`Slice=manta-acquisition.slice`, install, enable. The runbook carries this as a
checklist.

Two limits of the pattern are stated here so they are not rediscovered:
`Type=oneshot` + timer suits a **bounded** pass; a streaming subscription (a
plausible shape for Databento tick capture) is a `Type=simple` unit like
`mt-serve`, with historical backfill as a separate pass unit. And systemd
serializes only a unit against *itself* — see Deferred.

### Pausing a source

916's supervision is what makes an off switch exist at all; today "not running"
and "nobody ran it" are indistinguishable. Three levels, each per-source:

- `systemctl stop mt-minute-pass.timer` — no further firings this boot; a pass
  already running is untouched. Returns after a reboot (still enabled).
- `systemctl disable --now mt-minute-pass.timer` — **the operator-facing pause**:
  stops it now and removes the boot symlink, so it stays off across reboots
  until an explicit `enable --now`.
- `systemctl mask mt-minute-pass.service` — nothing can start it, not even a
  manual `start`. A guardrail for "leave this alone while X runs".

`systemctl stop mt-daily-pass.service` stops a pass that is *currently* running:
the runner traps SIGTERM and exits cleanly between symbols, which is why the
unit sets `TimeoutStopSec=300` rather than letting the 90s default escalate to
SIGKILL.

**Resuming fires a catch-up immediately.** `Persistent=true` means re-enabling
after a pause runs the missed schedule at once, which is usually the intent —
but it must be documented rather than discovered. The escape hatch, when a
resume should *not* backfill, is the stamp file
`/var/lib/systemd/timers/stamp-mt-{unit}.timer`.

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

### Install script failure recovery

`install-production.sh` is the slice's only substantial new I/O path, it runs as
root on the production host, and it does network work (`git clone`, `uv sync`)
that can fail part-way. It runs under `set -euo pipefail` and exits non-zero at
the first failed step, naming the step. **Recovery is always the same — fix the
cause, re-run the whole script** — which is only true because every step is
check-then-act and because no step enables anything: a failed run leaves the
host inert (nothing started, nothing scheduled) and the dev checkout untouched.

What each step checks, and what a failure leaves behind:

1. **Service account** — `getent passwd manta-trading`; create only if absent
   (system account, nologin shell, home `/opt/manta-trading`). If it exists,
   verify shell and home match and continue; if it exists with a different
   shape, **abort** rather than adopt it — a colliding or hand-made account is
   a decision for the PM, not a silent fixup. A re-run after any later failure
   takes the "exists and matches" path.
2. **Checkout** — if `/opt/manta-trading/.git` is absent, remove any leftover
   directory (a died-mid-way `git clone` leaves either nothing or a partial
   tree with no usable `.git`) and clone at the ref passed as an argument. If
   `.git` is present: `git status --porcelain` must be empty and `origin` must
   match, else **abort untouched**. That guard is about *local modifications*
   only — a clean checkout sitting at some other ref is fetched and checked out
   to the requested ref, so it never blocks a retry.
3. **Venv** — `uv sync --frozen` as the service account. `uv sync` is itself
   idempotent and resumable, so a partial `.venv` from a dropped connection is
   reconciled by the next run; the script does **not** delete `.venv` on
   failure. It then verifies `/opt/manta-trading/.venv/bin/mt --version`, since
   every `ExecStart` depends on that exact path, and stops hard if that fails.
   A genuinely corrupt venv is escalated in the runbook as `rm -rf
   /opt/manta-trading/.venv` + re-run — deliberately a human step, not an
   automatic wipe.
4. **Env file** — installs `/etc/manta-trading.env` from the example *only if
   the file does not exist*; an existing file is never overwritten or merged,
   so the PM's filled-in credentials survive every re-run. Ownership and mode
   (0640 root:manta-trading) are re-asserted on every run.
5. **Unit files + journald drop-in** — copied unconditionally; the repo is the
   source of truth and these files carry no host-local state, so overwriting is
   correct. A failure mid-copy leaves some units present but unreferenced —
   inert, because nothing is enabled — and the re-run completes the set.
6. **`systemctl daemon-reload`** — runs unconditionally at the end of every
   run, including runs where step 5 changed nothing. There is therefore no
   reachable state where unit files are installed but the reload was skipped:
   either the script reached this step, or it died earlier and the re-run
   performs it. Reload runs last so a half-copied unit set is never loaded.

On success the script prints the next two steps (fill the env file, then run one
pass by hand) and restates that nothing has been enabled.

### Code changes

Minimal, by design (this slice is deployment + documentation):

- `src/manta_trading/cli/commands/serve.py` — `--workers` help text: replace
  "Run the daemon in a separate terminal; slice 155 adds supervised launch"
  with a reference to the `mt-serve` systemd unit.
- New files under `deploy/`: five unit files plus `manta-acquisition.slice`,
  `manta-trading.env.example`, `install-production.sh`. The two `.service.tmpl` files are deleted —
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
  cheap re-runs) survives — it now explains timer behavior. Gains two new
  procedures: **adding a source** (copy a unit pair, change `ExecStart`, pick a
  non-colliding `OnCalendar`, set `Slice=`, install, enable) and **pausing a
  source** (`disable --now` is the pause that survives reboot; `mask` is the
  guardrail; resuming fires a `Persistent=true` catch-up immediately, with the
  stamp file named as the escape hatch).
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
11. Pause works and survives a reboot: `systemctl disable --now
    mt-minute-pass.timer` removes it from `list-timers`, and it is still
    absent after a reboot that brings back daily and `mt-serve`; `enable --now`
    restores it (and fires the catch-up pass).
12. `systemctl stop` on a *running* pass exits cleanly via the SIGTERM path
    (journal shows the clean-exit log line, not `Killed`/`signal=KILL`), and
    `acquisition_state` shows a resumable position.
13. `install-production.sh` re-runs cleanly on an already-installed host:
    exit 0, no account recreated, `/etc/manta-trading.env` unchanged
    (checksum before/after), nothing enabled that was not already enabled.

### Verification Walkthrough

As executed 2026-08-22/23 on manta9000 (PM ran all `sudo` steps; measured
results in `user/notes/2026-08-23-916-verification-results.md`). Reproducible
by an external agent; expected outputs noted inline.

```bash
# 0. Deploys use a READABLE TAG, never a raw SHA (PM rule, 20260823).
#    Tag the commit to deploy, e.g.:
git tag -a prod-YYYYMMDD -m "production deploy: <what>" && git push origin prod-YYYYMMDD
# Going forward, prefer version tags (vX.Y.Z matching pyproject) so
# `mt --version` on the host names the deploy.

# 1. Install (inert — enables nothing). Idempotent; re-run after any failure.
sudo deploy/install-production.sh --ref prod-YYYYMMDD
sudo deploy/install-production.sh --ref prod-YYYYMMDD   # second run: exit 0, env
                                          # file sha256 unchanged, nothing enabled
sudoedit /etc/manta-trading.env           # fill MT_TIMESCALE_DB_URL (DML app
                                          # credential) + MT_EODHD_API_KEY; never
                                          # paste MT_TIMESCALE_MAINTENANCE_URL
# Observed: transient network failure mid-run recovers by re-running unchanged.
# After first install, future runs also work from the /opt copy itself:
#   sudo /opt/manta-trading/deploy/install-production.sh --ref <tag>

# 2. One supervised pass per cadence, no cutover yet
sudo mt-run daily     # live output; Ctrl-C detaches (pass keeps running)
# expected end: "Pass complete: mt-daily-pass.service exited 0 (success)"
# verify identity: journalctl -u mt-daily-pass.service -o verbose _COMM=mt -n 1
#   → _UID=997(manta-trading), _SYSTEMD_UNIT=mt-daily-pass.service,
#     _SYSTEMD_SLICE=manta-acquisition.slice, _CMDLINE=/opt/.../mt ...
sudo mt-run minute    # same; a large backfill may run for hours — a clean
                      # operator stop (systemctl stop) is a valid completion:
                      # journal shows "received signal 15 — initiating clean
                      # exit", exit 0, NO "Killed"/SIGKILL line
# Observed: daily 47min/373MB; minute (first catch-up) 10h14m/1.5G, clean-stopped.

# 3. Cutover — the one explicit step
sudo systemctl enable --now mt-daily-pass.timer mt-minute-pass.timer mt-serve.service
systemctl list-timers 'mt-*'    # 12:35 & 13:05 UTC next-fires
curl -s localhost:8100/api/v1/health   # {"status":"ok","db":"ok",...}
# Observed: first autonomous firing 06:35:07 local, +7s timer jitter, pass
# completed 34min later, exit 0 — no human involved.

# 4. Crash supervision
sudo kill -9 "$(systemctl show -p MainPID --value mt-serve.service)"
sleep 15; systemctl show mt-serve.service -p NRestarts   # 0 → 1, new MainPID,
                                                         # API answers again
# Observed: restart ~10s after kill (RestartSec=10s).

# 5. Clean stop of a RUNNING pass
sudo systemctl start --no-block mt-daily-pass.service; sleep 20
sudo systemctl stop mt-daily-pass.service
# ("its triggering units are still active" is informational — expected)
journalctl -u mt-daily-pass.service -n 10   # "received signal 15" then
                                            # "Deactivated successfully"; no SIGKILL
# Observed: signal→exit in <100ms (worst case is one 60s runner sleep + the
# in-flight symbol; TimeoutStopSec=300 covers it).

# 6. Pause, reboot, resume (pause must survive the reboot)
sudo systemctl disable --now mt-minute-pass.timer
sudo reboot
# after boot, with NO operator action:
systemctl is-active mt-serve.service        # active (~12s after boot, observed twice)
systemctl list-timers 'mt-*'                # daily listed; minute STILL absent
sudo systemctl enable --now mt-minute-pass.timer
# if a scheduled elapse was missed while paused, the catch-up fires IMMEDIATELY
# (observed: enable 08:08:40 → pass start 08:08:40). Escape hatch when a resume
# must not backfill: delete /var/lib/systemd/timers/stamp-mt-minute-pass.timer first.

# 7. Rollback rehearsal (dev checkout was never modified)
sudo systemctl disable --now mt-daily-pass.timer mt-minute-pass.timer mt-serve.service
cd ~/source/repos/manta/trading-data && uv run mt data daemon run --daily --stop-when-done
sudo systemctl enable --now mt-daily-pass.timer mt-minute-pass.timer mt-serve.service

# 8. Day-to-day operation is one front door (added by this slice):
mt-run status                  # what's running, last results, timers
mt-run data caggs status       # any production mt command, any directory
```

Timer *firing* was nevertheless observed live (step 3) in addition to the
`systemctl start` equivalence — no task waited on it; it happened during
Group G work.

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

## Design review disposition (20260822)

Review: `user/reviews/916-review.slice.supervised-production-services-systemd-units-and-a-real-install-path.md`,
z-ai/glm-5.2, verdict CONCERNS (one concern, three passes, one note), against
`24ce883`. (A prior file at that path held a bogus UNKNOWN verdict from an
unregistered model alias and is archived under `reviews/archive/`; it was never
a real review of this design.)

- **F003 (concern) — install-script failure recovery implicit, not per-step.**
  Valid. The design claimed idempotency and a local-modifications guard without
  saying what any individual step does when it fails. Answered by the new
  **Install script failure recovery** subsection, which takes the finding's
  three questions directly: a failed `uv sync` leaves `.venv` in place and is
  reconciled by re-running — the local-modifications guard covers the checkout,
  not the venv, so it cannot block a clean retry; `daemon-reload` runs
  unconditionally at the end of every run, so "files installed, reload skipped"
  is unreachable; an existing service account is verified and skipped, and only
  a *mismatched* account aborts.
- **F001, F002, F004 (pass), F005 (note).** No action.

Re-reviewed at `919df58` after the three multi-source forward-compatibility
folds (`TimeoutStopSec`, naming pattern + add-a-source and pause procedures,
empty `manta-acquisition.slice`) and the arbitration deferral: **PASS**, five
passes and one note, no concerns. The re-review specifically confirms F003
closed (per-step install failure recovery), that the deferral reads as
complexity resisted rather than scope dropped, and that the maintenance band
still holds — the `/opt` install is new infrastructure but corrective of the
deficiency the plan entry named. Design is closed for Phase 5.

## Implementation Notes

- Suggested order: unit files + slice unit + env example → install script →
  serve.py help text → runbook rewrite (including the add-a-source and pause
  procedures) → host install/verify (PM) → cutover (PM) → backup-and-restore
  pointer + slice-plan entry update.
- Task breakdown must mark every root/host step as PM-executed (the 917
  pattern); stage all files in the repo — multi-line pastes into the host zsh
  are unreliable.
- The install script is the only shell of substance; keep it plain (no
  templating engine — the fixed account name removed the need) and idempotent.
- Effort: 3/5 — the three forward-compatibility items are a documented
  pattern, one `TimeoutStopSec=` line, and an empty slice unit; none adds a
  mechanism. Cross-source arbitration, which would, is deferred. Risk: Med, concentrated in the
  cutover as the entry states.
