---
docType: slice-plan
parent: user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md
project: trading
dateCreated: 20261028
dateUpdated: 20261028
status: not_started
---

# Slice Plan: Futures Historical Bars, Statistics and Tick Aggregation

## Parent Document

300-arch.futures-historical-bars-statistics-and-tick-aggregation.md — Deep bar history (`ohlcv-1m`), open interest and settlement (`statistics`), and contract definitions for ES and GC, stored as archive units in their own tables; the open-interest roll rule; one bars read path with a named source; completeness checks for every futures tier; serving; and a small real purchase that proves 220's purchase path. Tick-derived aggregates are conditional. Live capture is initiative 280.

Measured on 2026-10-28 (free metadata calls, usage-based list prices, `GLBX.MDP3` parent symbols, 2010-06-06 to 2026-10-15): ES and GC `ohlcv-1m` is $89.74, `statistics` $1.45, `definition` $2.72. Together $93.91, outside any subscription. `ohlcv-1s` is $1,111 and is not in this plan's slices.

## Foundation Work

1. [x] **(300) Archive Generalisation** — The archive, manifest and ingest stop assuming every schema is a tick tier. Schemas belong to families (tick tiers, bars, statistics, definitions); supersession applies within a family only, so a bar unit never supersedes a `tbbo` unit. Ingest selects its projector per schema from one registry instead of the single `tick_trade` writer, and the tier-plus-definition bundle stays a tick-tier rule. Each request row gains a plan-inclusion marker, set from one configuration table (schema to included history, from the Databento pricing page), and the 30-day spend guard counts marginal spend only; list-price estimates stay on the row for information. The `tick` track, `MT_TICK_*` settings and `mt data tick` names do not change. No new schema is added here, so the tick pipeline's behaviour is unchanged, which the existing tick tests and a parity run on the held files prove. Dependencies: none within this plan. Risk: Medium. Effort: 4/5

## Feature Slices (in implementation order)

2. [ ] **(301) ohlcv-1m and Definition History** — Adds `ohlcv-1m` and full-range `definition` as archive schemas, built and tested on the DBN project's sample files and the local files. Bars land in their own table keyed by contract identity (never `minute_ohlcv`, never `tick_trade`). `definition` back to 2010-06-06 is a prerequisite, since a bar record carries only an instrument identifier and contract identity comes through definition validity windows. Completeness ships with the tier: load-complete (unit verified, counts and first and last event time against the manifest) and the day-condition findings, in `mt data tick status` and `coverage`. The sparse-bar rule is applied: a quiet session is not a hole. Backup coverage of the new archive units is confirmed in this slice. Dependencies: [300]. Risk: Medium. Effort: 4/5

3. [ ] **(302) Statistics and the Open-Interest Roll Rule** — Adds `statistics` as an archive schema with its own table. A statistics record is assigned to a session from its reference time and trading date, never its event time, and revisions are resolved by update action and receive time (both confirmed against the DBN statistics record definition). The tier's completeness ships with it, including the cross-tier check that a session's `ohlcv-1m` volume agrees with `statistics` cleared volume. The open-interest roll rule is built on stored values and evaluated against calendar and volume for ES and GC, including the GC case where Databento's own continuous symbol differs from the volume rule. Dependencies: [300, 301]. Risk: Medium. Effort: 4/5

4. [ ] **(303) First Real Purchase** — Runs 220's purchase path with real money for the first time, capped at $25 (PM, 2026-10-28). What it buys is kept and useful: `statistics` and `definition` for ES and GC over the full range (about $4), and the most recent years of ES `ohlcv-1m` with the rest of the cap. It goes through estimate, spend guard, manifest, batch download, verify and ingest, and the actual charge is compared with the estimate and the manifest. The slice's evidence is that comparison and the tiers' completeness verdicts on what was bought. Dependencies: [301, 302]. Risk: Low. Effort: 2/5

5. [ ] **(304) Futures Completeness Surfaces** — Brings tick, bar and statistics verdicts together in `mt data health`, `accounting`, `overview` and the status coverage aggregates, in place and not as a parallel set. The slice defines "source edge" and "caught up" for each futures tier, extends `COVERAGE_SOURCE_TABLE` and the status coverage logic beyond minute and daily, and adds the remaining cross-tier content check (`ohlcv-1s` summed to minutes against `ohlcv-1m` is wired but inactive until that schema exists). It also measures manifest-scale queries, preflight and status over tens of thousands of units, since history acquisition multiplies today's row count about a hundred times. Dependencies: [301, 302]. Risk: Medium. Effort: 3/5

6. [ ] **(305) Serving the New Tiers** — Bars through `/api/v1/futures/*` follow the architecture's source rule: provider `ohlcv-1m` is the default for 1m and every coarser bar, rolled up on the CME session model; tick-derived bars only where no provider bar unit exists; provider `ohlcv-1h` and `1d` are never served. Every response names its source. Open interest and settlement get routes, or an explicit recorded decision not to serve them. Documentation for humans and for agents is updated (`docs/api/reference.md`, `agents.md`, the OpenAPI file). Provider 1h/1d bucketing is checked at this slice's design and recorded. Dependencies: [301, 302]. Risk: Medium. Effort: 3/5

## Integration Work

7. [ ] **(306) Futures History Acquisition** — Takes the full range through the same pass: ES and GC `ohlcv-1m`, `statistics` and `definition` from 2010-06-06, whatever 303 did not already buy. At today's list prices the whole set is about $94 outside a subscription, and the estimate and the guard print the real figure before any pull. Every unit is verified and ingested, the tiers' completeness verdicts and the cross-tier checks are run over the whole range, and the findings are recorded. Dependencies: [303, 304]. Risk: Low. Effort: 2/5

## Notes

- **Order:** 300 is the foundation. 301 and 302 follow in that order, since statistics needs the definitions and contract identity 301 builds. 303 comes before the large pull so the purchase path is proven on a small one. 304 and 305 depend only on 301 and 302 and can be taken in either order. 306 is last, so everything it ingests can be verified.
- **Completeness with each tier:** the architecture's principle is that a tier ships with its completeness check, so 301 and 302 each carry their own. 304 is the cross-tier layer and the operator surfaces, not the first check.
- **No PM host steps:** no slice here needs a root step or a PM-run command. The $25 cap in 303 is the PM's direction, entered as the configured ceiling for that run.
- **Subscription:** none of these slices needs it. Every schema in scope is purchasable usage-based for under $100 in total, and the plan-inclusion marker makes a later subscription cost the guard nothing.
- **Backup:** the new archive units and tables join the tick backup regime in the slice that first writes them (301), as 220 did for ticks.

## Future Work

- **`ohlcv-1s`** — The schema, its table and its completeness, with the `ohlcv-1s` summed to `ohlcv-1m` check activated. It uses the path 301 builds, so adding it is small once a purpose is stated. It is $1,111 at list outside a subscription and included in the Standard plan. Trigger: a stated research purpose.
- **Tick-derived continuous aggregates** — Built only if provider bars arrive late or a measured read latency, or a granularity provider bars lack, needs them. If built, the three constraints in the architecture (open and close ordering, session-clipped buckets across DST, and supersession invalidation) are tested first.
- **Provider `ohlcv-1h` and `ohlcv-1d`** — The bar-source rule never serves them. Revisited if we choose to use them (PM, 2026-10-28).
