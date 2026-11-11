---
docType: slice-design
slice: futures-history-acquisition
project: trading
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies:
- 304
- 305
interfaces: []
dateCreated: 20261108
dateUpdated: 20261109
status: complete
turns: 31
promptTokens: 2390385
cachedTokens: 2287970
cacheWriteTokens: 102355
completionTokens: 24792
durationSeconds: 311.8
costUsd: 1.77285
usageRounds: 1
pricedRounds: 1
usageByModel:
  claude-opus-5-5:
    rounds: 1
    costUsd: 1.7728497000000003
    completionTokens: 24792
---

# Slice Design: Futures History Acquisition

## Overview

This slice buys the rest of the ES and GC history through the purchase path slice 304 proves, then verifies all of it. It buys `ohlcv-1m`, `statistics` and `definition` from 2010-06-08 to a fixed end of 2026-12-01, minus what 304 already bought.

304 bought (D1 of 304): `definition` and `statistics` for ES and GC from 2010-06-08 to 2026-11-01, and ES `ohlcv-1m` for calendar 2024 only. Two corrections are made with this design, in the slice plan entry and in the architecture (its first design goal, and the purchase-test entry under Anticipated Slices):

- **ES bars from 2017.** The entry said 304 bought them. That was the plan before 304 was cut to one year.
- **Start 2010-06-08, not the dataset start 2010-06-06.** This is a deliberate departure, inherited from 304 D1. The CME calendars are populated from 2010-06-06T22:00Z, and a want starting earlier fails the planner and `status`. The first sessions (dated 2010-06-07) are never bought. The entry and the architecture's first design goal now say 2010-06-08, so a reader does not take those days as missing.

The remainder is:

- ES `ohlcv-1m` outside 2024: 2010-06-08 to 2024-01-01, and 2025-01-01 to 2026-12-01;
- GC `ohlcv-1m` in full, 2010-06-08 to 2026-12-01;
- `statistics` and `definition` for both products, 2026-11-01 to 2026-12-01.

Costs were measured on 2026-11-08 with free `metadata.get_cost` and `get_record_count` calls (`GLBX.MDP3`, parent symbols, end exclusive). The dataset's last available day was 2026-11-07, so November is measured through that day:

| Want | Range | Records | List price |
|---|---|---|---|
| ES `ohlcv-1m` | 2010-06-08 to 2024-01-01 | 7,150,162 | $26.10 |
| ES `ohlcv-1m` | 2025-01-01 to 2026-11-01 | 1,028,307 | $3.75 |
| GC `ohlcv-1m` | 2010-06-08 to 2026-11-01 | 15,908,107 | $58.08 |
| Both products: `ohlcv-1m`, `statistics`, `definition` | 2026-11-01 to 2026-11-07 | 126,647 | $0.15 |
| **Measured total** | | | **$88.09** |

The rest of November is about $0.60 more at the rate of its first six days, so the purchase is about $89, usage-billed. The estimate and the guard print the real figure before anything is bought.

After the purchase, every session is verified. The exit criterion is the slice plan's: each family's not-caught-up count in `mt data health` is zero, or every remaining session is recorded by name with its reason.

## Value

- **Deep futures history in the project's own database.** Minute bars, open interest and settlement for ES and GC from 2010, each placed in a CME session with contract identity. Research and the engine can reach back 16 years, where today bars reach back one year for ES and not at all for GC.
- **GC gets bars at all.** GC is the product where the open-interest roll rule differs from the volume rule (architecture, "Open-interest roll"). Bars across its whole history are what make that difference testable in a backtest.
- **A gap is a named finding, not a surprise in a backtest.** Every session that is not complete is listed with its reason, and the cross-tier volume check runs over the whole range, not one window.

## Technical Scope

**Included**

- `TICK_UNIVERSE`: the wants of D1, as one edit. No other pipeline change.
- Parameterising 304's cutover scripts in place, so 307 runs them (D3). Two checks are added that 304 did not need: the issue #69 gate (D2) and the dataset-edge check (D1).
- Failed requests (D5). Already in 304's scripts: a pass exit 2 (failed-retryable) stops the cutover non-zero. New: the estimate judgement accepts a resubmit of a request made since the cutover's start instant (304 refuses every resubmit), and a retry-exhausted request is reported as `acquisition failed` instead of failing the exit rule.
- Evidence (D6). Already in 304's scripts: reconciliation, `status` and `coverage` reads, unit counts per finding class, definition growth, durations. New: the per-session not-caught-up list, the health and accounting rows, and the `traded_vs_cleared` counts.
- The new D5 and D6 code goes in one new module, `scripts/purchase_verdicts.py`, so each of the four scripts stays within about 300 lines without a split at review time.
- The session-placement check runs per product calendar, so GC bars are checked against the metals calendar (D6).
- The evidence note `user/notes/307-history-acquisition-evidence.md`, with every not-caught-up session listed by name (D6). The cutover does not write it; it is completed in Phase 7 from the cutover's narration log, as 304's was (D3).
- Runbook 100's "Purchase slices" section gives the cutover's new arguments.
- A follow-up issue for the December invoice comparison, filed when the purchase is done (D7).

**Excluded**

- `ohlcv-1s` (Future Work in the slice plan), and any change to the tick tier or its `tbbo` range.
- Any fix to an ingest, statistics or calendar rule that the data shows wanting. Findings are recorded and designed separately (D5). A calendar row is the exception, as in 304 D6.
- The close-minute bar check of issue #70. It needs held `tbbo` and provider bars together, which 304's 2024 bars already give; 307 adds nothing it needs.
- Serving (306) and tick bars (308).
- New operator commands. Health, accounting, `tick status` and `coverage` (305) are the surfaces.

## Dependencies

### Prerequisites

- **304** (first real purchase): its Phase 7 purchase has run, its evidence note is complete, and its cutover and reconciliation scripts exist. 307 changes those scripts, so 304's own run must have finished first.
- **Issue #69**: November's Databento invoice compared with 304's reconciliation, with any difference resolved. It is closed before 307's purchase, and the cutover checks that (D2). The invoice is issued after the month ends, so 307's purchase runs in December at the earliest.
- **305** (completeness surfaces): every family's verdict and caught up in health, accounting and `/api/v1/status`, the `traded_vs_cleared` counts, and its manifest-scale measurement at about 30,000 units. If a 305 bound missed and an issue was filed, 307 also depends on that issue (305 D10).
- **301** (calendar history), **302** (`ohlcv-1m`), **303** (statistics): unchanged.
- The Databento account with no subscription. Every request is usage-billed, and `MT_TICK_DATABENTO_PLAN` is unset in `/etc/manta-trading.env`.

### Interfaces Required

- `mt data tick pass --estimate-only --json`: the planned figure, trailing 30-day spend, verdict and the planned requests.
- `sudo mt-run tick`: one recorded pass. Exit 3 means some units failed; exit 6 means jobs were still processing at the wait budget, and running it again resumes.
- `mt data tick status --json` (including the dataset edge), `coverage --product P --schema S --json`, `debug requests|units|definitions --json`.
- `mt data health` and `mt data accounting --json` (305): one row or line per product and family, with caught up, source edge and the `traded_vs_cleared` counts.
- `scripts/reconcile_tick_spend.py` (304 D5), `scripts/verify_cme_sessions.py` (301), `deploy/set-tick-spend-ceilings.sh` (304 D4), `scripts/cutover_common.py`, `scripts/upgrade_production.py`.
- `FUTURES_PRODUCT_CALENDAR` (product to calendar id) in `data/tick/constants.py`.

## Architecture

### Component Structure

```
universe.py (TICK_UNIVERSE wants, D1)                 ── code, released
deploy/set-tick-spend-ceilings.sh                     ── PM, root, once (D4)
scripts/cutover_304_first_purchase.py <tag> --slice 307 ...  ── agent, after release (D3)
   ├─ preflight: cutover_common, env file, issue #69 closed, dataset edge
   ├─ install: hold timers, install the ref, release timers
   ├─ estimate: sudo mt-run data tick pass --estimate-only --json, judged
   ├─ buy and ingest: sudo mt-run tick, repeated while it exits 6
   └─ evidence (scripts/cutover_304_evidence.py)
        ├─ reconcile_tick_spend.py --since <start> --cap-usd <per-pass>
        ├─ status, coverage per product and family, debug units
        ├─ health and accounting rows (305)
        └─ verify_cme_sessions.py per product calendar
user/notes/cutover-307-<tag>-<stamp>.log              ── narration (written by the cutover)
user/notes/307-history-acquisition-evidence.md        ── findings, completed from the log
```

The scripts read production state; only the `mt` verbs write it. The reconciliation stays read-only, as 304 D5 requires.

### Data Flow

1. **Plan.** The pass reads the edited wants. Days already covered (all of 304's purchase, and the held `tbbo` days' definitions) are dropped. The rest are grouped into month runs per schema and product. Expect about 390 requests: about 185 for ES bars, about 198 for GC bars, and one each for November's `statistics` and `definition` per product. Expect about 8,100 units, from 304's figure of about 4,100 units per full-range want.
2. **Guard.** The trailing 30-day spend plus the planned figure is checked against both ceilings. Nothing is plan-included.
3. **Buy, download, verify.** Batch jobs are submitted, awaited, downloaded to `/data/tick-archive` and verified. `actual_cost_usd` is written when each job is done.
4. **Ingest.** Definitions first (by the acquisition pass), then bars and statistics into `tick_ohlcv_1m` and `tick_statistics` with their ledgers, one unit per transaction.
5. **Judge.** `status`, `coverage`, health and accounting read the manifest, ledgers and raw tables. The reconciliation reads `tick_request` and Databento's job list. The evidence step writes every result and every not-caught-up session to the note.

### State Management

- **Manifest** (`tick_request`, `tick_archive_unit`): about 390 requests and 8,100 units added. With 304's about 16,500, the manifest stays under the 30,000 units 305 measured against.
- **Archive**: about 1.4 GB billable of bar files (0.49 GB ES and 0.89 GB GC per the architecture's table, less 2024). The pass's space verdict checks it. The existing tick-archive backup covers it.
- **Tick database**: `tick_ohlcv_1m` grows by about 24 M rows (the measured records). The counts before and after go in the evidence note.
- **Production env file**: the two ceilings, set by the PM (D4).
- **Cutover state**: `user/notes/307-cutover-start.txt` and `307-cutover-baseline.json`, named from `--slice` (D3), so a re-run reconciles the whole purchase.

## Technical Decisions

### D1 — What is bought, as one universe edit with a fixed end
Status: decided

| Entry | `definition` | `statistics` | `ohlcv-1m` |
|---|---|---|---|
| ES | 2010-06-08 – 2026-12-01 | 2010-06-08 – 2026-12-01 | 2010-06-08 – 2026-12-01 |
| GC | 2010-06-08 – 2026-12-01 | 2010-06-08 – 2026-12-01 | 2010-06-08 – 2026-12-01 |

- **One want per schema, over the whole range.** Universe validation keys wants by schema, so ES cannot hold two `ohlcv-1m` wants around 2024. It does not need to: the planner drops covered days, so the 2024 bars are not bought again. `_HISTORY_START` stays 2010-06-08 (304 D1, the calendars' populated range). `_HISTORY_END` becomes 2026-12-01, and ES and GC both use the shared tuple with `ohlcv-1m` added, so the four ranges are defined once.
- **Fixed end 2026-12-01, not open.** 304 D1 left the choice to 307. An open end would make every later `mt-run tick` buy the newest days, which is spend nobody chose. There is no tick timer, and the PM has said there is no tick cadence code yet. A daily pull is a separate decision. The end is a whole month and is past by the time the purchase can run (D2).
- **The dataset must reach the end before buying.** If the cutover ran before Databento published through 2026-11-30, the pass would buy a partial November and a later run would buy the rest, splitting the reconciliation. So the preflight stops unless the dataset edge `status` reports is at or after the latest want end in `TICK_UNIVERSE` (2026-12-01T00:00Z), read from the universe and not passed in, so the end is defined once. The edge needs to reach only the want end: the statistics published on 2026-12-01 for the last session are outside the want either way (next bullet).
  - The edge is one value per dataset (`tick_dataset_edge.available_end` for `GLBX.MDP3`), not per product, so ES and GC share it. The stop names that edge.
  - A missing or unreadable edge stops the cutover, as a `gh` failure does in D2. That covers `status` failing or timing out (the stop gives its exit code and error), the edge absent or null, and an edge that does not parse as a UTC instant (the stop quotes the text). It is never read as reached.
- **The last statistics sessions.** A session's statistics are published during the next session (303), so the final session or two before the end have statistics in a UTC day past the want. They stay not complete, as at 304's end. They are expected findings and are recorded by name (D6). Extending only `statistics` would not help, because the new last session would have the same property.
- Validation already holds: every start is on or after `CME_DATASET_START`, `statistics` and `ohlcv-1m` lie inside `definition`, and both entries have a tier.
- *Rejected: end at the purchase date.* It is a code edit made at cutover time, and a re-run on a later day would plan more.
- *Rejected: leave older ES bars for later.* The architecture's first design goal is bars from the start of the dataset for both products, and the ES bars before 2024 are $26 of the $89.

### D2 — Issue #69 gates the purchase
Status: decided

The slice plan makes issue #69 a prerequisite: 304's reconciliation compared with November's invoice. A difference there means the manifest's cost record is wrong, and the guard would be counting the wrong figure on a purchase 14 times larger.

- The cutover's preflight runs `gh issue view 69 --json state` and stops unless the state is `CLOSED`. The stop says the invoice comparison is not done.
- The check is in the preflight, before any spend, and not a task, so no task waits on a date. Phase 7 runs once the issue is closed.
- The issue number is passed as `--gate-issue 69` (D3), not written into the script.

### D3 — Parameterise 304's cutover in place
Status: decided

304's cutover is three files (`cutover_304_first_purchase.py`, `cutover_304_checks.py`, `cutover_304_evidence.py`) with 304's values in them. A copy for 307 would duplicate about 640 lines. Instead the files keep their names and their tests, and take 307's values as arguments:

- `--slice 307` (required): the prefix of the state files (`307-cutover-start.txt`, `307-cutover-baseline.json`) and of the narration log, `cutover-307-<tag>-<stamp>.log`. The script reads and writes nothing else under the notes directory; the evidence note is completed from the log by hand in Phase 7, so the script never needs its name.
- `--gate-issue 69` (required): the issue must be closed (D2). There is no argument that skips the gate. 304's tests call the preflight function with no gate issue directly; the CLI never offers that.
- The edge check (D1) always runs, against the latest want end in the universe. No argument.
- `--estimate`: preflight and the estimate only (D4).

Values that were 304's constants become derived:

- **Wanted keys and products** come from `TICK_UNIVERSE`, as 304's `wanted_keys()` already does. Coverage is read for every product and wanted family.
- **The session calendar** comes from `FUTURES_PRODUCT_CALENDAR` per product, and bar job directories are grouped by product (D6).
- **Ceiling values in the stop message** come from the estimate (D4).

The pass loop is unchanged: `PASS_ATTEMPTS = 5` runs of `sudo mt-run tick` while it exits 6, then the cutover stops with "jobs still processing after 5 runs; re-run the cutover" (304 D3). 304's existing tests stay where they are, updated only to pass `--slice 304`. New tests cover the gate, the dataset edge and the per-product calendar.

- *Rejected: a `cutover_307_*.py` copy.* It duplicates about 640 lines.
- *Rejected: renaming the files and adding a per-slice registry.* Nothing needs it yet. Rename and extract when a third purchase slice exists.
- *Rejected: a new `mt data tick purchase` verb.* Operator commands need their own slice, and a cutover script is the established pattern.

### D4 — Ceilings: the PM's one command, values from the estimate
Status: decided

The ceilings are reserved to the PM (runbook 100, "No agent edits them"), and `set-tick-spend-ceilings.sh` is not in the operator's sudoers. That is the one root step, as in 304. The slice plan's "No PM host steps" note said no slice needed a PM-run command; it is amended with this design to name the ceiling command in purchase slices (304, 307) as the exception.

The values cannot be fixed at design time, because 304's spend ages out of the 30-day window on a date set by when 304 runs. So the agent runs the free estimate first: `uv run python scripts/cutover_304_first_purchase.py --slice 307 --gate-issue 69 --estimate <tag>` runs preflight and the estimate only, and prints the PM's command:

- per-pass = the planned figure rounded up to the next `CEILING_STEP_USD` (5);
- 30-day = trailing spend plus the planned figure, rounded up to the next 5.

For the measured $89 and nothing trailing, that is `--per-pass 90 --cap-30d 90`. The same command and values are in the stop message when the full cutover finds the ceilings short. `CEILING_STEP_USD` is named once in `cutover_304_checks.py`.

No restore afterwards (304 D4): after the purchase every want has a fixed end and is covered, so a pass plans nothing until a reviewed universe change asks for more.

### D5 — What real data breaks is a finding, never patched
Status: decided

304 D6 holds unchanged: an undefined definition window, a statistics `ts_ref` that is not a session, a holiday without statistics, a provider `missing` day (tallied, never bought) and a `degraded` day (bought, shown degraded) are each recorded, not fixed. GC bars before 2020 are the newest shapes here, and 304's GC definitions over the same range will already have shown how often windows are undefined. A day waiting on a definition that failed stays `awaiting definitions`, and is a finding.

**A request that fails at acquisition** has one of two outcomes, set by the pass as it is today (224, 234):

- **Failed-retryable** (a 429 or an auth refusal; the pass exits 2): a stop. The cutover exits non-zero, and a re-run resubmits the same request (a `resubmit` in the estimate), never a new one, so no day is bought twice. The estimate judgement accepts a resubmit of a request made at or after the cutover's start instant, and still refuses any other resubmit. The cutover stays non-zero until the request delivers.
- **Retry-exhausted** (any other 4xx, or a submit Databento never recorded within `TICK_SUBMIT_RESOLVE_AGE`): final. Its units hold their days, so the planner never plans them again and only `mt data tick reset` re-submits them. The second estimate is therefore `nothing_to_buy`. The request and its days are listed in the evidence note with the provider's error, classified `acquisition failed`, and the PM decides on a reset.

The only fix in scope is a defect in this slice's path (the cutover scripts). A rule defect is reported to the PM with the evidence and designed separately. A missed CME holiday or early close is fixed by a sourced calendar row and `mt data tick reset` of the affected units, as 301 set out.

### D6 — Verification over the whole range, and the exit criterion
Status: decided

The evidence step writes these to the narration log, and they are copied into `user/notes/307-history-acquisition-evidence.md` in Phase 7:

1. **Reconciliation**: `reconcile_tick_spend.py --since <start> --cap-usd <per-pass>`, every request, estimate against charge.
2. **Per product and family**: health's row and accounting's line (305) for ES and GC `tbbo`, `ohlcv-1m` and `statistics`: sessions complete over sessions judged, source edge, caught up.
3. **The not-caught-up list**: for every family not caught up, every judged session that is not `complete` (and not a provider hole on a `missing` day), from `coverage` over the family's whole wanted range. Each line names product, schema, session date and the bucket `coverage` emits (`TickSessionStatus`), copied verbatim. For a failed unit it adds the `failure_reason` the manifest holds (`debug units`), also verbatim; 304's `classify_unit` already groups those reasons by the ingest check that wrote them. The surfaces and the manifest stay the single source of every bucket and reason, so the note cannot disagree with them. The script adds only two labels of its own, both things only a cutover knows: `publication after want end` (a statistics session whose records fall in the UTC day at or after the want end, D1) and `acquisition failed` (a retry-exhausted request, D5). A session the exit rule cannot account for fails it: bucket `failed` or `retry_exhausted` with no failure reason, bucket `in_flight` or `awaiting_ingest`, or bucket `missing`, `pending` or `edge_unknown` that is not `publication after want end`.
4. **Cross-tier**: `traded_vs_cleared` counts per product and volume family (`agree`, `under`, `over`, `no_trades`). Any `over` session is listed by name; it is a defect by 305 D5 and health fails on it.
5. **Session placement** of every `ohlcv-1m` file bought, with `verify_cme_sessions.py --calendar <the product's calendar>`, one run per product. This is the first pre-2020 run of the check, which 304 moved here. Records outside any session are listed.
6. **Scale**: request and unit counts, pass and ingest durations, and `tick_ohlcv_1m` and `tick_definition` rows before and after.

**Exit criterion (slice plan):** each family's not-caught-up count is zero, or every remaining session is in item 3 with its reason. The cutover exits 0 only when that holds, the reconciliation passes, every unit is final (ingested, or failed with a manifest failure reason or `acquisition failed`), no request is failed-retryable (D5), health's exit code is what it was before (or the change is an `over` or failed unit that item 3 or 4 names), and a second estimate is `nothing_to_buy`.

- *Rejected: requiring zero not-caught-up sessions.* Provider gaps and the trailing statistics sessions can never be fixed, and a criterion that cannot pass would be waived. Naming them is the criterion.

### D7 — The December invoice is a follow-up issue
Status: decided

The architecture applies invoice reconciliation to every usage-billed request. 307's purchase is billed on a monthly invoice issued after the month ends, and a slice cannot wait for it. When the purchase is done, the closing step files an issue like #69: compare the invoice's usage total for the purchase window with the reconciliation total in 307's evidence note. The next purchase slice gates on it with `--gate-issue`.

### Technology Choices

- No new dependencies. The issue gate uses `gh`, already used by the release skill, called through `cutover_common.out`. A `gh` failure stops the cutover with its error; it is never read as "closed".

### Patterns and Conventions

- Cutover pattern as 303 and 304: numbered check-then-act steps, narration to `project-documents/user/notes/`, exit 0 only when the exit rule holds, every step re-runnable.
- Per-slice values only as cutover arguments; everything else is derived from the universe and the product calendar map.

## Integration Points

### Provides to Other Slices

- **306** (serving): ES and GC `ohlcv-1m` over the whole range, to serve and roll up.
- **308** (tick bars): nothing it depends on.
- **The engine and research**: a recorded, named list of every gap in the futures history.

### Consumes from Other Slices

- **304**: the purchase path proven at real size, the reconciliation script and the ceiling command.
- **305**: verdicts, caught up and cross-tier counts in health and accounting. If a surface read times out at this size, it is a 305 D10 miss: recorded, and fixed by a query or index change under its own issue, never by raising a bound.

## Success Criteria

### Functional Requirements

1. `TICK_UNIVERSE` holds D1's wants and passes import-time validation; a unit test pins them.
2. The cutover with D3's 307 arguments stops before spending when issue #69 is open, when the dataset edge is before the latest want end (2026-12-01T00:00Z) or missing or unreadable, when `--gate-issue` is not given, when the ceilings are short (naming the D4 command), or when a planned request is outside the wanted set.
3. `--estimate` prints the PM's ceiling command with values derived by D4 and buys nothing.
4. In production, every request is delivered or retry-exhausted with its error named (D5), no request is failed-retryable, and every unit is final.
5. `reconcile_tick_spend.py` exits 0 over the purchase.
6. The evidence note holds D6's six items. Each family's not-caught-up count is zero, or every remaining session is listed with its `coverage` bucket and its reason (D6 item 3).
7. A second estimate after the purchase is `nothing_to_buy` at $0.

### Technical Requirements

- 304's tests pass, calling the preflight function with no gate issue, and new unit tests cover: `--gate-issue` not given (refused), an open gate issue (stop), a `gh` error (stop, never "closed"), a dataset edge before the latest want end (stop), the minimum edge read from the universe, a `status` failure, an absent or null edge and an unparseable edge (each stops, never read as reached), a missing `--slice` (refused), a request still failed-retryable after the pass (non-zero), a retry-exhausted request (listed, exit rule holds), ceiling values rounded up to the next 5, the session check run once per product with its calendar, and each unaccounted-for case of D6 item 3 failing the exit rule.
- Each script stays within about 300 lines.
- Format, lint and typecheck on touched files. Unit and integration tick tiers pass, apart from known flakes and pre-existing failures.
- Runbook 100's purchase section gives the cutover's arguments.

### Integration Requirements

- 305's surfaces read the full manifest within their measured bounds; `sudo mt-run data health` completes without a `futures completeness` timeout.
- The follow-up invoice issue exists (D7).

### Verification Walkthrough

Step 1 is Phase 6 and was run at implementation. Steps 2 to 6 run in Phase 7, after the release and once issue #69 is closed; they have not been run, because they need production at the release and spend money. Step 3 is the only step that spends money: about $89, usage-billed. The new arguments exist as of this slice.

1. **Tests.**
   ```bash
   export MT_TIMESCALE_TEST_URL="$(grep '^MT_TIMESCALE_TEST_URL=' .env | cut -d= -f2- | tr -d '"')"
   uv run pytest test/unit/data/tick test/unit/scripts -q
   uv run pytest test/integration/data -k tick -q
   uv run python scripts/cutover_304_first_purchase.py --slice 307 --estimate v0.0.0; echo "exit=$?"
   ```
   Expected (run 2026-11-09): `914 passed` for the first command; `577 passed, 115 deselected` for the second (about five minutes); the third prints a usage error `the following arguments are required: --gate-issue` and `exit=2`, and touches nothing. The 914 include 304's cutover tests and the universe pin.

2. **Estimate and ceilings.**
   ```bash
   uv run python scripts/cutover_304_first_purchase.py --slice 307 --gate-issue 69 --estimate v0.x.y
   ```
   Expected: preflight passes (issue #69 closed, dataset edge at or after 2026-12-01); about $89 planned over about 390 requests, only `ohlcv-1m`, `statistics` and `definition` for ES and GC; the PM's ceiling command printed, for example `--per-pass 90 --cap-30d 90`. The PM runs that one command. `--estimate` writes no state and does not stop on unset or short ceilings; it needs production already at the release carrying this slice, because the estimate reads the universe production runs. A `status` exit of 3 is a verdict, not a failure, and does not stop the edge check.

3. **Purchase.**
   ```bash
   uv run python scripts/cutover_304_first_purchase.py --slice 307 --gate-issue 69 v0.x.y
   ```
   Expected: the estimate is `allowed`; `sudo mt-run tick` ends 0, or 3 with failed units named; re-running resumes after exit 6 or a stop. The narration lands in `project-documents/user/notes/cutover-307-<tag>-<stamp>.log`, and the evidence note is completed from it. It lists each family's accounting row, every not-caught-up session (the last statistics session is `publication after want end`), any retry-exhausted request as `acquisition failed`, and the `traded_vs_cleared` counts; an `over` session or a failed-retryable request makes it exit 1 (re-running resumes).

4. **Reconcile.**
   ```bash
   uv run python scripts/reconcile_tick_spend.py --since "$(cat project-documents/user/notes/307-cutover-start.txt)" --cap-usd 90
   ```
   Expected: exit 0, one line per request, delta reported.

5. **Verdicts over the whole range.**
   ```bash
   sudo mt-run data health
   sudo mt-run data accounting --json | jq '.tick.products[] | {product, families: [.families[] | {schema, caught_up, sessions_complete, sessions_in_scope}]}'
   sudo mt-run data tick coverage --product GC --schema ohlcv-1m --start 2010-06-08 --end 2026-12-01 --json
   ```
   Expected: rows `futures ES ohlcv-1m`, `futures GC ohlcv-1m`, `futures ES statistics`, `futures GC statistics` are `OK`, or `INFO` with each not-caught-up session in the evidence note; no `FAIL`. GC `ohlcv-1m` coverage shows ledger equal to raw in every ingested session.

6. **Nothing left to buy.**
   ```bash
   sudo mt-run data tick pass --estimate-only --json | jq '{verdict, planned_usd}'
   ```
   Expected: `nothing_to_buy`, 0.

## Implementation Notes

### Development Approach

Relative effort 2/5. One task file.

1. `--slice`; derive products, wanted families and calendars from the universe; per-product session check; ceiling values from the estimate; `--estimate`. 304's tests updated for `--slice` and the preflight's gate parameter. Commit.
2. `--gate-issue` and the edge check from the universe, with their failure cases and tests; the D5 handling of failed requests, in `purchase_verdicts.py`. Commit.
3. The D6 evidence items in `purchase_verdicts.py`: the not-caught-up list with the two cutover labels, health and accounting rows, cross-tier counts. Commit.
4. The universe edit and its pin test; runbook 100; the evidence note skeleton. (The slice plan and architecture corrections are already made with this design.) Commit.

Phase 7, after merge and release, once issue #69 is closed: the estimate, the PM's ceiling command, the cutover, the reconciliation and the follow-up invoice issue.

### Special Considerations

- Spend is guarded twice, by the guard and by the cutover's estimate judgement. The ceilings are never raised by an agent.
- The purchase is about 14 times 304's in money but half its request count, so 304's run is the scale test for the pass. The wait-budget and timeout cases of 304 D3 apply unchanged.
- `ruff format` touched files only, and check `git diff main` for swept lines before each commit.
