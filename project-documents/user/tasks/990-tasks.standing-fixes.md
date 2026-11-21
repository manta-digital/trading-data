---
docType: tasks
slice: standing-fixes
project: trading-data
lld: user/slices/990-slice.standing-fixes.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: []
interfaces: []
dateCreated: 20261119
dateUpdated: 20261119
status: in_progress
---

# Tasks: Standing Fixes

## #46 Index instruments: own symbols, a calendar, and their own data

Issue [#46](https://github.com/manta-digital/trading-data/issues/46), with [#4](https://github.com/manta-digital/trading-data/issues/4) (906 instruments skipped for having no trading calendar) as the same set. Findings are on #46 (comment 2026-11-19).

**What's wrong.** The 904 index instruments from EODHD's `INDX` exchange are stored with a bare symbol (`VIX`, `GSPC`, `DJT`) and no trading calendar. The daily pass skips them for having no calendar. Any fetch would ask EODHD for `{symbol}.US`, which is the wrong exchange. 24 of them share a symbol with an equity (DJT, MID, DWS, …). Bars and instrument updates are keyed by symbol alone, so those index rows show the equity's `first_data_date`. The bars themselves are the equity's and are correct. **Nothing is purged.**

**Decision (PM, 2026-11-19).** An index's symbol is its EODHD ticker, `{code}.INDX` (the instrument's existing `canonical_id`). That can't collide with an equity, and every EODHD fetch path already passes dotted symbols through unchanged. The three index rows on EODHD's US exchange (`^TNX`, `^W5000`, `^MGTN`) already work and are left alone.

### 1. One EODHD ticker rule, and index rows built right

- [x] **1.1** One function returns a symbol's EODHD ticker: the symbol itself when it has an exchange suffix, else `{symbol}.US`. `daemon/daily.py::_normalise`, `daemon/minute.py` (which imported daily's `_normalise`), `daily/providers/eodhd.py`, `adjustment/providers/eodhd.py`, `historical_minute/providers/eodhd.py` and `universe/populate_delisted_dates.py` (six copies or callers in all) call it instead of their own copies. The function is `eodhd_ticker` in `src/manta_trading/data/eodhd_ticker.py`.
  - [x] Unit test: `AAPL` → `AAPL.US`, `VIX.INDX` and `^TNX.US` unchanged.
- [x] **1.2** `universe/rebuild.py::build_upsert_rows`: an `INDX` row gets `symbol = canonical_id` (`{code}.INDX`) and the same trading calendar as equities. The calendar id is defined once, not as a second `"NYSE"` literal. The calendar id is `US_EQUITY_CALENDAR_ID` in `constants.py`; `HEALTH_MINUTE_SESSION_CALENDAR` now refers to it.
  - [x] Unit test: an equity `DJT` and an index `DJT` in one payload give two rows with symbols `DJT` and `DJT.INDX`. The index row has the calendar, and the equity row is unchanged.
  - [x] Unit test: a rebuild over an existing `VIX.INDX` row keeps it (match by symbol still finds it) and creates no bare `VIX` row.
- [x] **1.3** **Commit**: ruff and format on the touched files, mypy, unit tier, then `fix: give index instruments their own .INDX symbol and a calendar`. Unit tier 5,835 passed; ruff and mypy counts unchanged from HEAD (91 mypy errors, all pre-existing).

### 2. Move production's index rows

- [x] **2.1** List every table keyed by symbol text that holds rows for a bare index symbol, read-only on production. At least `acquisition_state` and `data_gaps`; check the rest. Record the list and counts in this group.
  - Findings (read-only on production, 2026-11-19): Tables with a `symbol` column: `acquisition_state`, `daily_ohlcv`, `data_gaps`, `dividends`, `instruments`, `minute_ohlcv`, `splits`, `universe_members`. 904 INDX instruments: 880 unshared codes, 24 shared with an equity. Unshared: 880 `acquisition_state` rows (minute, outcome `partial` — the minute pass already tries every index nightly as `{code}.US`), no gap/dividend/split/member rows, no daily bars, no minute bars in the last 400 days. Shared codes: 48 state, 243 gap, 127 dividend and 7 split rows, all the equity's.
- [x] **2.2** Migration (next free id on the minute track): for `asset_class='index'` rows with `eodhd_exchange='INDX'`:
  - set `symbol = canonical_id` and the equity trading calendar;
  - set `first_data_date` and `delisted_date` to NULL where the bare symbol is also an equity's (the 24 collisions), since those dates came from the equity;
  - in the symbol-keyed tables from 2.1, rename bare-symbol rows to `{code}.INDX` **only for the ~880 symbols no equity shares**. Rows under a shared symbol belong to the equity and stay as they are.
  - It must be idempotent (re-running changes nothing) and delete no bar rows.
  - Migration `067_index_instruments_indx_symbols`, SQL in `market/schema/migrations/index_symbols.py`. Shared vs unshared is decided by any other instrument holding the code. It refuses on daily bars under an unshared bare code instead of moving them.
- [x] **2.3** Integration test on a throwaway database seeded with an equity `DJT` that has bars, an index `DJT`, an index `VIX` with an `acquisition_state` row, and `^TNX`:
  - [x] after the migration, `DJT.INDX` exists with no `first_data_date`, the equity `DJT` and its bars and state are unchanged, `VIX` became `VIX.INDX` with its state row, and `^TNX` is unchanged;
  - [x] a second run changes nothing.
  - `test/integration/test_migration_067_index_symbols.py`, plus an abort test. The migration-list unit tests were retargeted to 067, and the mock pool is now sized from the list.
- [x] **2.4** Cutover script on the pattern of `scripts/cutover_0244_symbol_names.py`: check-then-act, holds and releases the timers, applies the migration, and verifies against production. 904 `.INDX` symbols; zero index rows without a calendar; `/api/v1/symbols/DJT` still the equity with its bars.
  - `scripts/cutover_046_index_symbols.py`. It verifies through the API: VIX.INDX and DJT.INDX are indices, and DJT is still the equity with daily data from 2021-09-30.
- [x] **2.5** **Commit**: `fix: move index instruments to their .INDX symbols in production`.

### 3. Check index data before the passes pick it up

- [x] **3.1** Measure on EODHD how far back index data goes, for 1m (VIX.INDX, GSPC.INDX, a thin index) and for daily. Record the depth and the quota cost of a full backfill of the 904 against the ~100k/day quota (memory: normal passes already use most of it).
  - [x] Measured 2026-11-19. Daily: back to 1990 for GSPC, VIX and DJT.INDX, and 2007 for ADVN. 1m: GSPC from early 2020 (present 2020-04, absent 2020-01-02); NDX and DJI present in 2023; RUT.INDX, DJT.INDX and ADVN.INDX return no 1m at all. Full minute backfill ≈ 904 × ~21 chunks of 120 days × 5 credits ≈ 95k credits once. Daily ≈ 904 credits per pass.
- [x] **3.2** Confirm the minute pass's symbol selection includes the index rows after the migration, and see what minute accounting and `mt data health` will report for them. A health check must not turn red just because 904 symbols start with no minutes.
  - [x] The minute pass selects every instrument, and already tries all 880 unshared indices nightly as `{code}.US`. Minute accounting excludes `eodhd_type = INDEX`, and the session-mass check only counts bars, so neither can turn red from indices starting empty.
- [x] **3.3** Run the off-tape detector over one backfilled week of VIX.INDX and GSPC.INDX minutes on a throwaway database. VIX minutes trade a few contracts each, so check the marked share is sane before production runs the detector on indices. If it marks much of a normal day, stop and file what you saw on #46.
  - [x] Ran `mark_symbol_days` on EODHD 1m for VIX.INDX (2,828 bars), GSPC.INDX (6,888) and NDX.INDX (6,863), 2026-11-09..13: 0 marked. GSPC and NDX carry about 1,390 1m bars a day, nearly round the clock.
- [x] **3.4** Write the outcome of 3.1–3.3 here. If 3.1 shows the backfill can't fit the quota alongside normal passes, add the throttling needed. Cut cost, never scope: the whole index set stays the target.
  - [x] No change needed. The one-time ~95k credits is spread by the minute pass's quota guard (402 → wait), and the whole index set stays the target.
- [x] **3.5** **Commit** (if 3.2–3.4 changed code): `fix: count index instruments correctly in minute accounting and health`. No code changed in 3.2–3.4, so no commit.

### 4. Docs, release and production

- [x] **4.1** `docs/api/reference.md` and `docs/api/agents.md`: index symbols are `{code}.INDX` (e.g. `/api/v1/bars/VIX.INDX`), indices have no volume worth reading, and split/dividend adjustment doesn't apply to them. `scripts/check_api_docs.py` passes.
- [x] **4.2** CHANGELOG `### Fixed` entry: indices now have their own symbols and data, and a client asks for `VIX.INDX`. Mention that `DJT` and the other shared symbols were always the equity.
- [x] **4.3** **Commit**: `docs: document .INDX index symbols`.
- [x] **4.4** Release (patch) and run the cutover from 2.4 per the release skill. Released 0.27.6 (b6a5265b): CI green, GitHub release and PyPI up. The first cutover run applied 067, but its verify hit mt-serve before it had bound its port. `wait_for_serve` was added to `scripts/cutover_common.py` (commit 'fix: wait for mt-serve to answer before a cutover verifies through it'), and the re-run PASSED.
- [x] **4.5** Production check:
  - [x] run the daily pass for `VIX.INDX` and `GSPC.INDX` only (`--list` with those two);
  - [x] `curl -s 'http://127.0.0.1:8100/api/v1/bars/VIX.INDX?granularity=1d&limit=5'` returns 5 bars, and the same for `GSPC.INDX`;
  - [x] `/api/v1/bars/DJT?granularity=1d&limit=5` returns the same bars as before the release.
  - [x] Production 2026-11-19: 904 `.INDX` symbols, 0 without a calendar, 0 unmoved, 880 state rows moved. The daily pass stored VIX.INDX 1990-01-02..2026-11-19 (9,323 bars) and GSPC.INDX 1927-12-30..2026-11-18 (24,839). `start`/`end` reads return them. `limit=5` returned 4 and 3, because `daily_coverage` holds only its refresh window for backfilled history. That's issue #18, with this case recorded there. DJT's bars are unchanged.
- [x] **4.6** Comment the results on #46 and close #46 and #4. Tell trading-engine and mt-ui that indices are served as `{code}.INDX`. #46 and #4 closed with results. trading-engine and mt-ui told (Remote Control, not confirmed read).

## #83 Daily gap detection reports every session missing

Issue [#83](https://github.com/manta-digital/trading-data/issues/83). `compute_missing_ranges` compared a session's open (14:30Z or 13:30Z) with daily bars stamped 00:00Z on the session date, so every daily session in a window looked missing. Production's daily gap rows (15,941, measured 2026-11-21 read-only) mostly span sessions whose bars are stored. The daily daemon does not schedule fetches from these rows, so no quota was spent on them; the rows only misreport coverage.

**Decision.** Existing rows are shrunk in place to the sub-ranges that truly lack a bar, keeping each row's status and attempt count. They are not rewritten through `update_data_gaps`, which would reset terminal rows to `UNKNOWN`. Dry run on production: 35 unchanged, 6,371 deleted, 4,632 shrunk, 4,903 split; 15,941 rows become 148,793 (the real single-session holes the long ranges hid).

### 1. Fix and reconcile

- [x] **1.1** Daily detection compares by session date, and looks bars up by the sessions' dates so the first session's own bar is found. Tests on real session rows and real `00:00Z` stamps fail on the old code. Commit `fix: compare daily bars to sessions by date in gap detection`.
- [x] **1.2** `reconcile_daily_gaps` (`gaps/daily_gap_reconcile.py`): delete, shrink or split each daily row, state kept, idempotent, rows with no lifecycle anchor left as found. Integration test on a throwaway database covers full, partial and no coverage, the dry run, the second run, and the unanchored symbol.
- [x] **1.3** `scripts/cutover_083_daily_gaps.py`, on the pattern of `cutover_174_calendar.py`: holds the timers, installs, reconciles, requires a clean dry re-check, restarts serve, verifies health and overview.
- [x] **1.4** **Commit**: `fix: shrink existing daily gap rows to the sessions that lack a bar`.

### 2. Release and production

- [x] **2.1** CHANGELOG `### Fixed`, release the patch per the release skill, run `uv run python scripts/cutover_083_daily_gaps.py vX.Y.Z`; the report lands in `project-documents/user/notes/`. Released 0.28.1 (d347aa55): CI green, GitHub release and PyPI up. Cutover PASSED: 15,941 rows became 77,962 (overlapping parent rows produced identical sub-ranges), a dry re-check found nothing left to change.
- [x] **2.2** Production check: a read-only dry reconcile reports nothing to change, and `compute_missing_ranges(conn, "SPY", "daily", 2021-12-29Z, 2022-01-05Z)` returns `[]`. Both hold on production 2026-11-21 (SPY returns `[]`).
- [x] **2.3** Comment the result on #83 and close it.
