---
docType: tasks
slice: futures-completeness-surfaces
project: trading-data
lld: user/slices/305-slice.futures-completeness-surfaces.md
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies:
- 302
- 303
interfaces:
- 307
projectState: Slice design committed and reviewed (CONCERNS). `tick status` and `tick
  coverage` know every futures family (302 bars, 303 statistics). `mt data health`,
  `accounting`, `overview` and the `/api/v1/status` tick block know only the tick
  tier. `_families` and `_judged` live in `tick_status_build.py` (288 lines).
dateCreated: 20261108
dateUpdated: 20261108
status: not_started
turns: 21
promptTokens: 1562390
cachedTokens: 1463310
cacheWriteTokens: 99036
completionTokens: 25697
durationSeconds: 180.9
costUsd: 0.799832
usageRounds: 3
pricedRounds: 3
usageByModel:
  claude-sonnet-5-5:
    rounds: 3
    costUsd: 0.7998323999999999
    completionTokens: 25697
revision_number: 2
---

# Tasks: Futures Completeness Surfaces

## Context Summary

- Working on slice 305. It brings every futures family into `mt data health`, `accounting`, `overview` and the `/api/v1/status` tick block, through one per-family report that `status` also uses.
- Changes no ingest or acquisition behaviour. The one behaviour change is statistics `caught_up` (D2): a session whose publication window is still open becomes `pending` and no longer counts against caught up.
- It delivers:
  - `data/tick/family_edges.py` (`FAMILY_PUBLICATION_LAG`, `source_edge`);
  - `data/tick/family_report.py` (`family_reports`, `futures_completeness`, `ReadProgress`, `FamilyStatus` moved here);
  - `data/tick/cross_tier_checks.py` (`traded_vs_cleared`, `INACTIVE_CROSS_TIER_CHECKS`);
  - per-family accounting lines, health rows, overview lines and API `families` records;
  - the manifest-scale load test, its seed, and `user/notes/305-manifest-scale-measurement.md`.
- Design reference: the slice design (`lld` above). Decisions are cited as D1–D10.
- Effort: 4/5. One task file; one slice branch `305-slice.futures-completeness-surfaces`.
- Conventions:
  - Run pytest with `MT_TIMESCALE_TEST_URL` exported from `.env` (quotes stripped).
  - Run mypy with the src, kalshi_support and tests paths in **one** invocation.
  - Format only the files you touched (`ruff format <files>`), then check `git diff` for unrelated deletions before each commit.
  - Existing tests to extend or keep green: `test/unit/data/tick/test_tick_status.py`, `test_tick_status_scope.py`, `test_statistics_status.py`, `test/integration/data/test_tick_accounting.py`, `test_health_tick.py`, `test/integration/test_overview_gather.py`, `test/unit/api_server/test_futures_models.py`, `test/load/test_231_futures_api_nfr.py`.
  - A failure outside the touched areas: re-run it in isolation, and compare against `main`, before investigating.
- Next planned slice: 306 (serving), then 307 (history acquisition), which consumes the surfaces and the measurement.

---

## 1. Source Edge and Edge-Bounded Judging (D1, D2)

- [x] **1.1 Create `data/tick/family_edges.py`**
  - [x] `FAMILY_PUBLICATION_LAG: Mapping[SchemaFamily, int]`: `TICK_TIER` 0, `BAR_1M` 0, `STATISTICS` = `STATISTICS_PUBLICATION_SESSIONS` (import 303's constant; do not restate the number).
  - [x] `source_edge(family, family_want_end, dataset_edge, calendar) -> datetime | None` per the D2 table: tick tier and bars use `min(want end, dataset available end)`; statistics use `min(want end, E)` where E is the close of the last session whose publication window (D through the close of the session after D) ends at or before the dataset available end. Each family uses its own want end only.
  - [x] Returns `None` when no dataset edge is known.
  - [x] Module docstring says this is the futures counterpart of `COVERAGE_SOURCE_TABLE`.
  - [x] Success: importable; no I/O; no file over ~300 lines.

- [x] **1.2 Point `COVERAGE_SOURCE_TABLE` at it**
  - [x] In `constants.py`, change only the docstring of `COVERAGE_SOURCE_TABLE` to name `FAMILY_PUBLICATION_LAG` and `source_edge` as the futures counterpart. No new entry.
  - [x] Success: `git diff` on `constants.py` shows a docstring-only change.

- [x] **1.3 Test the edges** in `test/unit/data/tick/test_family_edges.py`
  - [x] Pin test: `FAMILY_PUBLICATION_LAG.keys() == FAMILY_LEDGER.keys()` (D1).
  - [x] Lag for statistics equals `STATISTICS_PUBLICATION_SESSIONS`.
  - [x] Tick tier and bars: want end before the dataset edge gives the want end; after gives the dataset edge.
  - [x] Statistics: a dataset edge between a session's close and the close of the next session gives the previous session's close; across a weekend and a holiday (use the real calendar).
  - [x] Production shape: tick want ends 2024-12-31, statistics want ends 2026-11-01, dataset edge 2026-11-06. The statistics edge is in 2026; the tick edge is 2024-12-31.
  - [x] `None` dataset edge gives `None`.
  - [x] Success: all pass.

- [x] **1.4 Move `_judged` into `tick_status_scope` and bound it by the edge (D2)**
  - [x] `_judged` leaves `tick_status_build.py` for `tick_status_scope.py`, public name `judged`, taking the family's source edge: sessions overlapping the wanted range **and** closing at or before the edge. `tick_status_build.py` imports it; its line count does not grow.
  - [x] Wire it into the existing status path: both `_judged` call sites in `tick_status_build.py` (`_family_status` and `_product_status`) call `source_edge` with that family's own want end (tick tier for the product-level call) and pass the result to `judged`. Without this, 1.5's behaviour tests cannot pass.
  - [x] `caught_up` in `tick_status.py` keeps 225's rule unchanged.
  - [x] Sessions that overlap the want but close after the edge show as `pending` in the buckets and are not judged.
  - [x] Success: existing status tests still pass unmodified.
  - Note: scope_caught_up in tick_status_scope wraps judged + caught_up; source_edge None => caught_up None. calendar read clamps to the calendar's populated end (test DB edge is 2030, calendar ends 2027).

- [x] **1.5 Test the judged set**
  - [x] Regression: on the existing status fixtures, tick-tier and `BAR_1M` judged sets are identical before and after (compare to the golden files `es_status`, `es_tiered_status`).
  - [x] New (criterion 2): a statistics session whose window closes after the dataset edge is `pending` and leaves `caught_up` true.
  - [x] New (criterion 2): tick want ends 2024-12-31 and statistics want ends 2026-11-01; a statistics session missing in 2025 keeps `caught_up` false.
  - [x] Success: all pass, including `test_statistics_status.py` unchanged.

**Commit**: `feat: add family publication lag and edge-bounded judging`

---

## 2. One Per-Family Report (D3)

- [x] **2.1 Create `data/tick/family_report.py` and move `FamilyStatus` into it**
  - [x] Move `FamilyStatus` and `StatisticsFamilyStatus` from `tick_status_build.py`; re-import there so existing callers keep working.
  - [x] Create `data/tick/cross_tier_result.py` holding only `CrossTierResult`: frozen dataclass with `check` (name), `family` (the volume family judged), and counts by `VolumeClass` (`agree`, `under`, `over`, `no_trades`; use 303's values, not literals). `family_report.py` and `cross_tier_checks.py` both import it from here, which avoids an import cycle. (This module is not in the design's component list; it is the one addition, made for that reason.)
  - [x] Add four fields to `FamilyStatus`: `source_edge`, `records` (the family's `ledger_totals` sum over the family's wanted range), `cross_tier: tuple[CrossTierResult, ...]` (empty until task 3), and `load_complete_only: bool`.
  - [x] `load_complete_only` is True when the family is in `VOLUME_FAMILIES` and the entry wants no statistics (D4). Define the label text `load-complete only` once as a constant beside the field; status, accounting, health and the API all render from the field.
  - [x] Success: module compiles; `tick_status_build.py` is shorter than before.

- [x] **2.2 Extract `family_reports(conn, calendar_url, entry, edge, *, cross_tier: bool)`**
  - [x] Move the body of `tick_status_build._families` (and `_family_status`, `_statistics_status`) here. For each wanted family: `family_scope`, then `source_edge` (task 1), then `judged`, `caught_up`, and for statistics `content_findings` over the load-complete sessions; then `ledger_totals` for `records`, and `load_complete_only`.
  - [x] Define `ReadProgress` here: a small mutable recorder holding the current product, family and step (`dataset edge`, `scope`, `content`, `traded_vs_cleared`, `records`). `family_reports` takes a keyword `progress: ReadProgress | None = None` and writes product, family and step to it before each of its steps; it catches nothing.
  - [x] `status` calls `family_reports` with `cross_tier=True` for now (nothing consumes it until task 3).
  - [x] Contract lines and roll lines stay in `status` only (D3).
  - [x] Success: `tick status` text and JSON output unchanged except the new `FamilyStatus` fields (`source_edge`, `records`, `cross_tier`, `load_complete_only`) in its JSON.
  - Note: `records` sums ledger_totals over all current units of the family (date.min..date.max), as 230's accounting did for the tick tier, so records match the sessions in scope (which include sessions units touch outside the wanted range). Deviates from the 'wanted range' wording in 2.1.

- [x] **2.3 Test the report**
  - [x] Existing status unit and integration tests pass with no expectation change other than those four new keys.
  - [x] New: `test_family_report.py` seeds one product wanting the tick tier, `BAR_1M` and statistics; the report has three entries in `family_wants` order, each with its own `source_edge`, `records` equal to the ledger totals, `cross_tier` empty, and `load_complete_only` True for the volume families only when statistics is not wanted.
  - [x] Success: all pass; `test_tick_status.py` golden files regenerated only for those four keys, and the diff is reviewed.

- [x] **2.4 Add `futures_completeness(conn, url, universe, progress, *, cross_tier)`**
  - [x] It is the only function that reads the database for the surfaces. It writes the `dataset edge` step to `progress`, reads `dataset_edge` once, then calls `family_reports(..., progress=progress)` per product, and returns `dict[str, tuple[FamilyStatus, ...]]` keyed by product. It catches nothing (D5 "Failure and concurrency").
  - [x] Add one helper beside `ReadProgress`: `unavailable_with_progress(progress, unavailable_text) -> str`, returning `<product> <family>: <step>: <text>` (for example `GC statistics: traded_vs_cleared: canceling statement due to statement timeout`). Accounting (4.2), health (5.3) and the API block (7.2) all call it; none builds that string itself.
  - [x] Success: callable with a stub connection.

- [x] **2.5 Test `futures_completeness`**
  - [x] Two products in one call return reports for both; `dataset_edge` is read once (count statements).
  - [x] A stub that raises on a named step propagates the error unchanged, and `progress` names that product, family and step; `unavailable_with_progress` returns the example string above.
  - [x] Success: all pass.

**Commit**: `refactor: extract family_reports and add futures_completeness`

---

## 3. Cross-Tier Checks (D4)

- [x] **3.1 Define `INACTIVE_CROSS_TIER_CHECKS`** in `data/tick/cross_tier_checks.py`
  - [x] `INACTIVE_CROSS_TIER_CHECKS: Mapping[str, str]` with one entry: `ohlcv_1s_minute_sum` → `needs the ohlcv-1s family`. Comment names issue #71 and the three steps to activate (D4).
  - [x] Import `FamilyStatus` only under `TYPE_CHECKING` (with `from __future__ import annotations`), because `family_report.py` imports this module.
  - [x] Success: importable with no import cycle (`python -c "import manta_trading.data.tick.family_report"`).

- [x] **3.2 Implement `traded_vs_cleared(conn, entry, reports) -> tuple[CrossTierResult, ...]`**
  - [x] Applies only when the entry wants statistics and at least one of `VOLUME_FAMILIES` (tick tier, `BAR_1M`); otherwise returns `()`.
  - [x] For each wanted volume family: sessions that are `complete` in that family's report **and** in the statistics report; one call to 303's `volume_check` over them, unchanged; count verdicts per `VolumeClass`. Do not call `checkable_sessions` and do not judge sessions again.
  - [x] Range is every such session in the wanted range (no recent window).
  - [x] Success: callable with a stubbed `volume_check`.
  - Note: `traded_vs_cleared` takes `scopes: Mapping[SchemaFamily, ProductScope]` (the scopes `family_reports` already built) instead of `reports`, because `FamilyStatus` carries only bucket counts, not per-session verdicts.

- [x] **3.3 Test `traded_vs_cleared`**
  - [x] Unit (stubbed `volume_check`): no statistics want returns `()`; statistics but no volume family returns `()`; two volume families return two results; only sessions complete in both families are passed to `volume_check`.
  - [x] Counts per class are summed correctly, including a session class of `over`.
  - [x] Integration (real 303 fixtures, `test_tick_statistics_real.py`'s seeding): the counts equal `mt data tick coverage --schema statistics` for the same range (criterion 6).
  - [x] Success: all pass.

- [x] **3.4 Wire into `family_reports` and `status`**
  - [x] `family_reports` with `cross_tier=True` writes the `traded_vs_cleared` step to `progress`, then attaches the results to the volume families' `FamilyStatus.cross_tier`.
  - [x] `tick status` text and JSON print the counts per family and list every `INACTIVE_CROSS_TIER_CHECKS` entry as `not active: <reason>` (criterion 7).
  - [x] A family with `load_complete_only` set prints the label constant from 2.1 (criterion 5), matching 302's existing wording.
  - [x] Success: status output shows counts and the inactive line.

- [x] **3.5 Test the status wiring**
  - [x] Status text and JSON contain the cross-tier counts, the `ohlcv_1s_minute_sum` inactive line with its reason, and `load-complete only` for a product without a statistics want.
  - [x] `cross_tier=False` leaves `FamilyStatus.cross_tier` empty and issues no `volume_check` statement (count statements).
  - [x] Success: all pass.

**Commit**: `feat: add traded-vs-cleared cross-tier check to the family report`

---

## 4. Accounting (D6)

- [x] **4.1 Make `accounting_lines` one line per product and family**
  - [x] `TickAccountingLine` gains `family` and `schema`, plus `caught_up`, `source_edge`, `load_complete_only` and `cross_tier`.
  - [x] `accounting_lines(reports_by_product) -> list[TickAccountingLine]` becomes a pure conversion of `futures_completeness`'s result. It takes no connection and reads nothing; `futures_completeness` is the only reader. Update both callers here: the accounting command (4.2) and `api_server/tick_access.read_tick` (which calls `futures_completeness(cross_tier=False)` inside its single call). Its API output stays as 231 shaped it until 7.1 adds `families`.
  - [x] Records are the family's ledger total; sessions in scope and buckets come from the family's verdicts.
  - [x] Update the module docstring: the 230 rule now covers every family.
  - [x] Success: `tick_accounting.py` has no direct `family_scope` or `product_scope` call.
  - Note: the API caller is `api_server/routes/status.py` (`_tick_block`), not `tick_access.read_tick`; `read_tick` does not call `futures_completeness`. `TickBlock.from_result` keeps only the tick-tier lines, so the API output stays in the 231 shape. Supporting additions: `FamilyStatus.schema` (display schema, None for an unwanted tick tier); `futures_completeness` always includes the tick tier (`family_reports` runs with `include_tier=True`, and `FamilyWant.start` may be None), so a product wanting nothing keeps its tick-tier line; `unavailable_at(progress, unavailable)` in `family_report.py` wraps `unavailable_with_progress` for callers.

- [x] **4.2 Update `cli/commands/accounting.py`**
  - [x] Retitle the section `futures universe:` (constant `TICK_SECTION_TITLE`), print lines grouped by product, one per family, with sessions, records, caught up, source edge, cross-tier counts, and `load-complete only` where it applies. List `INACTIVE_CROSS_TIER_CHECKS` once at the end of the section.
  - [x] JSON keeps `tick.products` with one entry per product; each entry's existing top-level fields stay the tick tier's and a `families` list carries every family, tick tier included.
  - [x] The call is one `futures_completeness(cross_tier=True)` inside one `read_line`, then `accounting_lines` on its result; on `TickUnavailable` the unavailable line replaces the section, as today. Use `unavailable_with_progress` (2.4) for a partial failure.
  - [x] Exit code and recorded minute detail unchanged (230 TD7).
  - [x] Success: `uv run mt data accounting --json` parses and has `families`.

- [x] **4.3 Test accounting**
  - [x] Update `test/integration/data/test_tick_accounting.py`: one line per product and wanted family; sessions equal `status`'s for the same family; `records` equal ledger totals.
  - [x] Text: section title `futures universe:`; JSON: existing top-level fields identical to before, plus `families`.
  - [x] Failure: an injected statement timeout prints the unavailable line naming product, family and step; exit code unchanged.
  - [x] Text lists `ohlcv_1s_minute_sum` as not active with its reason (criterion 7), and a family with `load_complete_only` prints the label (criterion 5).
  - [x] `read_tick` after the 4.1 caller change: the existing API status tests (`test/integration/api_server`, status block) pass unchanged, so the API output is still the 231 shape.
  - [x] Minute accounting tests (`test_minute_accounting.py`) pass unchanged.
  - [x] Success: all pass.

**Commit**: `feat: report every futures family in mt data accounting`

---

## 5. Health (D5)

- [ ] **5.1 Add the row builder to `cli/commands/health_tick.py`**
  - [ ] Pure function `family_check(product, report) -> HealthCheck`, name `futures {product} {schema}` (display text only; logic keys on `SchemaFamily`).
  - [ ] State by the D5 table, first match wins: `FAIL` (counted) when any judged session is `failed` or `retry_exhausted`, or any `over` verdict; `INFO` (informational) when `caught_up` is None (detail `no dataset edge; completeness not judged`), or not caught up, or content findings, or `no_trades`; otherwise `OK` (counted).
  - [ ] Detail is one line: complete over judged sessions, the source edge, caught up or not, and each non-zero bucket or finding count by name; `load-complete only` where it applies.
  - [ ] Success: callable with no I/O.

- [ ] **5.2 Test the row builder**
  - [ ] One case per severity row: failed unit, retry_exhausted, `over` → `FAIL` counted; missing edge, not caught up with no defect, content findings, `no_trades` → `INFO` informational; caught up and clean → `OK`.
  - [ ] Precedence: a failed unit plus a missing session is `FAIL`. Detail text for the two examples in D5.
  - [ ] Success: all pass.

- [ ] **5.3 Wire `gather_tick` (D5 "Failure and concurrency")**
  - [ ] After `tick retention`, and only when the tick database answered, make **one** `read_line` call to `futures_completeness(cross_tier=True)` and emit one row per product and wanted family.
  - [ ] A failure inside the call emits one counted `FAIL` row named `futures completeness`, whose detail comes from `unavailable_with_progress` (2.4), and no per-family rows.
  - [ ] When the database does not answer, only the `tick database` row prints (unchanged). The `tick database` row stays informational (234 TD6).
  - [ ] Success: `gather_tick` returns the new rows in `futures {product} {schema}` order.

- [ ] **5.4 Test the wiring** in `test/integration/data/test_health_tick.py`
  - [ ] Seeded `failed` unit makes the row `FAIL` and `mt data health` exits 1 (criterion 4).
  - [ ] A seeded `over` volume verdict makes the row `FAIL`, exit 1.
  - [ ] One not-caught-up family is `INFO` and the exit stays 0 (the other severities are covered by 5.2).
  - [ ] An injected statement timeout gives exactly one counted `FAIL` row `futures completeness` naming product, family and step, and no per-family rows.
  - [ ] A product without a statistics want shows the `load-complete only` label (criterion 5).
  - [ ] Success: all pass; existing `tick database` and `tick retention` tests unchanged.

**Commit**: `feat: add futures family rows to mt data health`

---

## 6. Overview (D7)

- [ ] **6.1 Add `newest_bar_ns` and `newest_statistics_session` to `data/tick/status_reads.py`**
  - [ ] `newest_bar_ns`: newest `last_event_ns` in `tick_bar_ledger` over current units. `newest_statistics_session`: newest `session_date` in `tick_statistics_ledger` over current units. Mirror `newest_event_ns`; one statement each; None when empty.
  - [ ] Success: both are importable; `status_reads.py` stays near 300 lines (it is 277 now; extract if it would pass ~320).

- [ ] **6.2 Add the freshness lines to `cli/commands/overview.py`**
  - [ ] Constants beside `TICK_SOURCE_NAME`: `futures ohlcv-1m` and `futures statistics`.
  - [ ] Each line is one read on the composed connection; the statistics line shows the session date, not a timestamp. A family no product wants prints no line. A tick outage is still a line, never a failed overview (230 TD8).
  - [ ] `overview.py` is already 414 lines: put the new gatherers in a new module (for example `overview_futures.py`) and keep `overview.py` growth to the call sites.
  - [ ] Success: overview prints `futures ticks`, then the new lines.

- [ ] **6.3 Test overview** in `test/integration/test_overview_gather.py`
  - [ ] Both lines appear when a product wants the family; neither appears when no product wants it; the statistics line shows a session date.
  - [ ] Tick outage: each line shows the unavailable text, and the overview still returns (criterion 9).
  - [ ] Success: all pass; existing overview tests unchanged.

**Commit**: `feat: add per-family freshness lines to mt data overview`

---

## 7. `/api/v1/status` Block (D8)

- [ ] **7.1 Add `FamilyAccountingRecord` and `TickAccountingRecord.families`** in `api_server/models/futures.py`
  - [ ] `FamilyAccountingRecord`: `family`, `schema`, `sessions_in_scope`, `sessions_complete`, `buckets`, `records`, `caught_up` (nullable), `source_edge` (nullable datetime), `load_complete_only`. Field docs say `caught_up: null` means no edge or no wanted range, never "assume yes".
  - [ ] No cross-tier fields.
  - [ ] Success: existing fields keep name, type and meaning.

- [ ] **7.2 Serve it from `tick_access.read_tick`**
  - [ ] Call `futures_completeness(cross_tier=False)` (wired in 4.1) and map each line's family fields into `FamilyAccountingRecord`. On error, return the existing non-`ok` state with the `unavailable_with_progress` detail and no products (231).
  - [ ] Success: `GET /api/v1/status` returns `families` per product.

- [ ] **7.3 Test the API block**
  - [ ] Unit: model round-trip; the existing `TickAccountingRecord` JSON for a product without families still validates.
  - [ ] Integration: `families` present per wanted family, no cross-tier keys, existing fields identical to the 231 shape; `load_complete_only` is true for a volume family of a product with no statistics want (criterion 5); an unreadable tick tier returns the non-`ok` state and empty products.
  - [ ] Success: all pass (criterion 10).

- [ ] **7.4 Document the block**
  - [ ] Update `docs/api/reference.md` and `docs/api/agents.md` with `families` and the `caught_up: null` meaning, and regenerate `docs/api/openapi.json` with the project's existing export command (look it up in the 231/190 task files or `README.md`; do not hand-edit).
  - [ ] Success: `git diff` on `openapi.json` shows only the new model and field; the existing openapi drift test passes.

**Commit**: `feat: serve per-family completeness in /api/v1/status`

---

## 8. Four-Surface Agreement (criterion 3)

- [ ] **8.1 Agreement test** `test/integration/data/test_family_surface_agreement.py`
  - [ ] Seed one database with two products and all three families, including a missing session, a provider-hole day and an open statistics window.
  - [ ] Build `build_status`, the health rows, `accounting_lines` and the API block from it. For status, accounting and the API, assert identical buckets, `caught_up` and `source_edge` per product and family.
  - [ ] Health rows carry no bucket map: parse each row's detail (the D5 format: complete/judged counts, source edge date, caught-up wording, non-zero buckets by name) with a small helper in the test, and assert the same values.
  - [ ] Success: passes; fails if any surface judges a different session set.

**Commit**: `test: add four-surface futures completeness agreement`

---

## 9. Manifest-Scale Seed and Measurement (D9)

- [ ] **9.1 Create the seed fixture and the units** in `test/load/manifest_scale_seed.py`
  - [ ] Session-scoped fixture creating one throwaway database on the test cluster, migrated to head. It never reads the production URL variable. The finalizer drops only the database this fixture created, so an aborted run leaves nothing behind (destructive-statement rule).
  - [ ] Seed ES and GC, 2010-06-08 to 2026-11-01, every real-calendar session: `definition`, `statistics`, `ohlcv-1m` units plus held-window `tbbo` units, all `ingested`, in month-run requests (about 30,000 units). Bulk-load with set-based SQL (`generate_series`/`COPY`), not per-row inserts.
  - [ ] Success: the fixture builds once per test session and prints the unit count (about 30,000).

- [ ] **9.2 Seed ledgers, definitions, day conditions and the dataset edge** (same file)
  - [ ] Ledger rows per unit at real proportions (instruments per day and stat types per instrument from the real fixtures under `test/fixtures/databento/real/`, the same ones `test_tick_statistics_real.py` seeds from); `tick_definition` rows per instrument; day conditions for every day; one `tick_dataset_edge` row. No `tick_ohlcv_1m` raw rows.
  - [ ] Success: every family's ledger count is non-zero.

- [ ] **9.3 Seed `tick_statistics` and compress** (same file)
  - [ ] `tick_statistics` at full size (about 19.4 M rows, real stat-type mix from the fixtures).
  - [ ] Compress chunks explicitly with `compress_chunk` (the test cluster has no scheduler).
  - [ ] Success: row count within 10% of 19.4 M and chunks report compressed.

- [ ] **9.4 Test the seed's shape** in `test/load/test_305_manifest_scale_nfr.py` (first test only)
  - [ ] Assert unit count within 5% of 30,000, per-family ledger counts non-zero, `tick_statistics` row count within 10% of 19.4 M, and chunks compressed.
  - [ ] Confirm the load tier's existing guard test (which refuses the production URL) picks up the new file. The load tier runs only under `MT_RUN_LOAD_TESTS=1` and is not part of CI (`.github/workflows` has no load step), so this file adds no CI wiring.
  - [ ] Success: passes under `MT_RUN_LOAD_TESTS=1`; skipped otherwise.

**Commit**: `test: add manifest-scale seed fixture`

- [ ] **9.5 Add the timed tests (D9 table)** in the same file
  - [ ] Each is the median of three runs after one warm-up, printing the samples:
    - `futures_completeness(cross_tier=True)`, both products, every family: ≤ 5 s total and no single statement ≥ 5 s;
    - the API tick block (`cross_tier=False`): added latency < 0.25 s;
    - `build_status`, both products: ≤ 30 s;
    - tick preflight plus a steady-state `pass --estimate-only` that plans nothing: ≤ 10 s;
    - `select_units` with nothing selectable: ≤ 2 s.
  - [ ] Record the slowest statement by timing each statement through the connection's logging hook. In the same test run, execute `EXPLAIN (ANALYZE, BUFFERS)` on that statement and print the statement and its plan, so the note and any fix start from the output (the database is dropped afterwards).
  - [ ] Success: the test runs and prints every median, its bound, and the slowest statement with its plan.

- [ ] **9.6 Run the measurement**
  - [ ] `MT_RUN_LOAD_TESTS=1 uv run pytest test/load/test_305_manifest_scale_nfr.py -s`. The run takes several minutes; run it in the background with output to `/tmp/305-measurement.log` and read that file when it finishes.
  - [ ] Success: the run completes and its output is in `/tmp/305-measurement.log`; the throwaway database is dropped afterwards (verify with `\l` on the test cluster).

**Commit**: `test: add manifest-scale seed and futures surfaces load test`

---

## 10. Fix Any Missed Bound (D10) and Record the Note

- [ ] **10.1 Triage the result**
  - [ ] If every bound passed, mark 10.2 and 10.3 not applicable in a note line and continue to 10.4.
  - [ ] Otherwise, take each miss's statement and plan from the 9.5 output in `/tmp/305-measurement.log` (written by 9.6).
  - [ ] Success: each miss has a recorded plan.

- [ ] **10.2 Fix a miss by query rewrite or index only (D10)**
  - [ ] Rewrite per-session reads to set-based reads, or add tick migration `tick_011_*` (read the last migration id in the tick migrations directory first; it should follow `tick_010_statistics`). An index migration needs its own migration test and an entry in the migration-chain test.
  - [ ] Never raise a bound. If the fix needs stored or cached derived state, a new table or a change in what a surface reports, stop: file an issue on trading-data with the measurement, note that 307 depends on it, and record that in the note.
  - [ ] Success: the code change is minimal and the surface tests from tasks 4–8 still pass.

- [ ] **10.3 Re-measure**
  - [ ] Re-run 9.6 until every bound passes, or the issue in 10.2 is filed.
  - [ ] Success: the final run's medians are all within bound, or each remaining miss names its issue.

**Commit** (only if 10.2 changed code, after the re-measurement passes): `fix: bring futures surfaces within manifest-scale bounds`

- [ ] **10.4 Write `project-documents/user/notes/305-manifest-scale-measurement.md`** (with YAML front matter per `file-naming-conventions`)
  - [ ] Unit and request counts; each median with its three samples and its bound; the slowest statement with its `EXPLAIN (ANALYZE, BUFFERS)`; the host (CPU, RAM, Postgres and TimescaleDB versions); and any D10 fix or issue with its before and after medians.
  - [ ] Success: the note exists and every figure matches the last run's output.

**Commit**: `docs: record 305 manifest-scale measurement`

---

## 11. Close-Out

- [ ] **11.1 CHANGELOG and docs**
  - [ ] Add a CHANGELOG entry under the unreleased section: the new health rows, accounting section, overview lines, API `families`, and the statistics `caught_up` change (call it out as a behaviour change). No version bump; releases are separate.
  - [ ] Update the slice plan's 307 entry only if it does not already carry the exit criterion that each family's not-caught-up count is zero (or every remaining session is named with its reason); check first.
  - [ ] Success: CHANGELOG diff reads accurately.

- [ ] **11.2 Final validation**
  - [ ] `ruff format` on touched files only, `ruff check`, mypy in one invocation, unit, integration, and the touched load test (`test_305_manifest_scale_nfr.py` and `test_231_futures_api_nfr.py`).
  - [ ] Confirm `tick_status_build.py` has not grown past 288 lines.
  - [ ] Check `git diff main --stat` for deletions you did not intend (format sweeps).
  - [ ] Confirm every success criterion 1–10 in the slice design maps to a passing test named in tasks 1–8, and that criterion 7's inactive listing and criterion 5's label are covered.
  - [ ] Success: all pass (a failure outside the touched areas passes in isolation); no file over ~300 lines among the new modules.

**Commit**: `docs: add 305 changelog entry`
