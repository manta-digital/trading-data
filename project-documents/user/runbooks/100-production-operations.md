---
docType: runbook
project: trading-data
scope: project-wide
host: <prod_host>
dateCreated: 20260427
dateUpdated: 20260825
status: current
supersedes: the by-hand dev-checkout runbook (slice 916 made the /opt + systemd target real; see git history of this file)
---

# Production Runbook — `<prod_host>`

**Production is the dedicated install at `/opt/manta-trading`**, a git checkout
pinned to a chosen ref, owned by the `nologin` service account `manta-trading`,
supervised by systemd (slice 916). The developer checkout at
`~/source/repos/manta/trading-data` is no longer production — it is the dev
workspace and the rollback path, and is deliberately never modified by the
production tooling.

---

## Quick reference — operating production

### What runs by itself (no operator action)

| Unit | What | When | On failure |
|---|---|---|---|
| `mt-daily-pass.service` | one bounded daily acquisition pass | timer: **00:35 & 12:35 UTC** | next timer firing resumes it |
| `mt-minute-pass.service` | one bounded minute acquisition pass | timer: **01:05 & 13:05 UTC** | next timer firing resumes it |
| `mt-kalshi-pass.service` | one bounded Kalshi collection pass | timer: **hourly at :20 UTC** | next timer firing resumes it |
| `mt-serve.service` | API server (port 8100) | always on, starts at boot | auto-restart in 10s |

A reboot needs **no operator action**: everything above comes back, and
`Persistent=true` fires any pass schedule missed while the host was down.

### Commands

| I want to… | Command | sudo? |
|---|---|---|
| See what's running + latest output | `mt-run status` | no |
| Run a pass now, watch it live | `mt-run daily` / `mt-run minute` / `mt-run kalshi` | yes |
| Watch a running pass | `mt-run follow daily` (also `follow minute`, `follow kalshi`; Ctrl-C detaches, pass unaffected) | no |
| Check the API server | `systemctl status mt-serve` · `curl localhost:8100/api/v1/health` | no |
| See timer schedule | `systemctl list-timers 'mt-*'` | no |
| Run ANY production `mt` command | `mt-run <mt args>`, e.g. `mt-run data caggs status` | no¹ |
| Check the Kalshi catalog | `mt-run data kalshi status` | no¹ |
| Read a pass's full log | `journalctl -u mt-daily-pass.service -e` | no |
| Pause a source (survives reboot) | `systemctl disable --now mt-minute-pass.timer` | yes |
| Resume it (fires catch-up at once) | `systemctl enable --now mt-minute-pass.timer` | yes |
| Stop a pass mid-run (clean) | `systemctl stop mt-daily-pass.service` | yes |
| Roll back to manual operation | `systemctl disable --now mt-daily-pass.timer mt-minute-pass.timer mt-kalshi-pass.timer mt-serve.service` | yes |
| Update production code | see *Update procedure* below | yes |

¹ after a one-time `sudo usermod -aG manta-trading <user>` (new shell); otherwise prefix `sudo`.

Things to know before touching anything: **`mt-run` is the production front
door — there is never a reason to operate production from the dev checkout.** a completed pass shows
`inactive (dead)` + `status=0/SUCCESS` — that is success, not failure.
Passes are resumable: stopping or crashing one loses nothing; the next run
continues where it left off. `Ctrl-C` on `mt-run`/`journalctl` views only
detaches — it never kills a supervised pass. Nothing here touches the dev
checkout at `~/source/repos/manta/trading-data`.

---

## Current reality

| | |
|---|---|
| Checkout | `/opt/manta-trading`, owned by `manta-trading:manta-trading` |
| Tracks | a pinned ref (tag or SHA) — never a branch |
| Install | `deploy/install-production.sh` (PM-run, idempotent, enables nothing) |
| Venv | `uv sync --frozen` into `/opt/manta-trading/.venv`; uv is deploy-time only |
| Environment | `/etc/manta-trading.env`, mode 0640 `root:manta-trading` |
| Process manager | systemd — two pass timers + `mt-serve.service` |
| Log sink | journald, capped 2 GiB total / 200 MiB per file |
| Migrations | operator-run with the maintenance credential, never by units |

**`mt update` is not the update path.** It runs `uv tool install --upgrade` /
`pip install --upgrade` against PyPI, which is how an *interactive user* installs
the CLI. Production runs from a git checkout (slice 908 D7), so `mt update`
would either do nothing or install a second, unrelated copy.

## The units

| Unit | Kind | What it does |
|---|---|---|
| `mt-daily-pass.service` | oneshot pass | `mt data daemon run --daily --stop-when-done` |
| `mt-daily-pass.timer` | timer | fires the daily pass at **00:35 and 12:35 UTC** |
| `mt-minute-pass.service` | oneshot pass | `mt data daemon run --minute --stop-when-done` |
| `mt-minute-pass.timer` | timer | fires the minute pass at **01:05 and 13:05 UTC** |
| `mt-kalshi-pass.service` | oneshot pass | `mt data kalshi pass` — every registered Kalshi phase, in order |
| `mt-kalshi-pass.timer` | timer | fires the Kalshi pass **hourly at :20 UTC** |
| `mt-serve.service` | long-running | the API server; `Restart=on-failure` |
| `manta-acquisition.slice` | grouping | home for acquisition passes; carries no resource limits yet |

**What re-invokes the acquisition passes: the timers.** Each firing runs one
bounded pass that exits (`--stop-when-done`); this is the same invocation an
operator used to type by hand. The daily timer's 00:35 UTC firing lands just
after the 00:30 UTC daily gate opens, so the pass starts working immediately;
the 12:35 firing is a same-day catch-up — a drained pass exits with "no
actionable work" and no provider call, an interrupted one resumes. The minute
timer is staggered 30 minutes behind the daily so the two passes never start
simultaneously. `Persistent=true` on both timers fires any schedule missed
while the host was down. systemd never starts a service whose previous
activation is still running, so an overlong pass simply absorbs the next firing.

**Restart-after-reboot procedure: nothing.** That is the point of the slice.
`mt-serve` is `WantedBy=multi-user.target`, the timers are enabled, and
`Persistent=true` runs any missed pass at boot. Verify after a reboot with:

```bash
systemctl is-active mt-serve.service        # active
systemctl list-timers 'mt-*'                # both timers, sane next-fire times
```

## Environment file

`/etc/manta-trading.env` (mode 0640 `root:manta-trading`) is the **only**
configuration source for the units — no `.env` file exists in
`/opt/manta-trading`. It carries:

- `MT_TIMESCALE_DB_URL` — the DML-only application credential
- `MT_EODHD_API_KEY`
- optional tuning: `MT_LOG_LEVEL`, `MT_DAILY_CYCLE_RETRY_MINUTES`
- optional Kalshi tuning, all commented out in the skeleton:
  `MT_KALSHI_REQUESTS_PER_MINUTE` (lower it to ease 429s on `/events`;
  the default public budget is 300/min), and the authenticated pair
  `MT_KALSHI_API_KEY_ID` / `MT_KALSHI_PRIVATE_KEY_PATH` — **both or neither**

**Where the Kalshi private key goes.** The pass units set `ProtectHome=true`,
so a PEM anywhere under `/home` is invisible to the service. Put it beside the
environment file: `/etc/manta-trading-kalshi.pem`, mode `0640`
`root:manta-trading`, installed by hand — never by the install script, never
in the repository.

`MT_TIMESCALE_MAINTENANCE_URL` is **deliberately absent**: the DDL credential
stays out of service environments (slice 913). Migrations remain an operator
action from an interactive shell.

Edit with `sudoedit /etc/manta-trading.env`; the skeleton comes from
`deploy/manta-trading.env.example` and is installed only when the file does not
exist — the install script never overwrites it. The dev checkout keeps its own
`.env` in its working directory; the file itself is never copied to `/etc`.

---

## Install / reinstall

PM-run. Deploys use a **readable tag, never a raw SHA**: tag the commit
(`git tag -a v0.7.8 -m "..." && git push origin v0.7.8` — prefer version tags
matching `pyproject.toml` so `mt --version` on the host names the deploy),
then:

```bash
sudo /opt/manta-trading/deploy/install-production.sh --ref v0.7.8
```

(Works from any directory once production is installed; the first-ever install
runs the same script from a git checkout.)

Idempotent — every step is check-then-act, and recovery for any failure is
"fix the cause, re-run the whole script." It **enables nothing**; a failed or
partial run leaves the host inert. It aborts rather than adopt a `manta-trading`
account with an unexpected shape, and aborts untouched if `/opt/manta-trading`
has local modifications (a clean checkout at a *different* ref is fine — it is
fetched and moved to the requested ref).

**Corrupt venv escalation:** if re-running the script does not get
`/opt/manta-trading/.venv/bin/mt --version` working, remove the venv and re-run
— deliberately a human step, never automatic:

```bash
sudo rm -rf /opt/manta-trading/.venv
sudo deploy/install-production.sh --ref <same-ref>
```

## Update procedure (production)

PM-executed. Move the pinned checkout, resync, restart the long-running unit:

```bash
sudo -u manta-trading git -C /opt/manta-trading fetch --tags origin
sudo -u manta-trading git -C /opt/manta-trading checkout --detach <ref>
cd /opt/manta-trading && sudo -u manta-trading env HOME=/opt/manta-trading UV_CACHE_DIR=/var/cache/manta-trading/uv UV_PYTHON_INSTALL_DIR=/var/cache/manta-trading/python uv sync --frozen
sudo systemctl restart mt-serve.service
```

(Equivalently: re-run `deploy/install-production.sh --ref <ref>`, then restart
`mt-serve` — the script performs exactly these steps plus the inert re-checks.)

**A release that adds a new unit needs the install script run twice.** Bash
parses the entire script — including its `UNITS=( … )` array — before running
it, so the first invocation is the *old* copy already on the host: it moves
the checkout to the new ref and installs the new `mt-run`, but iterates the
old unit list, so the new unit is never copied. The symptom is
`Unit mt-<name>-pass.service not found` from `mt-run <kind>` even though
`mt-run` clearly knows the kind. Run the script a second time — it is
idempotent and enables nothing — then confirm with
`systemctl list-unit-files 'mt-*'`.
Pass units need no restart: each timer firing starts a fresh process, which
picks up the new code at its next firing.

**Verify the update took:**

```bash
/opt/manta-trading/.venv/bin/mt --version
```

**Apply migrations only if the release notes say a slice added one** — operator
step, interactive shell, maintenance credential (never in the unit
environment). Migrations are organised in **tracks** (`mt data migrate
apply --help` lists them: `minute`, `daily`, `kalshi`); each track is applied
separately and `--track` defaults to `minute`. All tracks share one ledger
(`schema_migrations`) on the trading database.

```bash
cd ~/source/repos/manta/trading-data
uv run mt data migrate status                     # pre-flight: what the minute track would apply (app credential)
uv run mt data migrate apply                      # minute track (the default) — needs MT_TIMESCALE_MAINTENANCE_URL
uv run mt data migrate status --track kalshi      # pre-flight for the kalshi track
uv run mt data migrate apply --track kalshi       # kalshi track (schema `kalshi`; slice 261+)
```

`status` reads with the application credential and is always safe; `apply`
refuses to run without the maintenance credential. A second `apply` of an
already-applied track prints `0 migration(s) applied` — re-running is harmless.

**Rehearse on the test cluster first** (runbook 400) when a track is new or
the change is more than additive: create a throwaway database there, apply the
minute track from bare, then the new track, and read `status` for both — the
same sequence the integration tests run, but through the real CLI and the
maintenance-credential path. Point *both* `MT_TIMESCALE_DB_URL` and
`MT_TIMESCALE_MAINTENANCE_URL` at the throwaway database for the rehearsal
(shell-local `export`, never in `.env`), and drop it afterwards.

**There are no down-migrations.** The runner only moves forward; rolling back
the *code* (below) does not roll back an applied migration, and every track is
written to be additive and idempotent so that is safe. Removing a track's
objects is a PM-only manual action on a throwaway or explicitly designated
database — e.g. for the kalshi track, `DROP SCHEMA kalshi CASCADE` plus
deleting its `kalshi_*` rows from `schema_migrations`; the integration tests
prove that re-applying afterwards succeeds.

The dev checkout keeps its own update procedure (`git pull` + `uv sync` on
`main`); nothing about it changed.

---

## Running the acquisition passes

Normally, nobody runs them — the timers do. To run one out of schedule
(supervised, correct user, correct environment), use the operator front-end
installed at `/usr/local/bin/mt-run`:

```bash
sudo mt-run daily          # or: sudo mt-run minute — live output, like a manual run
sudo mt-run kalshi         # one Kalshi collection pass, same treatment
mt-run status              # what's running now, latest output, last results, timers
mt-run follow daily        # re-attach to a running pass's live output
```

`sudo mt-run daily` streams the pass output to your terminal and exits with
the pass's real exit code — the pre-systemd experience, with one improvement:
**Ctrl-C detaches your view and the pass keeps running** (a manual run dies
with the terminal). Under the hood it is `systemctl start --no-block` plus a
journal follow; a bare `systemctl start` on a oneshot blocks silently until
the pass exits, which is why the wrapper exists.

The manual form still works from the dev checkout and is the rollback path:

```bash
cd ~/source/repos/manta/trading-data
uv run mt data daemon run --daily --stop-when-done    # or --minute
uv run mt data kalshi pass                            # the Kalshi equivalent
```

`mt data kalshi pass` takes only `--events-file` and `--json`; it deliberately
has no phase or replay options, so the timer's invocation can never carry one.
The replay and repair levers live on `mt data kalshi sync` (`--settled-since`),
which runs the catalog phase alone. **`--events-file` is a hand-run tool only**
— under the unit, `PrivateTmp=true` and `ProtectSystem=full` mean the path you
name is not the path you get; the journal is the supervised run's event log.

`--stop-when-done` makes each of these **one pass, then exit** — they are not
long-running processes despite the `daemon` subcommand name.

**Telling a supervised run from a manual one:** supervised runs carry the unit
in the journal — `journalctl -u mt-daily-pass.service` shows only supervised
runs, and `journalctl _SYSTEMD_UNIT=mt-daily-pass.service --output=verbose`
shows the field explicitly. A manual run from a shell has no `mt-*` unit (it
logs under the user session) and runs as the login user, not `manta-trading`.

### Timing constraints that look like failures but are not

- **The daily gate opens at 00:00 UTC + `DAILY_CYCLE_START_OFFSET` (30 min).**
  A daily pass started between 00:00 and 00:30 UTC **waits** for the gate
  rather than exiting — up to 30 minutes of apparent hang, announced by an INFO
  line (and re-announced every 5 minutes). This is deliberate (slice 912,
  issue #6). The 00:35 UTC timer firing lands after the gate precisely to avoid
  this wait; you will only see it on out-of-schedule manual starts.
  Interrupting during the wait takes up to 60s to take effect.
- **After a daily pass completes, the next is held for
  `DAILY_CYCLE_RETRY_INTERVAL` (default 30 min).** Tunable per-invocation with
  `--daily-retry-minutes`, or by environment with `MT_DAILY_CYCLE_RETRY_MINUTES`
  (which the unit reads from `/etc/manta-trading.env`). A timer firing inside
  the hold sleeps it out — up to 30 minutes of `activating` state — then
  proceeds; this is the pass working correctly, not hanging.
- **Re-running the daily pass after it has completed for the day is cheap and
  safe.** It derives the work list from `acquisition_state`, finds nothing
  pending, logs "no actionable work", and exits without any provider call. This
  is why the 12:35 catch-up firing costs nothing on a normal day.
- **An interrupted daily pass resumes at the symbols it never reached**, within
  the same UTC day, without re-fetching what it already did (slice 912, issue
  #7). Re-invoking after an interruption — including the next timer firing —
  is the correct recovery action.

---

## Checking state

**Is production running?** systemd answers directly now:

```bash
systemctl status mt-serve.service              # API server up, restart count
systemctl list-timers 'mt-*'                   # next/last firing per pass
systemctl status mt-daily-pass.service         # last pass result + recent log
journalctl -u mt-daily-pass.service -n 50      # last pass output
systemctl status manta-acquisition.slice       # pass units, while one runs
```

`systemctl status mt-daily-pass.service` showing `inactive (dead)` with
`status=0/SUCCESS` is a **completed** pass, not a failure — oneshot units do
not stay active.

**What did acquisition actually do?** The database remains the ground truth.
Always set a statement timeout — production holds a multi-billion-row
hypertable, and an unbounded probe against it is what caused the 2026-07-20
incident:

```sql
SET statement_timeout = '15s';

-- what the daily pass has attempted, most recent first
SELECT symbol, last_attempt_ts, last_attempt_outcome
  FROM acquisition_state
 WHERE granularity = 'daily' AND provider = 'eodhd'
 ORDER BY last_attempt_ts DESC NULLS LAST
 LIMIT 20;
```

`daemon_heartbeat` is frequently empty and is not a reliable liveness signal;
`acquisition_state` is.

### Kalshi

**Two status layers, and they answer different questions.**

```bash
mt-run status                       # did the pass RUN? (systemd: result, exit code, timers)
mt-run data kalshi status           # did the pass ACHIEVE anything? (the database)
```

**`No such command 'kalshi'` from `mt-run` means production is on old code**,
not that anything is broken. `mt-run` execs
`/opt/manta-trading/.venv/bin/mt` — the pinned production binary — so the
Kalshi commands appear only once a release containing them
(v0.9.0 or later) has been installed there. Your shell's own `mt` may
already be newer; `mt --version` and `mt-run --version` answer for two
different installs. Fix: run the *Update procedure* above.

The first is systemd's own record — whether the unit ran, when, and how it
exited. The second reads `kalshi.sync_state` and the catalog tables: the last
full sync, the settlement watermark, series/event/market counts, and the
awaiting-settlement set. A pass can exit 0 and still tell you little; a
watermark that has not moved across several firings is the signal worth acting
on.

**Cold start runs long, once.** A catalog that has never synced drains the
settled stream from Kalshi's historical cutoff — millions of rows and hours,
not the 2–3 minutes a steady-state pass takes. That first drain is normally
done by hand from the dev checkout **before** the timer is enabled. The unit
sets `TimeoutStartSec=infinity` so a legitimate catch-up is never killed
mid-pass.

**A firing while a hand-run `sync` holds the lock exits 1, and that is fine.**
Both commands take the same session-level advisory lock, so only one writer
ever runs. The timer's pass fails preflight with `another kalshi sync holds the
run lock`, the unit shows `failed`, and the next hour's firing succeeds once
the hand-run finishes. Hourly exit-1s during a long manual sync are expected —
they are the mutual exclusion working, not a defect.

**Exit codes** are the same taxonomy `sync` uses: 0 ok · 1 preflight (config
missing, database unreachable, track not applied, lock held) · 2 provider abort
· 3 partial (item errors — a market whose status the model does not know) · 4
storage abort. Exit 3 fails the unit **on purpose**: if Kalshi starts serving a
status outside the known set, every pass fails visibly until the one-line fix
ships, rather than succeeding while rows are silently skipped.

**Applying the Kalshi schema track** is the normal migration step under
*Update procedure* — the units never run migrations:

```bash
mt data migrate apply --track kalshi        # with the maintenance credential
```

**Is the rate budget too high?** The client retries a 429 with backoff and logs
a WARNING per retry, so the journal answers it directly:

```bash
journalctl -u mt-kalshi-pass.service --since '7 days ago' | grep -c retry
```

A handful per pass is the designed behavior absorbing a per-endpoint limit. If
that count grows into the hundreds, lower `MT_KALSHI_REQUESTS_PER_MINUTE` in
the environment file — no code change is involved.

Never run an expression aggregate over a compressed hypertable — it decompresses
everything. If a query outlives its client-side timeout, cancel the backend
rather than assuming it stopped.

---

## Adding a source

EODHD daily/minute are the first two of several sources. The units are an
instance of a pattern, not one-offs:

```
mt-{source-or-cadence}-pass.service + .timer    — a bounded pass
mt-{name}.service                               — a long-running service
```

(The current pass units omit the source because EODHD is the only provider
today; a second daily-cadence provider forces the fuller
`mt-{source}-{cadence}-pass` form.)

Checklist for a new bounded pass (e.g. a Kalshi pass):

1. Copy a pass unit pair (`mt-daily-pass.service` + `.timer`) in
   `deploy/systemd/` under the new name.
2. Change `ExecStart` to the new pass invocation (keep `--stop-when-done`
   semantics: one bounded pass, then exit).
3. Pick an `OnCalendar` that collides with no existing timer's (see
   `grep OnCalendar deploy/systemd/*.timer`); keep `Persistent=true`.
4. Set `Slice=manta-acquisition.slice` in the service.
5. Add the unit pair to the `UNITS` list in `deploy/install-production.sh`.
6. Add the kind to `KINDS` in `deploy/mt-run` — that one line is what gives
   the source its `mt-run {kind}`, `mt-run follow {kind}`, and its row in
   `mt-run status`; the wrapper derives the unit name from the kind.
7. PM: re-run the install script, then `sudo systemctl enable --now
   mt-{name}-pass.timer`.

Two limits of the pattern, stated so they are not rediscovered:

- `Type=oneshot` + timer suits a **bounded** pass. A streaming subscription (a
  plausible shape for Databento tick capture) is a `Type=simple` unit like
  `mt-serve` — with historical backfill as a separate pass unit.
- systemd serializes a unit only against **itself**. Two different pass units
  run concurrently with no priority and no queue; cross-source arbitration does
  not exist yet (Future Work 4 in the foundation-cleanup slice plan). Until it
  does, avoid overlap by choosing `OnCalendar` times by hand.

## Pausing a source

Three levels, each per-source:

- `sudo systemctl stop mt-minute-pass.timer` — no further firings **this boot**;
  a pass already running is untouched. Returns after a reboot (still enabled).
- `sudo systemctl disable --now mt-minute-pass.timer` — **the pause that
  survives reboot**: stops it now and removes the boot symlink, until an
  explicit `enable --now`.
- `sudo systemctl mask mt-minute-pass.service` — nothing can start it, not even
  a manual `start`. A guardrail for "leave this alone while X runs".

`sudo systemctl stop mt-daily-pass.service` stops a pass that is *currently*
running: the runner traps SIGTERM and exits cleanly between symbols. The unit
sets `TimeoutStopSec=300` for this — the runner's sleeps are capped at 60s, so
a clean stop can legitimately take a minute or two; a `Killed`/`signal=KILL`
line in the journal after a stop means something is wrong.

**The Kalshi pass stops differently, and that is by design.** It installs no
SIGTERM handler and sets no `TimeoutStopSec`: every unit of its work is already
safe to lose (each catalog page is its own transaction, the settled watermark
advances only per fully walked window), so it dies where it stands and the next
firing re-walks at most one page or window. In the journal a normal
`systemctl stop mt-kalshi-pass.service` therefore reads
`code=killed, status=15/TERM` followed by `Deactivated successfully`, and
systemd records `Result=success` — **that is a clean stop, not a crash**. A
`status=9/KILL` line is not: it means SIGTERM was ignored, which this pass
never does.

**Resuming fires a catch-up immediately.** `Persistent=true` means
`sudo systemctl enable --now mt-minute-pass.timer` after a pause runs the
missed schedule at once — usually the intent, since the pass resumes/backfills
safely. When a resume should **not** backfill, delete the stamp file first:

```bash
sudo rm /var/lib/systemd/timers/stamp-mt-minute-pass.timer
```

The Kalshi timer pauses and resumes the same way
(`sudo systemctl disable --now mt-kalshi-pass.timer`). Resuming it after a long
pause makes the next pass a catch-up: it walks several settled windows instead
of one and logs a `settled window …` line per window, which is the progress
signal to watch.

## Rollback

Disable the three units and fall back to the by-hand procedure from the dev
checkout — which was never modified:

```bash
sudo systemctl disable --now mt-daily-pass.timer mt-minute-pass.timer \
                            mt-kalshi-pass.timer mt-serve.service
cd ~/source/repos/manta/trading-data
uv run mt data daemon run --daily --stop-when-done     # etc., as before slice 916
uv run mt data kalshi pass                             # Kalshi, from the checkout
```

Re-enabling them restores the supervised state (and fires the
`Persistent=true` catch-up). Each source rolls back independently — disabling
the Kalshi timer leaves the EODHD timers untouched.

Rolling back code does **not** undo applied schema migrations — there are no
down-migrations (see the migrations note under *Update procedure*). Older code
runs unchanged against a newer additive schema.
