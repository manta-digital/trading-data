---
docType: runbook
project: trading-data
scope: project-wide
host: <prod_host>
dateCreated: 20260427
dateUpdated: 20260803
status: current
supersedes: the slice-128 /opt + systemd runbook (see git history of this file)
---

# Production Runbook — `<prod_host>`

**This describes how production actually works today.** It replaces the slice-128
runbook, which specified a `/opt/manta-trading` checkout owned by a `nologin`
service account and driven by systemd units. That layout was never built. It
remains a reasonable target and is summarised under "Future target" below, but
until it exists, treating it as current is what has repeatedly caused confusion
about how to deploy. The full original is in `git log -p` on this file.

---

## Current reality

| | |
|---|---|
| Checkout | `~/source/repos/manta/trading-data`, owned by the login user |
| Tracks | `main` |
| Install | `uv sync` into the checkout's own `.venv` — **not** a PyPI install |
| Process manager | none |
| systemd units | none installed (`deploy/systemd/` holds unused templates) |
| Migrations | applied manually, only when a slice adds one |

**`mt update` is not the update path.** It runs `uv tool install --upgrade` /
`pip install --upgrade` against PyPI, which is how an *interactive user* installs
the CLI. Production runs from a git checkout, so `mt update` would either do
nothing or install a second, unrelated copy. This is deliberate — slice 908 D7
kept production on the checkout rather than forcing a `nologin` account through
an install mechanism designed for user tools.

---

## Update procedure

```bash
cd ~/source/repos/manta/trading-data
git fetch origin
git checkout main          # only needed if sitting on a detached tag
git pull
uv sync
```

`uv sync` runs even when no dependency changed — skipping it is how a future
dependency addition turns into a confusing runtime error rather than an obvious
install step.

**Verify the update took:**

```bash
uv run mt --version
```

This reads installed distribution metadata, not the source tree, so it confirms
both the checkout *and* the sync. If it reports the old version, `uv sync` did
not run or did not succeed.

**Apply migrations only if the release notes say a slice added one:**

```bash
uv run mt data migrate apply --db all
```

---

## Running the acquisition passes

```bash
# daily
uv run mt data daemon run --daily --stop-when-done

# minute
uv run mt data daemon run --minute --stop-when-done
```

`--stop-when-done` makes each of these **one pass, then exit** — they are not
long-running processes despite the `daemon` subcommand name. Nothing keeps them
running across a reboot, which is why acquisition stops until someone re-invokes
them.

### Timing constraints that look like failures but are not

- **The daily gate opens at 00:00 UTC + `DAILY_CYCLE_START_OFFSET` (30 min).**
  Run `--daily --stop-when-done` between 00:00 and 00:30 UTC and it will **wait**
  for the gate rather than exiting — up to 30 minutes of apparent hang, with one
  INFO line announcing the wait. This is deliberate (slice 912, issue #6): it
  previously exited immediately claiming the scope was drained, having fetched
  nothing. Interrupting during the wait takes up to 60s to take effect.
- **After a daily pass completes, the next is held for
  `DAILY_CYCLE_RETRY_INTERVAL` (default 30 min).** Tunable per-invocation with
  `--daily-retry-minutes`, or by environment with `MT_DAILY_CYCLE_RETRY_MINUTES`.
- **Re-running the daily pass after it has completed for the day is cheap and
  safe.** It derives the work list from `acquisition_state`, finds nothing
  pending, logs "no actionable work", and exits without any provider call.
- **An interrupted daily pass resumes at the symbols it never reached**, within
  the same UTC day, without re-fetching what it already did (slice 912, issue
  #7). Re-invoking after an interruption is the correct recovery action.

---

## Checking state

There is no `status` subcommand. Check the database directly, and always set a
statement timeout — production holds a multi-billion-row hypertable, and an
unbounded probe against it is what caused the 2026-07-20 incident.

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

Never run an expression aggregate over a compressed hypertable — it decompresses
everything. If a query outlives its client-side timeout, cancel the backend
rather than assuming it stopped.

---

## Not yet documented

Recorded honestly rather than guessed, because inventing these is what made the
previous runbook misleading:

- **Where the environment file lives** and which variables production sets
  (`MT_TIMESCALE_DB_URL`, `MT_EODHD_API_KEY` at minimum). `Settings` reads `.env`
  from the working directory plus `MT_*` variables from the environment.
- **What, if anything, re-invokes the acquisition passes** — cron, a shell loop,
  or manual invocation.
- **Restart-after-reboot procedure**, which follows from the answer above.

---

## Future target — `/opt` + systemd

The superseded runbook specified a `/opt/manta-trading` checkout owned by a
`nologin` `manta-trading` account, `/etc/manta-trading.env` at mode 0640, and
`mt-daily-daemon` / `mt-minute-daemon` systemd units rendered from
`deploy/systemd/` with a journald drop-in. That design is sound and its unit
templates are still in the repo. Adopting it would give restart-on-reboot,
supervised restarts, and log rotation — the three things the current layout
lacks.

It is not scheduled. When it is, it should be its own slice with its own
verification, not folded into an unrelated deployment. Until then this document
describes what is true, and the systemd design is a target rather than a
procedure.
