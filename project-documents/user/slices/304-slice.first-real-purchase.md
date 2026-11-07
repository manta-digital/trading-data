---
docType: slice-design
slice: first-real-purchase
project: trading
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies:
- 302
- 303
interfaces:
- 305
- 306
- 307
dateCreated: 20261107
dateUpdated: 20261107
status: not_started
turns: 54
promptTokens: 6570763
cachedTokens: 6369809
cacheWriteTokens: 200846
completionTokens: 63197
durationSeconds: 1148.3
costUsd: 4.145438
usageRounds: 3
pricedRounds: 3
usageByModel:
  claude-opus-5-5:
    rounds: 3
    costUsd: 4.145437600000001
    completionTokens: 63197
revision_number: 2
---

# Slice Design: First Real Purchase

## Overview

This slice runs 220's purchase path with real money, in production, for the first time. The cap is $25 (PM, 2026-10-28). Everything bought is kept:

- `statistics` and `definition` for ES and GC over the whole CME dataset, 2010-06-06 to 2026-11-01;
- ES `ohlcv-1m` from 2017-01-01 to 2026-11-01, which is as many recent years as the rest of the cap buys.

The purchase is a universe edit (history wants on the ES and GC entries) released to production. It runs through `sudo mt-run tick`, the production form slice 234 built: estimate, spend guard, manifest, batch download, verify, ingest. The window slice 303 bought on throwaway databases for its evaluation (2024-08-30 to 2024-12-31, $0.065) is not in production's manifest, so the pass buys it again (D2).

The slice's evidence is (1) the reconciliation of each request's estimate, its manifest row and the charge Databento reports for its job, and (2) the completeness verdicts `status` and `coverage` give on what was bought, with every finding named.

Costs measured on 2026-11-07 with free `metadata.get_cost` calls (`GLBX.MDP3`, parent symbols, end 2026-11-01 exclusive):

| Want | Range | List price |
|---|---|---|
| ES `definition` | 2010-06-06 to 2026-11-01 | $0.158 |
| GC `definition` | 2010-06-06 to 2026-11-01 | $2.574 |
| ES `statistics` | 2010-06-06 to 2026-11-01 | $0.452 |
| GC `statistics` | 2010-06-06 to 2026-11-01 | $1.010 |
| ES `ohlcv-1m` | 2017-01-01 to 2026-11-01 | $20.04 (yearly: 1.91, 2.08, 2.00, 2.14, 1.94, 2.11, 2.03, 2.07, 2.08, 1.68 for Jan to Oct 2026) |
| **Total** | | **$24.23** |

The planned figure is a little lower. Days production already holds are not bought again: the definitions bought with the held `tbbo` days. Starting ES bars in 2016 would add $1.97 and pass the cap.

## Value

- **The purchase path is proven at real size before the large pull.** Slice 307 buys about $70 more through the same path. It should not be the first run at about 900 batch requests and 19,000 units, or the first run on pre-2020 data.
- **Open interest and settlement over the whole history.** The open-interest roll rule (`n`, slice 303) can answer back to 2010 for both products.
- **Contract identity back to 2010.** Bars and statistics resolve their instrument through definition validity windows, so every later history pull depends on these definitions.
- **Ten years of ES minute bars with a named source.** Slice 306 serves them as the default source for ES time bars, and the volume roll rule gains a bar basis for 2017 to 2023.
- **The estimate is checked against the charge.** An estimate that drifts from what Databento bills would make every spend ceiling meaningless. This is the reconciliation the architecture asks for, at a size where a small per-request error would show.

## Technical Scope

**Included**

- `TICK_UNIVERSE` gains history wants for ES and GC (Technical Decision D1, below). No other source change to the pipeline.
- `deploy/set-tick-spend-ceilings.sh`: the PM's one host command. It sets the two spend ceilings in the production env file (D4).
- `scripts/reconcile_tick_spend.py`: estimate, manifest and provider charge side by side, for every request since a given instant (D5). It is reusable by 307.
- `scripts/cutover_304_first_purchase.py`: release install, estimate, purchase and ingest, evidence (D3).
- The evidence note `user/notes/304-first-purchase-evidence.md`, and a runbook update: the purchase-slice procedure and the ceiling script in `100-production-operations.md`.

**Excluded**

- GC `ohlcv-1m`, and ES `ohlcv-1m` before 2017 (307).
- Any `ohlcv-1s`, any tick tier change, any change to the tick ranges.
- Health, accounting and overview verdicts for the new tiers, and the manifest-scale measurement (305). This slice records its own run's durations and counts, which 305 can use, but adds no benchmark.
- Serving, and the tick-against-provider bar parity (306).
- Changes to ingest rules found wanting by real data. A failed unit is a finding (D6). A rule change needs its own design unless it is a defect in this slice's path (Risk Assessment).
- The month-end Databento invoice comparison (D5; deferred to issue #69).
- The close-minute bar check 301 handed over (deferred to issue #70).

## Dependencies

### Prerequisites

- **300** (archive generalisation): schema families, the plan-inclusion marker, and the guard that counts marginal spend only.
- **301** (calendar history): CME sessions back to 2010-06-06, so pre-2020 records place in sessions.
- **302** (`ohlcv-1m` and definitions): the `ohlcv-1m` family, `tick_ohlcv_1m`, the bar ledger, history wants and their validation, load-complete status and `coverage --schema ohlcv-1m`.
- **303** (statistics): the `statistics` family, `tick_statistics`, session assignment from `ts_ref`, content findings, and the cross-tier volume check.
- Production at release 0.26.0 or later, with the tick track at `tick_010_statistics` (applied by 303's cutover).
- The Databento account with no subscription, so every request is usage-billed. `MT_TICK_DATABENTO_PLAN` is unset in `/etc/manta-trading.env`.

### Interfaces Required

- `mt data tick pass --estimate-only --json`: the planned figure, trailing 30-day spend, verdict.
- `mt data tick run` (through `sudo mt-run tick`): one recorded pass. Exit 3 means some units failed; exit 6 means jobs were still processing at the wait budget, and running it again resumes.
- `mt data tick status --json`, `coverage --schema {ohlcv-1m,statistics} --json`, `debug requests|units|definitions`.
- `tick_request.estimated_cost_usd` (set at plan time from `get_cost`) and `actual_cost_usd` (set from the batch job's reported cost when it is `done`).
- `scripts/cutover_common.py` and `upgrade_production.py`: preflight, timer hold and release, install, logging.
- `scripts/verify_cme_sessions.py`: unchanged. Its first pre-2020 run is here, as slice 301 recorded.

## Architecture

### Component Structure

```
universe.py (TICK_UNIVERSE history wants)            ── code, released
deploy/set-tick-spend-ceilings.sh                    ── PM, root, once
scripts/cutover_304_first_purchase.py                ── agent, after release
   ├─ cutover_common: preflight, hold timers, install, release timers
   ├─ sudo mt-run data tick pass --estimate-only --json
   ├─ sudo mt-run tick              (repeat while exit 6)
   └─ evidence
        ├─ scripts/reconcile_tick_spend.py --since <cutover start> --cap-usd <per-pass ceiling>
        ├─ mt-run data tick status / coverage --json
        ├─ mt-run data tick debug requests|units|definitions --json
        └─ scripts/verify_cme_sessions.py on 2017–2019 bar jobs
user/notes/304-first-purchase-evidence.md            ── narration + findings
```

The cutover and reconciliation scripts read production state; only the `mt` verbs write it. The reconciliation script runs from the dev checkout, whose `.env` reaches the same tick database and Databento account (runbook 100: "both sides buy against one account and one manifest"). It must never write: it opens its database session read-only (`SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`) and calls only free provider methods, and its docstring says so for 307's reuse.

### Data Flow

1. **Plan.** The pass reads the new wants. For each want it takes the product's session days in range, drops covered days, and keeps those whose dataset condition is `available` or `degraded`. It groups them into month runs per schema and product, and puts definitions first within a day. Expect about 900 requests (four full-range wants of about 197 months each, plus 118 months of ES bars) and about 19,000 units.
2. **Guard.** Trailing 30-day spend (manifest rows plus provider jobs the manifest lacks) plus the planned figure is checked against both ceilings. No request is plan-included, because no plan is configured.
3. **Buy and download.** Batch jobs are submitted and awaited. Each job's files are downloaded to the archive and verified against size, hash and record counts. `actual_cost_usd` is written when the job is done.
4. **Project and ingest.** Definitions are projected by the acquisition pass. Statistics and bars are ingested by the ingest pass, one unit per transaction, into `tick_statistics` and `tick_ohlcv_1m` with their ledgers.
5. **Judge.** `status` and `coverage` read the manifest, the ledgers and the raw tables. The reconciliation reads `tick_request` and Databento's job list.

### State Management

- **Production manifest** (`tick_request`, `tick_archive_unit`) gains about 900 requests and about 19,000 units. These rows are the record of the purchase.
- **Archive** `/data/tick-archive` gains the files: a few GB, against 996 GB free on `/data`. The pass's space verdict checks this before buying. They are backed up by the existing tick-archive backup (227); no backup change.
- **Tick database**: `tick_statistics` about 19.4 M rows, `tick_ohlcv_1m` about 5.4 M rows, and `tick_definition` grows by distinct instruments. The row counts go in the evidence note, read from `debug` and `status`.
- **Production env file**: the two ceilings, set by the PM (D4). Nothing else changes.

## Technical Decisions

### D1 — What is bought, as one universe edit
Status: decided

Both entries gain `history`. Every end is the fixed date 2026-11-01 (exclusive), not open:

| Entry | `definition` | `statistics` | `ohlcv-1m` |
|---|---|---|---|
| ES | 2010-06-06 – 2026-11-01 | 2010-06-06 – 2026-11-01 | 2017-01-01 – 2026-11-01 |
| GC | 2010-06-06 – 2026-11-01 | 2010-06-06 – 2026-11-01 | none |

- The PM's split: statistics and definitions for both products over the full range, and ES bars with the rest of the cap. At the measured prices that buys bars from 2017. The start is a calendar year so 307's remainder is whole years.
- **Fixed end.** An open end would make every later `mt-run tick` buy the newest days, which is spend nobody chose. With a fixed end the purchase is bounded, the reconciliation is exact, and a re-run plans nothing. 307 decides whether its ends are open.
- Universe validation already holds for these values: each want starts on or after `CME_DATASET_START`, `statistics` and `ohlcv-1m` lie inside `definition`, and both entries have a tier (`tbbo`).
- The end leaves the last statistics session or two `edge_unknown`. Their publication falls in a UTC day after 2026-11-01, as at the end of 303's window. That is expected, and 307 closes it.
- **If the cutover's estimate exceeds $25.00**, the cutover stops before buying (D3, step 3), and the bar start moves to 2018-01-01 in a fix release. This is decided now. The $24.23 measured above makes it unlikely.
- *Rejected: split the cap between ES and GC bars.* GC bars cost about $3.50 a year against ES's $2.00, so a split buys fewer years of each. The PM named ES.

### D2 — Re-buy 303's window, do not adopt it
Status: decided

Slice 303 bought ES and GC `statistics` for 2024-08-30 to 2024-12-31, with the definitions that window lacked. That was 16 jobs for $0.0647, on throwaway databases production's manifest does not know. 303's design proposed that 304 adopt them. This slice lets the pass buy the window again instead.

- Adopting would need a staging copy readable by the service user, a cutover step, and a rehearsal comparison. It would also be the first use of `adopt` on statistics and definition jobs. All of that saves six cents and is not part of the purchase test.
- The 303 scratch archive and throwaway databases are no longer needed once this design is accepted. The throwaway databases are dropped if still present (created by this process on the test cluster), and `~/scratch-303/archive` may be removed.

### D3 — The purchase runs in production, through `sudo mt-run tick`, after release
Status: decided

The universe is code, so the purchase needs a release. It runs as the production pass (systemd unit, service user, production env, `pass_runs` row), not from the dev checkout.

- Slice 234 made `sudo mt-run tick` the production path, and "the dev checkout and its `.env` stop being the production path". 307's large pull will run there, so its unit, its permissions on the archive and its wait budget are what must be proven.
- So the purchase happens in Phase 7, after merge and release, through the cutover script. The slice is complete when the evidence note holds the reconciliation and the verdicts. Phase 6 delivers the code and the scripts.
- Cutover steps, check-then-act, re-runnable after a failure:
  1. **preflight**: the ref exists here and on origin. `/etc/manta-trading.env` has no `MT_TICK_DATABENTO_PLAN` and has both ceilings set; the per-pass value is kept for step 5. 
  2. **install**: hold timers, install the ref, release timers as found (`cutover_common`, as 303's cutover does). The release is in a `finally`, so timers are released on every exit from this step, including a failed install. No migration. Timers are not held during the purchase: the tick pass uses its own database and runs under its own unit, so it does not contend with the timed passes.
  3. **estimate** with `sudo mt-run data tick pass --estimate-only --json`. Stop if the planned figure exceeds the per-pass ceiling or the 30-day room; the stop names the figure and the PM's command (D4). Also stop if any planned request is outside D1's wants (for example a `tbbo` request). Nothing is bought in either case.
  4. **buy and ingest**: `sudo mt-run tick`, run up to `PASS_ATTEMPTS = 5` times while it exits 6. Exit 3 (units failed) continues to evidence, since failed units are findings (D6). If the attempts run out, the script stops: "jobs still processing after 5 runs; re-run the cutover". Exit 1, 2, 4 or 5, or a pass that dies mid-download, stops the script with the pass's exit code and the last journal lines. In every case, re-running the cutover resumes. The cases at this scale are listed below.
  5. **evidence** (D5, D6, D7), narrated to `user/notes/`.
- **Failure cases at about 900 requests.** These are the existing pass's behaviour (224 and 234), stated with their values:
  - *Wait budget.* Each run's await phase polls for `TICK_WAIT_BUDGET_SECONDS = 1800`, every 15 s. Five attempts give 2.5 h of waiting per cutover run. That bound is not claimed to cover 900 jobs. When it runs out, the cutover stops with the reason above, and running it again waits again. Jobs keep processing at Databento meanwhile, and none is re-submitted.
  - *Unit time limit.* `mt-tick-pass.service` has `TimeoutStartSec=4h`. A run killed at 4 h loses nothing, because units commit one at a time. The kill surfaces as a non-zero exit that stops the cutover, and the next run resumes.
  - *Throttling or refusal part-way through submission.* A 429 or an auth refusal marks that request's units `FAILED_RETRYABLE` and ends the run `provider_abort` (exit 2). Requests submitted before it are recorded, and the cutover stops. Any other 4xx exhausts that request (`RETRY_EXHAUSTED`) and submission continues; the run ends `partial` (exit 3), and the evidence note lists those requests. Re-running the cutover submits only the requests not yet accepted.
  - *Death after submit, before the job is recorded.* The request row is written, with its attempt stamped, before the paid call. On the next run, any row with no job id is matched against Databento's job list, and a match is recorded. Such a row is never submitted again: if no match is found within `TICK_SUBMIT_RESOLVE_AGE = 1h`, it becomes `RETRY_EXHAUSTED`, and only `reset` re-submits it. Meanwhile the spend guard counts any unrecorded provider job, so the 30-day cap still holds. The reconciliation fails if any provider job is still missing from the manifest at the end.
- The script exits 0 only when the reconciliation passes and every unit has reached a final state (ingested, or failed with a named reason). Coverage findings do not fail it.
- *Rejected: buy from the slice branch's dev checkout before merge.* It reaches the same database, but it proves the path 234 retired and leaves production's code behind its own manifest.

### D4 — Ceilings: the PM's one host command, set once
Status: decided

The runbook reserves the ceilings to the PM ("No agent edits them"). The env file is root-owned, and neither existing script changes a key once set (`add-tick-env.sh` only adds keys). So the slice adds a minimal `deploy/set-tick-spend-ceilings.sh --per-pass USD --cap-30d USD`, run as root and check-then-act:

- It refuses, writing nothing, unless both values are non-negative decimals and the file exists.
- It replaces the two keys `MT_TICK_SPEND_CEILING_USD` and `MT_TICK_SPEND_30D_CEILING_USD` in place, which keeps the file's mode and owner. It prints the old and new values; ceilings are not secrets.
- It has one test hook, the target path, so that one pytest can run it against a temporary file.
- It is not added to `deploy/sudoers.d/manta-ops`, so an agent cannot raise a ceiling.

**Values for this run:** per-pass **25.00** (the PM's cap). 30-day **35.00**: trailing spend was $8.50 on 2026-11-05, so 25 plus at most 10 of trailing spend. The PM's command:

```bash
sudo /home/manta/source/repos/manta/trading-data/deploy/set-tick-spend-ceilings.sh --per-pass 25 --cap-30d 35
```

**No restore afterwards.** After the purchase the trailing spend is about $33, so the 30-day cap leaves about $2 of room until that spend ages out. By then every want is covered and has a fixed end, so a pass plans nothing until a reviewed universe change (307) asks for more. 307 sets its own figures with the same command. Runbook 100's "afterwards the PM restores the per-pass ceiling" is amended for purchase slices to say this.

### D5 — Reconciliation: estimate, manifest, Databento's charge
Status: decided

`scripts/reconcile_tick_spend.py --since <UTC instant> --cap-usd <amount>` reads every `tick_request` requested since the instant, and Databento's batch job list (`batch.list_jobs`, free) over the same span. It prints one line per request: job id, schema, product, days, estimate, actual, delta. Then totals. It exits non-zero when:

- a provider job in the span has no manifest row (spend the guard could not see), or
- a delivered request has no `actual_cost_usd`, or
- the total actual exceeds `--cap-usd`, or
- Databento's job list cannot be read (an error or a timeout). The script stops with the provider's error and never treats a failed read as an empty list.

`--cap-usd` is required and is never read from an environment, because the dev checkout's `.env` holds the dev ceilings, not production's. The cutover passes the per-pass ceiling it read from `/etc/manta-trading.env` in preflight, 25.00 for this run.

A non-zero delta between estimate and actual is reported per request and totalled. It does not fail the script; it is the slice's result. 303 saw exact equality on 16 jobs, so a delta here is a finding about `get_cost`, written up with the requests it affects.

- **Deviation from the architecture: no invoice check in this slice** (recorded in the architecture's purchase-test entry). The architecture asks for the purchase to be checked against the invoice. This slice compares the estimate with Databento's per-job reported charge. That charge comes from the same provider channel as `actual_cost_usd`, so it proves `get_cost` matches the job cost. It does not prove the amount billed matches the manifest. November's invoice is issued after the month ends, and a slice task cannot wait for it. The invoice comparison is deferred to issue #69: compare November's usage total with this slice's reconciliation total. Issue #69 is a prerequisite of 307 in the slice plan.
- *Rejected: a new `mt data tick` verb.* Operator commands get their own slice. 305 owns spend in `accounting`, and this script is the evidence tool until then.

### D6 — What real data breaks is a finding, never patched
Status: decided

The first pre-2020 data is expected to show provider shapes the 2024 window did not. The rules already decide each one. This slice records them and changes none:

- **A definition with an undefined activation or expiration** fails its unit, naming the instrument (302). The bar and statistics days that need it stay `awaiting definitions`.
- **A statistics record whose `ts_ref` is not a session** fails `stat_reference` (303's unit 506, ESZ9 on a Saturday). The unit stays `retry_exhausted`.
- **ES bars before the Sunday open on 2018-08-05** (one bar, recorded by 301 as unexplained) fail `session_boundary` for the 2018-08-06 unit.
- **Holiday sessions with no statistics of their own** remain content findings, as 303 found.
- **A day the provider marks `missing`** is tallied and never bought. A `degraded` day is bought and shown as degraded.

Each finding goes in the evidence note: product, schema, UTC day or session, failure reason, and the record that caused it. The only fix in scope is a defect in this slice's path (the cutover, the scripts). Anything in an ingest or calendar rule is reported to the PM with the evidence, and its fix is designed separately. A calendar row is the exception: a missed CME holiday or early close found by `session_boundary` is fixed by a sourced row and `mt data tick reset` of the affected units, as 301 set out, because that is data, not a rule.

### D7 — Checks earlier slices left for the first real files
Status: decided

No new script. Existing commands cover what 301 and 302 handed over, and the results go in the evidence note. The close-minute bar check 301 also handed over is deferred to issue #70, since it is not part of the purchase test.

- **Session placement before 2020** (301): `verify_cme_sessions.py --calendar CME_EQUITY` over the 2017–2019 ES `ohlcv-1m` job directories in the archive. Exit 1 lists records outside any session, which should be the 2018-08-05 bar alone. It is not run on statistics or definitions: their `ts_event` is a publication time, not a trading time.
- **`tick_definition` growth** (302): rows before and after, per product, from `mt data tick debug definitions`.
- **The run's own scale**: request and unit counts from `debug requests` and `debug units`, and pass and ingest durations from the run's `pass_runs` row (`mt data overview`). These are recorded for 305; this slice does no other measuring.

### Technology Choices

- No new dependencies. The reconciliation uses the `databento` client already pinned (`batch.list_jobs`). The ceiling script reuses `deploy/lib/env_value.sh` and replaces the two values inline. It is the only caller, so there is no new library helper.

### Patterns and Conventions

- Cutover scripts follow `cutover_303_tick_migrations.py`: numbered check-then-act steps, narration to `project-documents/user/notes/`, exit 0 only when every check passed.
- `PASS_ATTEMPTS` is named once at the top of the cutover script.

## Implementation Details

### Migration Plan

No schema, table or migration change. The production change is the universe constant, released, and the rows and files the purchase writes. Behavior that must be preserved: the tick tier's plan. The universe unit test pins the ranges, the cutover's estimate stops on any request outside D1's wants, and a second `mt-run tick` plans nothing.

## Integration Points

### Provides to Other Slices

- **305**: real bar and statistics data over 2010–2026 for its completeness surfaces, and this run's request and unit counts and durations as a first data point at manifest scale.
- **306**: ES `ohlcv-1m` 2017-01 to 2026-10 to serve and roll up, overlapping held `tbbo` (2024-11 to 2024-12) for the parity check. Open interest and settlement for both products over the whole history.
- **307**: a proven purchase path at about 900 requests, the reconciliation script, the ceiling command, issue #69 (the invoice comparison, which 307 waits on), and a precise remainder: ES `ohlcv-1m` 2010-06-06 to 2017-01-01, GC `ohlcv-1m` in full, and every want past 2026-11-01.

### Consumes from Other Slices

- The pipeline and checks of 300–303, unchanged.

## Success Criteria

### Functional Requirements

1. `TICK_UNIVERSE` holds D1's wants, and import-time validation accepts them. A unit test pins the ranges.
2. In production, `sudo mt-run tick` completes. Every request is delivered or ends with a named failure (D3's failure cases), and every unit is in a final state: ingested, or failed with a reason D6 classifies.
3. `reconcile_tick_spend.py --cap-usd 25` exits 0 (D5).
4. `status` and `coverage` (walkthrough step 4) show ledger equal to raw in every session whose units ingested. Every other session is in the evidence note, classified by D6. The evidence note is the one place that lists the findings and D7's results.

### Technical Requirements

- `set-tick-spend-ceilings.sh`: shellcheck clean, and one pytest: the two values replaced and another key untouched, and a bad value refused with nothing written.
- `reconcile_tick_spend.py`: unit tests on canned manifest rows and job lists, covering an unheld job (non-zero exit), a missing actual (non-zero exit), a delta (reported, exit 0), a total over `--cap-usd` (non-zero exit), and a job list read that raises (non-zero exit, never an empty list).
- The cutover script reuses `cutover_common` and has no step that waits on a person. Its preflight names the PM's ceiling command if the ceilings are short.
- Format, lint and typecheck on touched files. Unit and integration tick tiers pass, apart from the known flakes and pre-existing failures.
- Runbook 100: the purchase procedure, the ceiling script, and the amended restore rule (D4).

### Integration Requirements

- `sudo mt-run data health` exits as it did before the cutover, with tick lines unchanged in kind. The new tiers reach health in 305.
- A second `sudo mt-run tick` after the purchase plans nothing and spends nothing.

### Verification Walkthrough

Run from the checkout root. Step 1 is Phase 6. Steps 2 to 5 run in Phase 7, after the release, and step 2 is the only one that spends money: about $24, usage-billed. Commands marked *(new)* do not exist yet.

1. **Tests.**
   ```bash
   export MT_TIMESCALE_TEST_URL="$(grep '^MT_TIMESCALE_TEST_URL=' .env | cut -d= -f2- | tr -d '"')"
   env -i HOME=$HOME PATH=$PATH MT_TIMESCALE_TEST_URL="$MT_TIMESCALE_TEST_URL" uv run pytest test/unit/data/tick test/unit/scripts test/unit/deploy -q
   ~/.local/bin/shellcheck deploy/set-tick-spend-ceilings.sh     # (new)
   ```
   Expected: pass, and the universe test pins D1's ranges.

2. **Ceilings, then the cutover.** The PM runs the one command in D4. Then:
   ```bash
   uv run python scripts/cutover_304_first_purchase.py v0.27.x       # (new)
   ```
   Expected: preflight passes; estimate at most $25 (about 24.2) with verdict `allowed`, only D1's wants and about 900 requests; `sudo mt-run tick` ends 0, or 3 with the failed units named; the narration lands in `user/notes/`.

3. **Reconcile.**
   ```bash
   uv run python scripts/reconcile_tick_spend.py --since <cutover start, UTC> --cap-usd 25   # (new)
   ```
   Expected: exit 0, with about 900 lines and the delta reported (303 saw 0).

4. **Verdicts.**
   ```bash
   sudo mt-run data tick status --json
   sudo mt-run data tick coverage --product ES --schema ohlcv-1m --start 2017-01-01 --end 2026-11-01 --json
   sudo mt-run data tick coverage --product ES --schema statistics --start 2010-06-07 --end 2026-11-01 --json
   sudo mt-run data tick coverage --product GC --schema statistics --start 2010-06-07 --end 2026-11-01 --json
   sudo mt-run data tick debug units --json     # every unit not ingested has a failure reason
   ```
   Expected: ledger equals raw in every session. Non-complete sessions fall in D6's classes or are the last `edge_unknown` sessions.

5. **Checks and nothing left to buy.**
   ```bash
   uv run python scripts/verify_cme_sessions.py --calendar CME_EQUITY /data/tick-archive/<2017–2019 ohlcv-1m job dirs>
   sudo mt-run data tick debug definitions --json                   # definition rows per product
   sudo mt-run data tick pass --estimate-only --json
   sudo mt-run data health
   ```
   Expected: session placement exits 0, or 1 listing only the 2018-08-05 bar. The estimate is `nothing_to_buy` at $0. Health is unchanged in kind.

## Risk Assessment

### Technical Risks

- **Scale.** About 900 batch jobs in one pass is roughly 50 times any earlier pass. Databento's job queue, the pass's wait budget and the manifest queries behind `status` are untested at this size.
- **Pre-2020 provider shapes.** Definitions with undefined windows, or statistics with non-session `ts_ref`, could be common rather than rare. Many days would then sit `awaiting definitions` or failed after the money is spent.

### Mitigation Strategies

- The pass is idempotent and resumable, and each scale failure case has a stated outcome (D3). In-flight jobs are reused, never re-submitted. A query too slow for `status` is a finding for 305, not a blocker here.
- Files are kept whatever happens, so a rule change later re-ingests them with `reset`, without buying again. The money at stake is $24, and finding these shapes now is the purpose of buying before 307.

## Implementation Notes

### Development Approach

Relative effort 2/5. One task file.

1. Universe edit and its test.
2. `set-tick-spend-ceilings.sh`, its test, shellcheck.
3. `reconcile_tick_spend.py` and its tests.
4. `cutover_304_first_purchase.py`.
5. Runbook 100 update; the evidence note skeleton.

Phase 7, after merge and release: the PM's ceiling command, then the cutover, reconciliation, verdicts and checks, and the evidence note completed.

### Special Considerations

- Spend is guarded twice: the guard refuses past either ceiling, and the cutover stops before buying if the estimate exceeds the per-pass ceiling. The ceilings are never raised by an agent.
- `ruff format` touched files only, and check `git diff main` for swept lines before each commit.
