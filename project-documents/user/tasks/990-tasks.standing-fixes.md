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

- [ ] **1.1** One function returns a symbol's EODHD ticker: the symbol itself when it has an exchange suffix, else `{symbol}.US`. `daemon/daily.py::_normalise`, `historical_minute/providers/eodhd.py::_normalise_symbol` and `universe/populate_delisted_dates.py::_normalise_symbol` call it instead of their own copies.
  - [ ] Unit test: `AAPL` → `AAPL.US`, `VIX.INDX` and `^TNX.US` unchanged.
- [ ] **1.2** `universe/rebuild.py::build_upsert_rows`: an `INDX` row gets `symbol = canonical_id` (`{code}.INDX`) and the same trading calendar as equities. The calendar id is defined once, not as a second `"NYSE"` literal.
  - [ ] Unit test: an equity `DJT` and an index `DJT` in one payload give two rows with symbols `DJT` and `DJT.INDX`. The index row has the calendar, and the equity row is unchanged.
  - [ ] Unit test: a rebuild over an existing `VIX.INDX` row keeps it (match by symbol still finds it) and creates no bare `VIX` row.
- [ ] **1.3** **Commit**: ruff and format on the touched files, mypy, unit tier, then `fix: give index instruments their own .INDX symbol and a calendar`.

### 2. Move production's index rows

- [ ] **2.1** List every table keyed by symbol text that holds rows for a bare index symbol, read-only on production. At least `acquisition_state` and `data_gaps`; check the rest. Record the list and counts in this group.
- [ ] **2.2** Migration (next free id on the minute track): for `asset_class='index'` rows with `eodhd_exchange='INDX'`:
  - set `symbol = canonical_id` and the equity trading calendar;
  - set `first_data_date` and `delisted_date` to NULL where the bare symbol is also an equity's (the 24 collisions), since those dates came from the equity;
  - in the symbol-keyed tables from 2.1, rename bare-symbol rows to `{code}.INDX` **only for the ~880 symbols no equity shares**. Rows under a shared symbol belong to the equity and stay as they are.
  - It must be idempotent (re-running changes nothing) and delete no bar rows.
- [ ] **2.3** Integration test on a throwaway database seeded with an equity `DJT` that has bars, an index `DJT`, an index `VIX` with an `acquisition_state` row, and `^TNX`:
  - [ ] after the migration, `DJT.INDX` exists with no `first_data_date`, the equity `DJT` and its bars and state are unchanged, `VIX` became `VIX.INDX` with its state row, and `^TNX` is unchanged;
  - [ ] a second run changes nothing.
- [ ] **2.4** Cutover script on the pattern of `scripts/cutover_0244_symbol_names.py`: check-then-act, holds and releases the timers, applies the migration, and verifies against production. 904 `.INDX` symbols; zero index rows without a calendar; `/api/v1/symbols/DJT` still the equity with its bars.
- [ ] **2.5** **Commit**: `fix: move index instruments to their .INDX symbols in production`.

### 3. Check index data before the passes pick it up

- [ ] **3.1** Measure on EODHD how far back index data goes, for 1m (VIX.INDX, GSPC.INDX, a thin index) and for daily. Record the depth and the quota cost of a full backfill of the 904 against the ~100k/day quota (memory: normal passes already use most of it).
- [ ] **3.2** Confirm the minute pass's symbol selection includes the index rows after the migration, and see what minute accounting and `mt data health` will report for them. A health check must not turn red just because 904 symbols start with no minutes.
- [ ] **3.3** Run the off-tape detector over one backfilled week of VIX.INDX and GSPC.INDX minutes on a throwaway database. VIX minutes trade a few contracts each, so check the marked share is sane before production runs the detector on indices. If it marks much of a normal day, stop and file what you saw on #46.
- [ ] **3.4** Write the outcome of 3.1–3.3 here. If 3.1 shows the backfill can't fit the quota alongside normal passes, add the throttling needed. Cut cost, never scope: the whole index set stays the target.
- [ ] **3.5** **Commit** (if 3.2–3.4 changed code): `fix: count index instruments correctly in minute accounting and health`.

### 4. Docs, release and production

- [ ] **4.1** `docs/api/reference.md` and `docs/api/agents.md`: index symbols are `{code}.INDX` (e.g. `/api/v1/bars/VIX.INDX`), indices have no volume worth reading, and split/dividend adjustment doesn't apply to them. `scripts/check_api_docs.py` passes.
- [ ] **4.2** CHANGELOG `### Fixed` entry: indices now have their own symbols and data, and a client asks for `VIX.INDX`. Mention that `DJT` and the other shared symbols were always the equity.
- [ ] **4.3** **Commit**: `docs: document .INDX index symbols`.
- [ ] **4.4** Release (patch) and run the cutover from 2.4 per the release skill.
- [ ] **4.5** Production check:
  - [ ] run the daily pass for `VIX.INDX` and `GSPC.INDX` only (`--list` with those two);
  - [ ] `curl -s 'http://127.0.0.1:8100/api/v1/bars/VIX.INDX?granularity=1d&limit=5'` returns 5 bars, and the same for `GSPC.INDX`;
  - [ ] `/api/v1/bars/DJT?granularity=1d&limit=5` returns the same bars as before the release.
- [ ] **4.6** Comment the results on #46 and close #46 and #4. Tell trading-engine and mt-ui that indices are served as `{code}.INDX`.
