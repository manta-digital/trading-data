---
docType: runbook
project: trading
parent: user/slices/128-slice.eodhd-catchup-and-production-cutover.md
relatedSlices: [128]
host: <db_host>>
dateCreated: 20260427
dateUpdated: 20260708
status: ready
---

# Production Deployment Runbook — <db_host> (slice 128)

This runbook deploys the post-127, slice-128-extended manta-trading
stack to the production host <db_host>. It supersedes deferred slice 126's
plan; the systemd unit-file and journald-drop-in shapes are reused.

**Two HARD GATES gate the entire runbook. Nothing past Phase 1 runs
until both checkboxes are signed off.**

---

## Phase 0 — HARD GATE A: PM-confirmed minute-data backup

The production `minute_ohlcv` hypertable holds irreplaceable historical
data. Every migration this slice introduces is additive (new tables
only — no `ALTER TABLE` on existing data), but production DDL of any
kind is gated on a current backup per the architecture's external
operational constraint and slice 125's backup gate.

- [ ] Operator pings PM in the agreed channel: "Requesting confirmation
      that production minute-data backup is current as of <timestamp>."
- [ ] PM responds with explicit confirmation: "Backup confirmed current
      as of <timestamp>." Record the timestamp here:
      `_____________________`
- [ ] Operator confirms backup confirmation is recorded above before
      proceeding. **Do not skip this step.**

---

## Phase 1 — HARD GATE B: ≥24h test dry-run results

The slice-128 test-environment dry-run (Phase 10.3 of the task list)
must have produced evidence in
`project-documents/user/research/slice-128-dry-run.md` showing:

- [ ] Coverage scan ran cleanly (or `coverage_gaps` populated only
      with known/triaged entries — at minimum the inaugural NVDA row
      visible).
- [ ] Stage A (`mt data adjustment verify`) PASSed on AAPL plus ≥4
      additional symbols.
- [ ] Stage B (`mt data adjustment verify-against-eodhd-eod`) PASSed
      on AAPL plus ≥4 additional symbols.
- [ ] Backfill quota guard demonstrably engaged at ≥1 fraction-of-cap
      threshold during a small backfill (`quota_sleep` or
      `quota_window_advance` event visible in
      `~/.local/share/manta-trading/events/acquisition.jsonl`).
- [ ] Operator records the dry-run report's path / Git revision here:
      `_____________________`

**Both gates above MUST be checked before continuing.**

---

## Phase 2 — Set service-user variable

```bash
export MANTA_TRADING_SERVICE_USER=manta-trading
```

Used by Phase 3 and Phase 7 substitutions. Re-export in any new shell.

---

## Phase 3 — Prepare host (one-time)

Skip if the host has already been prepared in a previous deployment.

```bash
# As root:
sudo useradd --system --shell /usr/sbin/nologin --create-home \
  --home-dir /opt/manta-trading "$MANTA_TRADING_SERVICE_USER"

sudo install -d -o "$MANTA_TRADING_SERVICE_USER" -g "$MANTA_TRADING_SERVICE_USER" \
  -m 0755 /opt/manta-trading

sudo install -d -o "$MANTA_TRADING_SERVICE_USER" -g "$MANTA_TRADING_SERVICE_USER" \
  -m 0755 /var/lib/manta-trading
```

---

## Phase 4 — Install code

```bash
sudo -u "$MANTA_TRADING_SERVICE_USER" git clone \
  https://github.com/<org>/trading /opt/manta-trading

cd /opt/manta-trading
sudo -u "$MANTA_TRADING_SERVICE_USER" git checkout main
sudo -u "$MANTA_TRADING_SERVICE_USER" /usr/local/bin/uv sync
```

---

## Phase 5 — Install `/etc/manta-trading.env`

This file holds production credentials and must be created out-of-band
(do NOT commit it to the repo). Required keys:

```
MT_EODHD_API_KEY=<production-key>
MT_MARKET_DB_URL=postgresql://user:pass@host/market
MT_TIMESCALE_DB_URL=postgresql://user:pass@host/timescale
MT_MINUTE_PROVIDER=eodhd
MT_DAILY_PROVIDER=eodhd
MT_CORPORATE_ACTIONS_PROVIDER=eodhd
MT_LOG_LEVEL=INFO
MT_LOG_FORMAT=json
```

`MT_ALPHAVANTAGE_API_KEY` is not required — slice 128 closing
change consolidated all production data sources on EODHD (minute,
daily, splits, dividends). The AV daily provider remains on disk
and is selectable via `MT_DAILY_PROVIDER=alphavantage`, but is not
the production path.

Install with restrictive permissions:

```bash
sudo install -o root -g "$MANTA_TRADING_SERVICE_USER" -m 0640 \
  ./manta-trading.env /etc/manta-trading.env
sudo shred -u ./manta-trading.env  # erase the staging copy
```

- [ ] `/etc/manta-trading.env` exists, mode 0640, root:manta-trading.
- [ ] Spot-check: `sudo -u "$MANTA_TRADING_SERVICE_USER" cat /etc/manta-trading.env | head -3` returns the file (not permission-denied).

---

## Phase 6 — Apply schema migrations against production DBs

```bash
sudo -u "$MANTA_TRADING_SERVICE_USER" --preserve-env=PATH bash -c '
  set -a; source /etc/manta-trading.env; set +a
  cd /opt/manta-trading
  /usr/local/bin/uv run mt data migrate apply --db all
'
```

Expected new entries in the minute track: `012_coverage_gaps`,
`013_backfill_state`, `014_nvda_inaugural_gap`. Expected new entries in
the daily track: none (slice 128 introduces no daily migrations).

Spot-check the inaugural NVDA row landed:

```bash
sudo -u "$MANTA_TRADING_SERVICE_USER" --preserve-env=PATH bash -c '
  set -a; source /etc/manta-trading.env; set +a
  psql "$MT_TIMESCALE_DB_URL" -c \
    "SELECT symbol, gap_start, gap_end, resolution_status \
       FROM coverage_gaps WHERE symbol = '\''NVDA'\'';"
'
```

- [ ] One row returned with the 2024-06-07 → 2024-07-25 range and
      `resolution_status = provider_confirmed_unfillable`.

---

## Phase 7 — Render and install systemd units + journald drop-in

```bash
cd /opt/manta-trading/deploy/systemd

sudo bash -c "
  envsubst '\$MANTA_TRADING_SERVICE_USER' < mt-daily-daemon.service.tmpl  > /etc/systemd/system/mt-daily-daemon.service
  envsubst '\$MANTA_TRADING_SERVICE_USER' < mt-minute-daemon.service.tmpl > /etc/systemd/system/mt-minute-daemon.service
"

sudo install -d /etc/systemd/journald.conf.d
sudo install -o root -g root -m 0644 \
  journald-manta-trading.conf /etc/systemd/journald.conf.d/manta-trading.conf

sudo systemctl daemon-reload
sudo systemctl restart systemd-journald

# Sanity check unit files parsed cleanly:
sudo systemd-analyze verify mt-daily-daemon.service mt-minute-daemon.service
```

- [ ] Both `.service` files in `/etc/systemd/system/` render with
      `User=` matching the service-user variable.
- [ ] `systemd-analyze verify` reports no errors.

---

## Phase 8 — Start daily service

```bash
sudo systemctl enable --now mt-daily-daemon

# Watch for ≥5 minutes:
sudo journalctl -u mt-daily-daemon -f
```

Observe:

- [ ] Per-symbol cycle log lines include `fetched daily OHLCV`,
      `ingested splits`, `ingested dividends`, `checkpoint advanced`.
- [ ] No persistent ERROR-level log lines.
- [ ] CTRL-C the journalctl follow when satisfied.

---

## Phase 9 — Gated minute service start

```bash
sudo systemctl enable --now mt-minute-daemon

sudo journalctl -u mt-minute-daemon -f
```

Observe for ≥5 minutes:

- [ ] Per-chunk log lines include `chunk_ok` events and watermark
      advances.
- [ ] No persistent ERROR-level log lines.
- [ ] AV minute provider absent from logs (`grep -i alphavantage`
      returns nothing in the minute daemon journal).

---

## Phase 10 — Failure recovery sanity check

```bash
# Kill one of the daemon processes; systemd should restart it.
sudo systemctl kill --signal=SIGKILL mt-minute-daemon
sleep 15
sudo systemctl status mt-minute-daemon
```

- [ ] Status shows `active (running)` after the restart window.
- [ ] Journal shows the restart and a clean resume from the last
      watermark (no double-fetch, no missed chunk).

---

## Phase 11 — Log volume check after 24h

After the deployment has run for ≥24 hours:

```bash
sudo journalctl --disk-usage
sudo journalctl -u mt-daily-daemon --since '1 hour ago' | wc -l
sudo journalctl -u mt-minute-daemon --since '1 hour ago' | wc -l
```

- [ ] Disk usage well below the 2 GiB cap.
- [ ] Per-hour line counts are reasonable (no log-storm signature).

---

## Phase 12 — Backfill (optional, gated, mutually exclusive with daemon)

Per slice 128 Decision 18: the minute daemon and the backfill
orchestrator MUST NOT run concurrently. Concurrent runs would
over-spend the EODHD daily quota and cause redundant fetches. This is
operational policy enforced here, not a lock table.

```bash
# 1. Stop the minute daemon (the daily daemon may keep running):
sudo systemctl stop mt-minute-daemon

# 2. Run backfill under tmux/screen — full universe runs ~21 days:
sudo -u "$MANTA_TRADING_SERVICE_USER" --preserve-env=PATH bash -c '
  set -a; source /etc/manta-trading.env; set +a
  cd /opt/manta-trading
  /usr/local/bin/uv run mt data minute backfill \
    --universe active --since 2004-01-01 --quota-fraction 0.8
' &

# Monitor progress from a separate session:
uv run mt data minute status --json | jq .backfill

# 3. After the backfill orchestrator exits cleanly, re-enable the daemon:
sudo systemctl start mt-minute-daemon
```

- [ ] Minute daemon stopped before backfill invoked.
- [ ] Backfill exit status recorded: `_____` (0 = clean, 1 = failures
      or aborted).
- [ ] Minute daemon re-enabled only after backfill exits.

---

## Rollback

If the deployment surfaces a blocker before Phase 11 (24h soak), the
fastest safe revert is:

```bash
sudo systemctl stop mt-minute-daemon mt-daily-daemon
sudo systemctl disable mt-minute-daemon mt-daily-daemon
```

The schema migrations are additive (new tables only); no rollback of
DDL is required to restore prior behavior. The slice-127 path remains
operable.

If a code-level bug is found, branch from the deployed tag and ship a
small follow-up; do not bundle code changes into this runbook.
