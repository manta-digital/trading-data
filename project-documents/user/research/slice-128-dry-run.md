---
docType: dry-run-evidence
sliceParent: 128
project: trading
dateCreated: 20260428
dateUpdated: 20260429
status: in_progress
---

# Slice 128 — ≥24h Test-Environment Dry-Run Evidence

This file collects the evidence required to satisfy **Hard Gate B** in the
production deploy runbook ([production-deploy.md](../runbooks/production-deploy.md))
and task **10.3** in the slice-128 task list.

PM reviews this file alongside the runbook Phase 1 checklist before
authorizing production cutover (task 10.4).

## Findings during run

### 1. Daemon over-fetched dormant symbols (Fix A applied 2026-04-29)

The daily daemon ran 21,123 fetch cycles in 24 hours (median gap
between symbol fetches: 0.67s, no long sleep ever taken). Combined
with the 3-call CA-ingest cycle (OHLCV + splits + dividends per
symbol), this consumed ~63k of the 100k daily EODHD quota and
trended toward exhaustion.

Root cause: the work-queue freshness check used `last_success_ts`
(the date of the latest stored bar) instead of `last_attempt_ts`
(when we last tried to fetch). For ~1,132 dormant tickers (delisted,
halted, NYSE test tickers like `NTEST-A/B/C/N`, very-low-volume
issues whose last bar is from 2017–2024) this meant every cycle
re-classified them as stale and re-fetched them, even though the
fetch immediately confirmed there was nothing new. The fetch cost
3 EODHD calls each, repeated thousands of times.

Fix A: split `_is_fresh` into two helpers — `_is_fresh` (kept for
status reporting: "how current is our data?") and
`_is_attempt_fresh` (new, used by daemon work-queue and orchestrator
skip-if-recent: "do we need to try again today?"). All three
work-queue callers (daily, minute, both orchestrators' `skip_recent`
paths) now use `_is_attempt_fresh(last_attempt_ts)`. Status display
unchanged.

Validated: with the fix, dormant symbols are skipped on the second
cycle once they've been attempted in the current MIN_DAYS window.

### 2. Status command's "stale" bucket misleads on dormant symbols

`mt data daily status` shows "Symbols: 8011 total │ 6873 fresh │
1132 stale" — but the 1132 "stale" are correctly-handled dormant
tickers, not a data-quality problem. UX follow-up (not slice 128
blocking): split the status display into three buckets — fresh
(success_ts within MIN_DAYS), dormant (attempt_ts within MIN_DAYS
but success_ts older — we tried, no new data exists), stale
(neither).

### 3. AV-tagged minute rows from slice 127 (cosmetic)

`acquisition_state` had 649 minute rows tagged `provider='alphavantage'`
even though slice 127 fetches went to EODHD. Cause: the minute
orchestrator's `provider_id` parameter defaulted to AV, and the CLI
didn't pass an override. Fixed in commit 0341d0e by passing
`provider_id=settings.minute_provider.value` from the CLI. Existing
AV-tagged rows remain (harmless artifact; clean up later).

### 4. Permanent-404 symbols pinging EODHD every cycle (Fix A side-effect)

After Fix A, the daily daemon correctly sleeps once all symbols have a
recent attempt. But ~7 symbols in the AV-derived universe return
permanent HTTP 404/422 from EODHD and are re-attempted every poll cycle:

- `NXT(EXP20091224)` — expired warrant, weird ticker syntax (422)
- `TEST_ERROR` — literal test data in the symbol table (404)
- `OCTO`, `ATGE` — delisted, EODHD does not carry (404)
- `BC/PA`, `BC/PB`, `BC/PC` — preferred-share class syntax EODHD rejects (404)

At 1h poll interval that's ~168 wasted calls/day — harmless against the
100k/day cap, but a real symbol-hygiene gap. Slice 129 (named universes
+ EODHD universe source) is the right place to fix this: when migrating
to the EODHD-sourced universe, these symbols simply won't be there. For
the current AV-derived universe, a separate cleanup pass could prune
them, but it's not slice 128 work.

## Scope note

Slice 128's dry-run validates the **AV-derived universe** (8,010 active
US-listed symbols, populated via the now-cancelled AV `LISTING_STATUS`
endpoint) being served by EODHD. EODHD coverage of this universe is
99.91% (8,003 of 8,011 daily-OK; 7 genuine bad-symbol-list entries).

Universe-completeness is **out of scope** for slice 128 — EODHD's own
US-listed universe is ~13,118 symbols (≈64% larger), and EODHD also
exposes a delisted-history endpoint with 54,119 tickers (29k delisted
common stocks) for survivorship-bias-free backtesting. Slice 129 is
scoped to handle the universe-source swap, symbol normalisation, and
delisted-instrument coverage.

## Environment

- **Operator host:** _(developer machine — fill in: hostname, OS)_
- **Test DBs:** `MT_TIMESCALE_DB_URL`, `MT_MARKET_DB_URL` _(fill in: which DBs / whether dedicated test instances or shared)_
- **Branch / commit at start of dry-run:** _(fill in: `git rev-parse HEAD`)_
- **Provider config:**
  - `MT_MINUTE_PROVIDER=eodhd`
  - `MT_DAILY_PROVIDER=eodhd`
  - `MT_CORPORATE_ACTIONS_PROVIDER=eodhd`
- **Start timestamp (UTC):** _(fill in)_
- **End timestamp (UTC):** _(fill in — must be ≥24h after start)_

## Phase 1 — Daemons running ≥24h continuously

Both daemons launched against test DBs and observed throughout the window.

- [ ] `mt data daily daemon` running continuously for ≥24h
- [ ] `mt data minute daemon` running continuously for ≥24h
- [ ] No unhandled crashes / OOMs / runaway-restart loops in either daemon's
      log stream

### Observation notes

_(Fill in: pid lifetime, restart counts, anything notable from the journals.
Paste in the `journalctl -u` or equivalent log excerpts that demonstrate
continuous run.)_

```
# example:
# mt-daily-daemon.service ran from 2026-04-29 14:00 UTC to 2026-04-30 14:30 UTC
# cycles_completed=NN, errors=0
# mt-minute-daemon.service ran from 2026-04-29 14:00 UTC to 2026-04-30 14:30 UTC
# cycles_completed=NN, errors=0
```

## Phase 2 — Coverage scan

Per task 10.3: `mt data minute coverage --all --from <range> --to <range> --persist`.

- [ ] Command run; output captured
- [ ] `coverage_gaps` table populated only with known/triaged entries (NVDA
      inaugural visible at minimum)
- [ ] Exit code recorded

### Command invocation

```
# Fill in actual command + flags used:
# mt data minute coverage --all --from YYYY-MM-DD --to YYYY-MM-DD --persist --json > coverage.json
```

### Summary table

_(Fill in: total symbols scanned, symbols with gaps, total gap-days,
list of symbols with non-NVDA gaps if any. Paste tabular output or JSON
excerpt.)_

### Triage notes

_(For each gap that is NOT the NVDA inaugural row: explain why it is
acceptable for this dry-run window — known holiday, partial day, provider
limitation, etc. — or call out as a regression to investigate before
cutover.)_

## Phase 3 — Stage A and Stage B verification

Per task 10.3: AAPL plus ≥4 additional symbols (NVDA, MSFT, GOOGL, TSLA,
AMZN suggested).

### Stage A (`mt data adjustment verify`)

| Symbol | Range | Pass days | Fail days | Max diff | Status |
|---|---|---|---|---|---|
| AAPL | _from_ → _to_ | _N_ | _N_ | _e.g. 1.2e-7_ | _PASS / FAIL_ |
| NVDA | _from_ → _to_ | _N_ | _N_ |  |  |
| MSFT | _from_ → _to_ | _N_ | _N_ |  |  |
| GOOGL | _from_ → _to_ | _N_ | _N_ |  |  |
| TSLA | _from_ → _to_ | _N_ | _N_ |  |  |

_(Stage A FAILs on real gap days are expected; note them here.)_

### Stage B (`mt data adjustment verify-against-eodhd-eod`)

| Symbol | Range | Pass days | Fail days | Max |stored − published| | Status |
|---|---|---|---|---|---|
| AAPL | _from_ → _to_ | _N_ | _N_ | _e.g. 1.2e-7_ | _PASS / FAIL_ |
| NVDA | _from_ → _to_ | _N_ | _N_ |  |  |
| MSFT | _from_ → _to_ | _N_ | _N_ |  |  |
| GOOGL | _from_ → _to_ | _N_ | _N_ |  |  |
| TSLA | _from_ → _to_ | _N_ | _N_ |  |  |

_(Paste `--json` output excerpts or attach files in this directory.)_

## Phase 4 — Backfill with quota-guard engagement

Per task 10.3: `mt data minute backfill --quota-fraction 0.05` for long
enough to demonstrate quota-guard sleep emission at ≥1 fraction-of-cap
threshold.

- [ ] Daily daemon LEFT RUNNING per Decision 18; minute daemon STOPPED
      before backfill (per runbook Phase 12)
- [ ] Backfill command run; quota_sleep event observed at ≥1
      fraction-of-cap threshold
- [ ] Quota_window_advance event observed when window advanced
- [ ] On completion: minute daemon restarted

### Command invocation

```
# Fill in:
# sudo systemctl stop mt-minute-daemon          # mutual exclusion
# mt data minute backfill --universe NAME --since YYYY-MM-DD --quota-fraction 0.05
# sudo systemctl start mt-minute-daemon
```

### Event-stream evidence

_(Paste the matching JSONL events from the daemon log: `quota_sleep`,
`quota_window_advance`, plus surrounding `backfill_symbol` events.)_

```
# example:
# {"action":"backfill_symbol","symbol":"AAPL",...,"timestamp":"..."}
# {"action":"quota_sleep","calls_used":NN,"cap":NN,"sleep_seconds":NN,"timestamp":"..."}
# {"action":"quota_window_advance","new_window_start":"...","timestamp":"..."}
```

## Phase 5 — Sign-off

- [ ] Operator confirms all four phases above show green / triaged signals
- [ ] PM has reviewed this evidence file
- [ ] PM authorizes production cutover (runbook Hard Gate B)

| Role | Name | Decision | Date |
|---|---|---|---|
| Operator |  |  |  |
| PM |  |  |  |

## Artifacts

_(List paths to any captured output files, screenshots, log excerpts,
JSON dumps stored alongside this document.)_

- `_(path)_` — coverage scan JSON
- `_(path)_` — Stage A output
- `_(path)_` — Stage B output
- `_(path)_` — backfill event stream excerpt
