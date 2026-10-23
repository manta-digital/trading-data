---
docType: runbook
project: trading-data
scope: project-wide
host: <prod_host>
dateCreated: 20260427
dateUpdated: 20261016
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
| `mt-minute-pass.service` | one bounded minute acquisition pass | timer: **13:05 UTC** | next timer firing resumes it |
| `mt-kalshi-pass.service` | one bounded Kalshi collection pass | timer: **hourly at :20 UTC** | next timer firing resumes it |
| `mt-serve.service` | API server (port 8100) | always on, starts at boot | auto-restart in 10s |

`mt-tick-pass.service` is **not** in this table: it has no timer and runs only
when an operator starts it (`sudo mt-run tick`).

A reboot needs **no operator action**: everything above comes back, and
`Persistent=true` fires any pass schedule missed while the host was down.

### Commands

| I want to… | Command | sudo? |
|---|---|---|
| See what's running + latest output | `mt-run status` | no |
| Run a pass now, watch it live | `mt-run daily` / `mt-run minute` / `mt-run kalshi` / `mt-run tick` | yes |
| Collect and load futures ticks (manual only) | `mt-run tick` — acquisition then ingest, one `pass_runs` row; run `mt-run data tick pass --estimate-only` first | yes |
| Watch a running pass | `mt-run follow daily` (also `follow minute`, `follow kalshi`, `follow tick`; Ctrl-C detaches, pass unaffected) | no |
| Check the API server | `systemctl status mt-serve` · `curl localhost:8100/api/v1/health` | no |
| See timer schedule | `systemctl list-timers 'mt-*'` | no |
| Run ANY production `mt` command | `mt-run <mt args>`, e.g. `mt-run data caggs status` | no¹ |
| Check the Kalshi catalog | `mt-run data kalshi status` | no¹ |
| See what the tick tier holds | `mt-run data tick status` (also `coverage`, `active`) — see *Operating the tick tier* | no¹ |
| Pull ES ticks or bars | `mt-run data tick get 1m --product ES --start D --end D` (`--csv > file` for bulk) | no¹ |
| Investigate a tick unit, job or contract | `mt-run data tick debug units --state verified` (also `requests`, `definitions`) | no¹ |
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
| `mt-minute-pass.timer` | timer | fires the minute pass at **13:05 UTC** |
| `mt-kalshi-pass.service` | oneshot pass | `mt data kalshi pass` — every registered Kalshi phase, in order |
| `mt-kalshi-pass.timer` | timer | fires the Kalshi pass **hourly at :20 UTC** |
| `mt-health.service` | oneshot check | `mt data health` — read-only; non-zero exit means a failing check |
| `mt-health.timer` | timer | fires the health check **hourly at :50 UTC** |
| `mt-accounting-pass.service` | oneshot pass | `mt data accounting` — counts expected vs covered minute symbol-sessions; records the universe line `mt data overview` prints |
| `mt-accounting-pass.timer` | timer | fires the accounting pass **daily at 16:30 UTC**, after the day's collecting firings |
| `mt-tick-pass.service` | oneshot pass, **no timer** | `mt data tick run` — the acquisition pass over the full universe, then the ingest pass whatever acquisition did; one `pass_runs` row (kind `tick`). Started only by `sudo mt-run tick`. `TimeoutStartSec=4h` ends a hung run; a killed run loses nothing (units commit one at a time). `UMask=0002` keeps the shared archive group-writable |
| `mt-serve.service` | long-running | the API server; `Restart=on-failure` |
| `manta-acquisition.slice` | grouping | home for acquisition passes (minute, daily, kalshi, tick); `MemoryMax=256G`, a fixed ceiling above host RAM (effectively unbounded) |

### Adding a source or pass

Slice 916 named the units as instances of a pattern so this is a procedure,
not a design conversation. Slice 922 added `mt-accounting-pass` by following
it; the steps are:

1. **Copy the nearest unit pair.** `mt-health.*` for a read-only check,
   `mt-minute-pass.*` for a fetching pass. Keep `Type=oneshot`, the
   `manta-trading` user, `EnvironmentFile=/etc/manta-trading.env`, and the
   four hardening keys.
2. **Change `ExecStart`** to the `mt` subcommand, by its full venv path.
3. **Pick a non-colliding `OnCalendar`.** Check `systemctl list-timers 'mt-*'`
   and the table above. Two passes on the same minute contend for the
   database and the provider quota every firing.
4. **Name the cadence in `manta_trading.constants`** and add a drift test in
   `test/unit/deploy/test_units.py` asserting the constant against the unit's
   `OnCalendar`. `mt data overview` prints "next firing" from the constant, so
   a constant that drifts from its timer names a time nothing happens at.
5. **Add both units to the `UNITS` array and the cutover hint** in
   `deploy/install-production.sh`, and to the table above.
6. **If it is a pass an operator will run by hand**, add its kind to `KINDS`
   in `deploy/mt-run` — that is the only place the script needs it, since
   unit names are derived from the kind. A scheduled-only check (health,
   accounting) does not need this; `mt-run status` lists every `mt-*` timer
   regardless.
7. **Install and enable explicitly:** `sudo ./deploy/install-production.sh
   --ref <tag>` then `sudo systemctl enable --now mt-<name>.timer`. The
   installer enables nothing on purpose.

A streaming subscription is not this shape: that is a `Type=simple` unit like
`mt-serve`, with historical backfill as a separate pass unit.

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
- the Kalshi collection rule, five `MT_KALSHI_COLLECTION_*` lines (slice
  264; renamed in slice 265 because it governs candles **and** trades),
  commented out at their defaults — see *Kalshi* below
- `MT_KALSHI_TRADES_EXCLUDED_CATEGORIES` (slice 268) — the trades-tape
  category filter, commented out (empty = off); production intent `Crypto`
- the tick pass's five keys (slice 234): `MT_TICK_DB_URL` (the DML-only
  `tick_app` credential), `MT_DATABENTO_API_KEY`, `MT_TICK_SPEND_CEILING_USD`,
  `MT_TICK_SPEND_30D_CEILING_USD`, `MT_TICK_ARCHIVE_DIR`. Added once by
  `sudo deploy/add-tick-env.sh --from <checkout>/.env`, which copies exactly
  these five, never changes a key already present, never prints a value, and
  re-asserts mode 0640 `root:manta-trading`. The ceilings are the dev
  `.env`'s: both sides buy against one account and one manifest, so the
  rolling 30-day cap is one sum

**Where the Kalshi private key goes.** The pass units set `ProtectHome=true`,
so a PEM anywhere under `/home` is invisible to the service. Put it beside the
environment file: `/etc/manta-trading-kalshi.pem`, mode `0640`
`root:manta-trading`, installed by hand — never by the install script, never
in the repository.

`MT_TIMESCALE_MAINTENANCE_URL` and `MT_TICK_MAINTENANCE_URL` are
**deliberately absent**: the DDL credentials stay out of service environments
(slice 913). Migrations remain an operator
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
apply --help` lists them: `minute`, `daily`, `kalshi`, `tick`); each track is
applied separately and `--track` defaults to `minute`. The `minute`, `daily`
and `kalshi` tracks share one ledger (`schema_migrations`) on the trading
database; the `tick` track targets the tick database and its own ledger (see
*Provisioning a tick database*), and `apply` refuses a database whose ledger
belongs to another database's tracks.

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

## Is anything wrong? — the health check (slice 919)

`mt-health.timer` runs `mt data health` hourly at :50 UTC. It is read-only and
judges raw-data freshness, every continuous aggregate's materialization lag,
Kalshi phase recency, and **minute session mass** against the thresholds in
`constants.py` (`HEALTH_*`). Any breach exits non-zero, so the unit fails:

```bash
mt-run status                      # "== health: OK (...)" or "== health: FAILING ..."
journalctl -u mt-health.service -n 20   # the one FAIL line names the number and the limit
mt-run data health                 # run it now, same output
```

`OK` means nothing needs a human. `FAILING` names exactly one measured value
and its limit per failing check; fix the cause, and the next firing clears it.

Only the unit's run (`mt data health --record`) records its verdict in
`pass_runs`, which is what `mt data overview` shows. A manual run, from
`mt-run` or the dev checkout, prints the same report and exit code but records
nothing (#55): the dev `.env` differs from production's, so its verdict is not
production's.

**The tick rows (slice 234).** Two lines follow the primary checks:

- `tick database` is **informational**: it prints `OK`, or `INFO` with the
  tick line's state (`not configured`, `unreachable: …`, `migrations
  pending`), and never changes the exit code or the verdict count. A tick
  outage does not fail health.
- `tick retention` runs only when the tick database answered. It fails when
  a unit is `submitted` or `delivered` (not a provider hole, not reopened)
  and its job's download deadline is less than `TICK_RETENTION_WARN_DAYS`
  (7) away, or already past. The line names each job and deadline. **Action:
  `sudo mt-run tick`** — the pass downloads, verifies and ingests what is in
  flight. A deadline that passes first expires and reopens the unit, and the
  next pass buys that day again — a second purchase.

### `minute session mass` (slice 921)

This check judges how much data the last completed trading session actually
holds — bars per session-minute, and how many symbols reached a usable number
of bars — rather than how old the newest bar is.

It exists because age could not see the 2026-09-07 failure. Every session
still had bars, one per symbol at the session open, so freshness passed while
the universe collected ~45k bars/day against ~2.0M/day through 2026-08-27.

The judged session is the newest NYSE session whose collecting firing has
finished: the first `MINUTE_PASS_FIRING_TIMES_UTC` entry after its close, plus
`HEALTH_MINUTE_SESSION_COLLECTION_LAG`. In practice a session becomes judgeable
at 16:05 UTC the next day, for a regular and an early close alike (the 13:05
firing is the one after EODHD has published the day; see
`MINUTE_PROVIDER_PUBLICATION_LAG` for the measurements). A FAIL
reading `no completed session to judge` means the calendar has no qualifying
row — check `trading_sessions` and the calendar id, not the acquisition pass.

**The EODHD quota-headroom check was removed with this slice.** Normal
operation is designed to consume the whole daily allowance on backfill, so no
floor was right — 99,996 of 100,000 used was a healthy reading. What mattered
about a starved night was its consequence, which this check now measures
directly.

**Load test for the read bound.** The mass read degrades to exit 2 on a
statement timeout, which an operator cannot distinguish from silence, so the
bound is checked by a load-tier test. CI runs no test job (tracked as slice
907), so this is a manual gate — run it after any change to the mass query,
the cagg shape, or `HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT`:

```bash
export MT_TIMESCALE_TEST_URL=...   # the dedicated test cluster, never production
MT_RUN_LOAD_TESTS=1 uv run pytest test/load/test_921_minute_session_mass_nfr.py
```

### Minute sessions collected but truncated (slice 921)

The failure class: a session is *present* but holds a single bar, at the
session open. Age-based checks pass, `data status` looks normal, and the only
symptom is that the day is nearly empty.

**Is it happening?** One query — the truncation signature is `max(time)` for a
session at or before that session's open:

```sql
SELECT ts.session_date, max(m.time) AS newest, ts.session_open_utc
  FROM trading_sessions ts
  JOIN instruments i ON i.trading_calendar_id = ts.calendar_id
  JOIN minute_ohlcv m ON m.symbol = i.symbol
   AND m.time >= ts.session_open_utc AND m.time < ts.session_close_utc
 WHERE i.symbol = 'AAPL'
   AND ts.session_close_utc <= now()
 GROUP BY ts.session_date, ts.session_open_utc
HAVING max(m.time) <= ts.session_open_utc
 ORDER BY ts.session_date DESC LIMIT 10;
```

**The standing diagnostics.** Both are read-only and safe against production
from any host that has the URL:

```bash
# Counts across the active universe: truncated symbol-days, zero-width rows,
# session-open-ended terminal rows, rows straddling the repair window start.
uv run python scripts/repair_921_minute_sessions.py --check

# Every acceptance measurement with its pass/fail bar, including the same
# `minute session mass` verdict `mt data health` reports.
uv run python scripts/repair_921_minute_sessions.py --verify
```

`--check` walks ~13k symbols in about five minutes; the truncation measurement
itself is one grouped query (~260 s).

**Repairing.** `--apply` re-seeds the affected sessions through
`update_data_gaps` under the daemon's advisory lock — it writes no `data_gaps`
SQL of its own and never calls the provider; the next pass does the fetching.
It refuses to run while `mt-minute-pass.service` or `mt-daily-pass.service` is
active, and commits per symbol, so a run that dies is resumed by rerunning.
Rows before 2026-07-16 are never touched.

### `off-tape unevaluated` and `off-tape share` (slice 171)

Every minute bar carries an `off_tape` mark saying which of its open, high,
low and close are off-tape prints (late-reported, average-price,
prior-reference trades: the tall isolated wicks). Both lines judge the ET day
of the session `minute session mass` judges.

- **`off-tape unevaluated` FAIL** (`N of M bars have no off-tape mark`): some
  bars were committed without a mark. Both insert paths mark in the insert
  transaction, so this means a path skipped marking, or the history sweep
  has not reached that day (only possible before the sweep has finished).
  Action: run the sweep (below); it marks exactly the chunks holding unmarked
  bars. If the count reappears after a clean sweep, a writer is bypassing
  `mark_inserted`: file an issue with the line.
- **`off-tape share` FAIL** (`X of M bars marked (s%; bounds a–b)`): the
  day's marked share left `[OFF_TAPE_SHARE_FLOOR, OFF_TAPE_SHARE_CEILING]`
  (0.13%–0.69%, measured over the universe week of 2026-10-05, where it ran
  0.26%–0.35%). **Zero or near-zero** means the detector stopped running
  while bars still arrived. **A flood** means the provider changed its bars
  or the detector broke. Either way the raw bars are untouched and
  `prints=all` (the default) is unaffected; only `prints=eligible` readers
  see the effect. Compare a few marked bars by hand:
  `SELECT time, open, high, low, close, off_tape FROM minute_ohlcv WHERE
  symbol = 'SPY' AND off_tape <> 0 ORDER BY time DESC LIMIT 20;`
- `no completed session to judge`: same cause as for `minute session mass`.

### Off-tape history sweep: `mt data off-tape sweep` (slice 171)

Marks every `minute_ohlcv` chunk that still holds an unmarked (`NULL`) bar,
oldest first, one chunk per transaction. Needed once, after the release that
applies migration 062; new bars are marked at insert. Measured on the test
cluster: one production-sized chunk (10.1 M rows) in about 400 s, so all
history is about 50 h in one process. Passes stay live while it runs.

It needs the maintenance (DDL) URL, passed explicitly, from an interactive
shell (the URL is deliberately not in the service environment):

```bash
export MT_TIMESCALE_MAINTENANCE_URL='postgresql://...'      # interactive shell only
mt-run data off-tape sweep --maintenance-url "$MT_TIMESCALE_MAINTENANCE_URL" --dry-run
# chunks: 1216 total, 1216 to sweep (1215 compressed, 1 uncompressed)
nohup mt-run data off-tape sweep --maintenance-url "$MT_TIMESCALE_MAINTENANCE_URL" \
    > ~/off-tape-sweep.log 2>&1 &
```

Each chunk logs `off-tape sweep: <chunk> rows=N marked=s% elapsed=Ts`.

- **Resume:** rerun the same command. Progress is the data itself (a chunk is
  done when it holds no `NULL` mark); a killed run rolls its current chunk
  back and the rerun redoes it. Nothing else to clean up.
- **Done:** `--dry-run` reports `0 to sweep`, and
  `SELECT count(*) FROM minute_ohlcv WHERE off_tape IS NULL` is `0`.

| Output / exit | Meaning | Action |
|---|---|---|
| `swept N chunk(s)`, exit 0 | Every pending chunk is marked | none |
| `deferred while a minute pass runs: <chunk>`, exit 0 | The newest chunk was skipped because `pass_runs` shows an open minute pass (it writes there) | rerun after the pass ends |
| `still locked after retry: <chunks>`, exit 1 | A writer held those chunks past `OFF_TAPE_SWEEP_LOCK_TIMEOUT` (30 s) twice | rerun; the rest are done |
| `stopped: raw checksum changed in <chunk>`, exit 1 | Raw prices or volume differed after the rewrite. Should be impossible; the chunk rolled back | stop and investigate; do not rerun blindly |
| traceback, exit 1 | Any other failure; the chunk rolled back | read the error; rerun resumes |

Effects to expect while it runs:

- An insert into the chunk being rewritten waits for that chunk's
  transaction (at most one chunk rewrite, about 400 s). That only happens
  for deep backfill into old chunks.
- Each chunk's `UPDATE` logs a continuous-aggregate invalidation for its
  range. The minute caggs' policies look back 10 days, so older entries stay
  in the log. The values are unchanged (`off_tape` is in no aggregate); a
  later wide manual refresh re-materializes those ranges at its normal cost.

### Eligible rollup build: `scripts/cutover_172_eligible_rollups.py` (slice 172)

Releases migration 063 and fills the eligible 5m, 15m, 1h and 4h rollups
(`minute_*_ohlcv_next`) and `minute_coverage_next` beside the current ones.
Nothing a reader sees changes: bars, `mt data status`, `/api/v1/status`,
`mt data health` and the seeder keep reading the current objects until slice
173 swaps them in.

**When:** after the off-tape history sweep is done, that is when
`mt-run data off-tape sweep --maintenance-url "$MT_TIMESCALE_MAINTENANCE_URL" --dry-run`
reports `0 to sweep`. The script checks this itself and refuses before
building anything if a chunk still holds an unmarked bar: a window built from
unswept rows would stay wrong, because repair compares counts, not values.

**The one command**, as `manta` from the dev checkout at the release ref:

```bash
cd ~/source/repos/manta/trading-data && git fetch && git checkout vX.Y.Z
uv run python scripts/cutover_172_eligible_rollups.py vX.Y.Z
```

It holds the minute, daily and Kalshi timers (waiting out a running pass),
installs the ref, applies 063, restarts mt-serve and releases the timers.
Then it builds with the passes live: for 4h, 1h, 15m and 5m it pauses that
`_next` view's jobs, runs
`mt data caggs repair --target next --order newest-first`, and resumes them;
then `rebuild-coverage --family minute --target next` and its `--verify`;
then `mt data caggs verify --target next`. Jobs are found by view name each
time; nothing uses a job id.

**Reading the result:** the report is
`project-documents/user/notes/<date>-172-cutover.md` (the narration is the
`.log` beside it). It lists each step as PASS or FAIL with what it saw, rows
per `_next` view, the verify output, and each `_next` refresh job's last run
duration against its schedule. Exit 0 only when every step passed.

**After a failure or a kill:** fix the cause the report names and rerun the
same command. The release steps skip what is done (ref already installed, 063
already applied). The build resumes any `_next` job a killed run left paused,
and repair and rebuild-coverage skip what is already built.

| Report line | Meaning | Action |
|---|---|---|
| `N minute chunk(s) still hold unevaluated off-tape marks` | The history sweep is not finished | finish the sweep, then rerun |
| `could not measure the database volume` / `not this machine` | Free space for repair's headroom check could not be read | run on the database host; check `pg_lsclusters` |
| `repair of <view> exited N` | One rollup's sweep failed; its jobs were resumed | read the narration above it; rerun |
| `out of parity: <views>` | The build is not complete | rerun |

**Expected until slice 173:**

- `mt data caggs status` lists five extra rows, the `_next` views. That is
  correct; health and freshness checks never read them.
- Minute-cagg refresh work roughly doubles, because the `_next` policies run
  beside the current ones. The report's refresh-job lines show what it costs.
- A `_next` view's own refresh is paused while it is repaired. Its newest
  window is built first and its policy looks back 10 days, so a repair that
  runs less than that needs no catch-up refresh.

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
ships, rather than succeeding while rows are silently skipped. (It happened on
2026-09-01: `amended` appeared, the pass went `partial` for a few hours, and
v0.12.0 admits it with `kalshi_008_amended_status`.)

**Applying the Kalshi schema track** is the normal migration step under
*Update procedure* — the units never run migrations:

```bash
mt data migrate apply --track kalshi        # with the maintenance credential
```

**The pass has two phases since v0.10.0 (slice 264): catalog, then
candlesticks.** The candle phase collects 1-minute candles for the markets
the **collection rule** selects — by default, markets that traded in the
last 24 hours, excluding the Sports and Mentions categories and the
mention/say-titled series elsewhere. The rule is configuration: the five
`MT_KALSHI_COLLECTION_*` lines in `/etc/manta-trading.env` (defaults in the
skeleton, `deploy/manta-trading.env.example`; renamed in slice 265 — see
below), and `mt-run data kalshi status` prints the
rule in force on its `rule` line together with the `excluded by rule`
count, so a rule change is visible before and after it takes effect. `kalshi_005_candlesticks` must be applied during the update; a
firing between install and apply exits 1 with `kalshi track has pending
migrations: kalshi_005_candlesticks` — expected, not a defect, and the next
hour's firing succeeds once the migration is in. **The first firings after
the release run long — under an hour each, not minutes.** Observed on
2026-08-27: the first firing took 55 minutes for 6,538 batch requests — a
100,000-market slice of the finalized backlog (the per-pass cap is
`1,000 × 100` markets; dense markets pack only ~20 to a request) followed by
the live set's first sight (~1,100 requests, ~6 minutes, 24 hours of history
each). Each following firing takes another 100,000-market slice until
`status` shows `backlog remaining` at 0 (about four firings from the 250k
observed), then settles to the ~70-request steady state. A firing that lands
while the previous one still runs exits 1 on the run lock — expected. The
live set is planned **after** the backlog within a pass, so during the drain
live candles land in the pass's last minutes, not its first.

**The pass has three phases since slice 265: catalog, candlesticks, then
trades.** The trades phase walks Kalshi's exchange-wide public trade tape in
one-hour windows, oldest first, under one watermark (`mt-run data kalshi
status` → `tape through`), and stores every trade whose market the catalog
knows and the **collection rule** selects into `kalshi.trades`. **One rule
governs candles and trades**, so its settings were renamed from
`MT_KALSHI_CANDLE_*` to `MT_KALSHI_COLLECTION_*` — rename any of the five
you have set in `/etc/manta-trading.env` **before the first firing on the
new release** (commented-out lines need nothing): while an old
`MT_KALSHI_CANDLE_*` line is still set, every `mt` command — the pass, and
`status` and `migrate` too — fails at start naming the new variable, rather
than silently collecting under the default rule. `kalshi_006_trades` must
be applied during the update; a firing between install and apply exits 1
with `kalshi track has pending migrations: kalshi_006_trades` — expected,
and the next hour's firing succeeds once the migration is in. **The first
~10 days after the release drain the live tape from Kalshi's trades
cutoff** (about 60 days back): each hourly firing is capped at 3,000 tape
requests (~7 hours of tape, ~15 minutes) and `status` shows `tape through`
advancing ~7 hours per firing and the lag falling; when its `behind` flag
clears, a steady-state pass is ~800–900 requests and ~3 minutes. Since slice 268 the phase also applies the
**trades-tape category filter** (`MT_KALSHI_TRADES_EXCLUDED_CATEGORIES`):
rule-selected trades of the named categories are counted
(`excluded_by_trades_filter` in the per-window `filtered` log figure and
the phase event) but not stored, while their candles keep collecting —
after a cutover, verify `mt-run data kalshi status --json` shows
`trades.filter.tape_filtered_markets > 0` (the named typo check). A trade
for a market the catalog does not know — the multi-leg MVE tape, about 8%
— is counted (`unknown`) and dropped, never stored; the journal's
`trades unknown markets:` line lists the ticker prefixes so anything that is
not MVE is visible. Chunks of `kalshi.trades` older than 14 days compress
under their own policy; see it by hypertable name, never by job ID:

```sql
select job_id, scheduled, config, last_run_status, last_successful_finish
from timescaledb_information.jobs j join timescaledb_information.job_stats using (job_id)
where hypertable_schema = 'kalshi' and hypertable_name = 'trades' and proc_name = 'policy_compression';
```

Only the chunk under the watermark can ever be met mid-write. If a firing's
`trades window` journal lines show a window taking minutes rather than
seconds during the drain, that chunk was compressed by the policy: pause the
job for the remainder of the drain
(`alter_job(job_id, scheduled => false)`, the id resolved by the query above
at the time, with the maintenance credential — the application role cannot
alter jobs) and resume it after. Never automated — the historical phase
below writes into compressed chunks with the policy **on** (the 265
rehearsal measured no penalty) and only warns; this lever stays yours.

**The pass has four phases since v0.12.0 (slice 267): catalog,
candlesticks, trades, then historical.** The historical phase backfills
what Kalshi serves only from its archive, under one request cap sized from
the client's rate budget — `HISTORICAL_PHASE_MINUTES` (30) of it: **30,000
requests authenticated, 9,000 public** — so a firing never runs the phase
longer than about half an hour and the whole pass stays under ~40 minutes
during the drain. Each firing, in order: (0) **the archive walk, once** —
`GET /historical/markets` newest-first into the catalog, millions of settled
markets the live catalog never saw, the cursor saved after every page so the
cap or an abort resumes it; **the first firing after install runs hours**,
and a timer firing that lands while it runs exits 1 on the run lock —
expected, as in the cold start above; (1) candles for the finalized markets
behind the historical cutoff (`status` → `behind cutoff, uncollected`), up to
1,000 markets per firing while the tape is still descending and as many as
the budget allows once it is not; (2) the public trade tape walked
**backward** in one-hour windows from the live tape's floor toward
`HISTORICAL_TRADES_FLOOR` (2026-01-01T00:00Z, a constant in
`data/kalshi/constants.py` — extending the range later is that one edit;
the phase simply continues downward). About fifteen authenticated firings
reach the floor. `kalshi_007_historical_surface` must be applied during the
update; a firing between install and apply exits 1 with `kalshi track has
pending migrations: kalshi_007_historical_surface` — expected. **Reading the
status line:** `mt-run data kalshi status` prints
`Kalshi historical  tape 2026-07-01 → 2026-05-14 (floor 2026-01-01) ·
behind-cutoff candles remaining 7,994 · last phase …` — the historical tape
range grows downward every firing until it reads `floor reached`; `archive walk in
progress` replaces the range while the first walk is still running;
`Historical: never run` before the first firing. The trades block's
`coverage from` and `before coverage` measure against the **effective
floor** (the lower of the live floor and the historical watermark), so
`before coverage` shrinks as the tape descends. **An archived market whose
candles Kalshi will not serve** shows as `historical=partial` (exit 3, the
unit fails) with the ticker in the journal's `historical: <ticker> skipped`
line; it is retried every firing rather than aborting the drain. A market
whose candles take longer than 30 s to fetch and write logs
`kalshi historical slow market …` — the signal that candle writes into a
compressed chunk are slow; the pause lever above is the remedy, by hand.

**A dead terminal drops the view, not the pass.** `sudo mt-run kalshi`
streams the journal, but the pass is a systemd unit and keeps running if the
terminal crashes or the SSH session drops (observed 2026-08-27: a terminal
crash froze the display at `2100/6538` while the unit ran to completion).
`mt-run follow kalshi` re-attaches; `mt-run status` shows the last lines;
for a long watch use `tmux`. Chunks of `kalshi.candlesticks` older than 14 days compress
automatically under a TimescaleDB policy; see it — and its last run — by
hypertable name and procedure, never by a job ID, which regenerates
whenever the policy is recreated:

```sql
select job_id, scheduled, config, last_run_status, last_successful_finish
from timescaledb_information.jobs j join timescaledb_information.job_stats using (job_id)
where hypertable_schema = 'kalshi' and hypertable_name = 'candlesticks' and proc_name = 'policy_compression';
```

The historical phase (slice 267) writes months-old candles into compressed
chunks with this policy on and only warns when a market is slow; pausing the
job for a drain (`alter_job(job_id, scheduled => false)`, resolved by the
query above at the time) and resuming it after remains the manual lever.

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
  run concurrently with no priority and no queue. Calendar stagger is the
  arbitration mechanism, kept on purpose (slice 233): minute and daily share
  EODHD's quota, which is why they start 30 minutes apart, and the overlaps
  measured so far (Kalshi × tick, Kalshi × heavy minute pass) slowed nothing.
  Choose `OnCalendar` times by hand so passes do not overlap. Reopen this if a
  pass slows measurably when it overlaps another (`pass_runs` durations), or
  when a `Type=simple` streaming source arrives, since a continuous load
  cannot be staggered.

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

**The Kalshi pass stops differently, and it leaves the unit `failed`.** It
installs no SIGTERM handler and sets no `TimeoutStopSec`: every unit of its
work is already safe to lose (each catalog page is its own transaction, the
settled watermark advances only per fully walked window), so it dies where it
stands and the next firing re-walks at most one page or window.

Because it dies *by signal* rather than exiting, systemd records
`Result=signal` and the unit enters the `failed` state — verified on the host
20260825:

```
mt-kalshi-pass.service: Main process exited, code=killed, status=15/TERM
mt-kalshi-pass.service: Failed with result 'signal'.
```

**A stop you issued is not an incident**, even though it is reported as a
failure. The EODHD passes differ here: their runner traps SIGTERM and exits 0,
so a stop leaves them `success`. Practical consequences for the Kalshi unit:

- `mt-run status` shows `last run: signal` after a manual stop — expected if
  you just ran `systemctl stop`, worth investigating if you did not.
- The unit shows red/`failed` in `systemctl status` until its next run. There
  is no `Restart=`, so nothing retries in a loop; the next `:20` firing runs
  normally and clears it. `systemctl reset-failed mt-kalshi-pass.service`
  clears it immediately if the red bothers you.
- `status=9/KILL` is still the abnormal case: it means SIGTERM was ignored,
  which this pass never does.

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

## Operating the tick tier (slice 230)

**Where the tick commands run.** Since slice 234, `/etc/manta-trading.env`
holds the tick keys, so `mt-run data tick …` runs every tick command in
production, and `sudo mt-run tick` runs the production pass. The dev
checkout's `uv run mt data tick …` still works (`manta` is in group
`manta-trading`, and the archive is group-writable), but `mt-run` is the
production path.

**Stage a source under `/data`.** `manta-trading` cannot read `/home/manta`
(the units set `ProtectHome=true`). To adopt a job held under your home
directory, copy it under `/data` first, then `mt-run data tick adopt
--source /data/…`.

Every read below is provider-free: no Databento key, no lock, and no
archive directory. All of them refuse with exit 1 while a tick migration is
pending (`mt data migrate apply --track tick`).

### Reading state

| Question | Command |
|---|---|
| What is held, per product: sessions complete, awaiting ingest, in flight, holes; the active contract per roll rule | `mt data tick status` |
| Does `tick_trade` hold what the ledger says, session by session (the raw-count proof) | `mt data tick coverage --start D --end D` |
| Which contract a roll rule picks, now or over a range | `mt data tick active --product ES` · `… --rule v.0 --start D --end D` |
| The tick tier beside the minute universe | `mt data accounting` (a `futures tick universe:` line per product; the same buckets as `status`) |
| The newest stored tick beside the other sources | `mt data overview` (the `futures ticks` source line) |

On `accounting` and `overview`, a tick database that is unset, unreachable or
has pending migrations shows up as that line's text, and the command's exit
code is unchanged. A tick outage never turns the minute screen red. The tick
alarm is `health`'s `tick retention` row (see the health check section).

### Reading data: `mt data tick get`

```bash
# Continuous front month under the default rule (v.0), 1-minute bars
mt data tick get 1m --product ES --start 2024-09-16 --end 2024-09-20
# One contract's ticks, to a file (bulk reads: always --csv > file)
mt data tick get ticks --contract ESZ4 --start 2024-09-18 --end 2024-09-19 --csv > esz4.csv
# Session bars, JSON
mt data tick get 1d --product ES --rule c.0 --start 2024-11-04 --end 2024-11-08 --json
```

- **What to read.** `WHAT` is `ticks` or a bar size: `1m 5m 15m 1h 4h 1d`.
  `1d` is the whole session.
- **Bars stay inside sessions.** A bar never crosses a session boundary,
  so the first bar of a session starts at its open.
- **One contract per session.** A product read serves one contract per
  session, the one the rule picks, and never merges two. The header names
  each segment, for example `2024-09-16 → 2024-09-17  ESU4 (118)`.
- **Dates are session dates, and `--end` is EXCLUSIVE.** This is unlike
  `mt data get`, whose end is inclusive.
  - With no dates, the read covers every held session.
  - `--start` alone runs to the last held session. `--end` alone starts at
    the first.
  - A product read longer than `TICK_ROLL_MAX_SESSIONS` (1,300 sessions)
    refuses. Narrow the range.
- **Unresolved and incomplete sessions.** A session the rule cannot
  resolve is listed `unresolved` with its reason, and nothing is served for
  it. A session that is not fully held is served and listed `incomplete`.
  Either list makes the exit code 3.
- **Output.** Prices are exact decimals; JSON carries them as strings. An
  undefined quote price is empty in CSV and `null` in JSON. Times are UTC
  with nine fraction digits.
- **CSV.** Only the header and rows go to stdout. Segments and the lists go
  to stderr.
- **A read that drops partway** (the database goes away, or a statement
  times out) exits 4, and the output says it is partial:
  - text ends `PARTIAL: N row(s), then: …`;
  - CSV writes `partial output: …` to stderr;
  - JSON closes with `"complete": false`.
- **Closing the pipe** (`| head`) ends the read with exit 0.
- **Contracts.** An exchange symbol can name two contracts a decade apart.
  `get` then refuses with exit 1, listing both. Give `--start/--end` that
  separate them.
- **Size.** A multi-month read is millions of rows. Memory stays flat, but
  the terminal is the bottleneck.

### Investigating: `mt data tick debug`

```bash
mt data tick debug requests                       # jobs: range, adoption, job id, costs, times
mt data tick debug units --state verified         # units awaiting ingest
mt data tick debug units --fetch-status FAILED_RETRYABLE --start D --end D
mt data tick debug definitions --product ES --as-of 2024-09-18   # contracts valid at that session's open
mt data tick debug definitions --symbol ESZ4 --spreads --json
```

- These are plain reads: one table each, or `{rows, exit_code}` with
  `--json`.
- `--state` and `--fetch-status` take the values `--help` lists.
- Definitions show outrights only unless you pass `--spreads`.
- `--as-of` needs `--product`, whose calendar dates the session.
- To narrow to one unit or request, use `--json | jq`. There are no id
  filters.
- `debug` asserts nothing, so it exits 0 (including when there are no
  rows), 1 or 4, and never 3.

### Running passes by hand

The production run is `sudo mt-run tick` (both passes, recorded; see *The
units*). The verbs below run one step at a time.

| Step | Command | Note |
|---|---|---|
| Cost a request (free calls) | `mt data tick estimate --symbols ES.FUT --stype parent --start D --end D` | needs the Databento key |
| Buy what the universe wants | `mt data tick pass` (`--estimate-only` first; `--start/--end` narrow within the universe) | buys only within both spend ceilings |
| Load verified units | `mt data tick ingest` (`--unit-id N` to pick) | no Databento key |
| Record a job already on disk | `mt data tick adopt --job-id GLBX-… --source <dir or zip>` | free calls only |
| Retry exhausted or reopen holed units | `mt data tick reset --unit-id N` (or `--all`) | asks first unless `--yes` |

**Backfill is a universe edit, then `pass`.** The wanted range lives in
`TICK_UNIVERSE` (`src/manta_trading/data/tick/universe.py`). To backfill
an older range:

1. Widen the entry's `start`.
2. Run `mt data tick pass --estimate-only` to cost it.
3. Run `mt data tick pass`.

There is no `backfill` verb, just as the minute tier has none (`pull` plays
that role there).

### Adding a futures product (slice 232)

A new product is configuration plus a calendar, never surface code. GC was
added this way. Each step is a commit or a command; nothing is bought until
the PM sets the ceilings.

1. **Calendar.** Decide whether the product trades an existing CME schedule.
   Check it against CME's documents, not by assumption: GC needed its own
   (`CME_METALS`), because metals hours and holiday closes differ from equity
   and change from year to year. For a new schedule:
   - add a seed module beside `src/manta_trading/market/schema/seed_cme_metals_calendar.py`:
     the calendar row, `SEED_START`, `HOLIDAYS_SEEDED_THROUGH`, and every dated
     exception from 2020, each under a `# source:` comment naming the CME document.
     Read the product's own row in CME's holiday spreadsheets, PDFs or the
     trading-hours JSON (via Wayback; cmegroup.com refuses automated reads).
     Never copy another product's rows or extrapolate a year;
   - check every past row against Databento trade counts (free
     `metadata.get_record_count`: trades before each early close, none after it,
     none on a closed day), and cross-check against `pandas_market_calendars` from
     a scratch directory (`uv run --no-project --with pandas_market_calendars`,
     never a project dependency). Resolve each difference from a CME document;
   - add a `CmeCalendarSeed` instance and a minute-track migration that inserts
     through `cme_calendar_insert(seed)` / `cme_holidays_insert(seed)` and
     extends sessions (model: `_seed_cme_metals_calendar` in
     `market/schema/migrations/minute.py`).
2. **Mapping.** Add `"<ROOT>": <CALENDAR_ID>` to `FUTURES_PRODUCT_CALENDAR`
   (`src/manta_trading/data/tick/constants.py`).
3. **Universe entry.** Add a `TickUniverseEntry` to `TICK_UNIVERSE`: parent
   symbol, tier, range. Measure the spread share first with two free estimates
   (parent against the outright raw symbols); drop spreads only above a quarter
   of the rows.
4. **Tests.** Run the unit and integration tiers. A test that counted products
   or assumed one product changes on the test side; a failure that needs a src
   change is a leaked product assumption and goes to the PM.
5. **Apply the migration** (PM go): `mt data migrate status` shows only the new
   migration pending; then `mt data migrate apply`; then
   `mt data calendars sessions --calendar <ID> --from D --to D` around an
   exception date shows the sourced close. An applied migration is frozen: a
   correction is a new migration.
6. **Estimate (free).** `mt data tick pass --estimate-only`: only the new
   product's days are planned, and the printed bundle cost is the figure of
   record. Report it to the PM.
7. **Ceilings (PM).** The PM sets `MT_TICK_SPEND_CEILING_USD` and
   `MT_TICK_SPEND_30D_CEILING_USD` at or above the figure. No agent edits them.
8. **Pass, then ingest.** `mt data tick pass`, then `mt data tick ingest`. Every
   unit must end *ingested*; a `session_boundary:` failure is a calendar finding
   (the calendar is too narrow), never a reason to loosen the check. Afterwards
   the PM restores the per-pass ceiling.
9. **Session check, too-wide direction.** The boundary check cannot see an
   early close seeded too late. For each exception session in the bought range,
   `mt data tick get 1m --product <ROOT> --start D --end D+1` (session dates)
   must end on the bar just before the seeded close, with no bars on a closed
   day.
10. **Roll.** `mt data tick active --product <ROOT> --start D --end D`, then
    `uv run python scripts/record_229_roll_fixture.py --product <ROOT>` and add
    the product to `PRODUCTS` in `test/unit/data/tick/test_roll_provider_equivalence.py`.
    A disagreement at `VOLUME_RANK_LAG_SESSIONS` goes to the PM; there is no
    per-product lag.
11. **Surfaces.** `mt data tick status`, `mt data tick coverage --start D --end D`
    and `mt data overview` show the product with no code change.

### Exit codes (the read verbs)

| Code | `get` | `status` / `coverage` / `active` | `debug` |
|---|---|---|---|
| 0 | every session resolved and complete | ok | ok, including no rows |
| 1 | bad `WHAT`, rule, dates or options; unknown product; unknown or ambiguous contract; range over the cap or outside the calendar; pending migration; `MT_TICK_DB_URL` unset | the same preflight refusals | bad option value, unknown product, pending migration, URL unset |
| 3 | output printed, and a session is unresolved or incomplete | `coverage` mismatch; `active` unresolved session; `status` roll line failed | never |
| 4 | tick or calendar database unreachable, timed out, or dropped (after rows: `PARTIAL`) | the same | the same |

## Provisioning a tick database (slice 923)

Run this once, **when the PM has decided the tick database's placement**
(same cluster, a second cluster, or another host). The database name and host
are part of that decision, so none is written here: `<tick_db>` and
`<tick_host>` below are placeholders, and the artifact has no default name.

The artifact creates two roles (`tick_app` DML, `tick_migrate` DDL — never the
`trading_*` roles), creates `<tick_db>` owned by `tick_migrate`, revokes
`CONNECT` from `PUBLIC`, and makes the ledger read-only to `tick_app`. It never
touches `trading`.

```bash
cd ~/source/repos/manta/trading-data
# 1. Provision, as a superuser on the chosen cluster.
psql "$SUPERUSER_URL" -v tick_db=<tick_db> -f scripts/provision_tick_roles.sql
# 2. Set both passwords out of band (never in the repository):
#      ALTER ROLE tick_app WITH PASSWORD '...'; ALTER ROLE tick_migrate WITH PASSWORD '...';
# 3. Create the tick ledger, with the maintenance credential for this invocation only.
MT_TICK_MAINTENANCE_URL='postgresql://tick_migrate:...@<tick_host>:5432/<tick_db>' \
  uv run mt data init --database tick
# 4. Provision again: the ledger now exists, and this run makes it SELECT-only
#    for tick_app. Idempotent; it changes nothing else.
psql "$SUPERUSER_URL" -v tick_db=<tick_db> -f scripts/provision_tick_roles.sql
# 5. Verify with the application credential.
MT_TICK_DB_URL='postgresql://tick_app:...@<tick_host>:5432/<tick_db>' \
  uv run mt data migrate status --track tick
```

Step 4 is not optional. `tick_migrate`'s default privileges give `tick_app`
DML on every table it creates, and `init` creates the ledger after step 1, so
until step 4 runs `tick_app` could write the ledger (measured, slice 923).

Step 5 lists `001_schema_migrations` as applied and nothing pending. Neither
tick variable goes into `/etc/manta-trading.env` in this slice; the tick passes
that need `MT_TICK_DB_URL` arrive with slice 223 onward.

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

## Revision Log

- 2026-10-16 (slice 172): the eligible rollup build,
  `scripts/cutover_172_eligible_rollups.py`, and the `_next` rows it adds to
  `mt data caggs status`.
- 2026-10-16 (slice 171): the `off-tape unevaluated` and `off-tape share`
  health lines, and the one-time `mt data off-tape sweep`.
- 2026-10-14 (slice 234): the tick pass unit `mt-tick-pass.service` (manual,
  `mt-run tick`), its five environment keys and `deploy/add-tick-env.sh`, the
  `tick database` and `tick retention` health rows, `mt-run data tick …` as
  the production path, and staging sources under `/data`.
