---
docType: tasks
slice: 143-compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable
project: trading
lld: user/slices/143-slice.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md
dependencies: [142-slice.schema-migration-and-cold-start]
projectState: >
  Slice 142 complete and committed (local, unpushed). Dev DB post-cold-start:
  migrations 018–022 applied, data_gaps empty, acquisition_state empty,
  minute_ohlcv empty, daily_ohlcv absent, data_status view installed
  (without-daily branch, ~65k rows all STALE). Branch:
  143-slice.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.
dateCreated: 20260501
dateUpdated: 20260501
status: complete
---

## Context Summary

- Slice 143 has two deliverables: (1) promote `k_factor` in
  `adjustment/k_factor.py` to a SSOT `compute_k_factor` with stable
  `CaSnapshot` + `compute_snapshot_id`; (2) create the `daily_ohlcv`
  hypertable via migrations 023/024.
- `Split` and `Dividend` dataclasses are **not** modified — no `fetched_at`
  field needed (D4: excluded from canonicalization because ingest always
  bumps it).
- `AdjustmentContext` → `CaSnapshot`; `load_adjustment_context` →
  `current_ca_snapshot`. Old names re-exported as deprecated aliases;
  removed in slice 144.
- Call sites to migrate: `adjustment/verify.py`, `adjustment/verify_eod.py`,
  `acquisition/minute/writer.py`.
- Zero-diff guard test (T5) must pass before any call-site edits begin.
- Next slice: 144 — daemon refactor + `trading_sessions` table.

---

## Tasks

- [x] **T1. Add migrations 023 (`daily_ohlcv`) and 024 (`data_status_view_refresh`)**
  - [x] In `src/manta_trading/market/schema/migrations/minute.py`, append
    two entries to `MINUTE_MIGRATIONS` after migration 022:
    - `023_daily_ohlcv`: `CREATE TABLE IF NOT EXISTS daily_ohlcv` with the
      exact column shape from LLD D6 (time, symbol, OHLCV, adj_*, k_factor,
      adjusted_at, created_at); call `create_hypertable('daily_ohlcv',
      'time', chunk_time_interval => INTERVAL '7 days', if_not_exists =>
      TRUE)`; create `ux_daily_ohlcv_symbol_time` (UNIQUE),
      `ix_daily_ohlcv_symbol_time`, and `ix_daily_ohlcv_time_symbol` indexes.
    - `024_data_status_view_refresh`: same DO-$$ body as migration 021
      (import `_DATA_STATUS_VIEW_WITH_DAILY` / `_DATA_STATUS_VIEW_WITHOUT_DAILY`
      from the same module — do not duplicate the SQL string).
  - [x] Both migrations are idempotent (`IF NOT EXISTS`, `CREATE OR REPLACE`).
  - [x] `pyright --strict src/manta_trading/market/schema/migrations/minute.py`
    reports zero errors.

- [x] **T2. Test: migrations 023 and 024**
  - [x] In the existing migration unit/integration test file (locate with
    `grep -rl "MINUTE_MIGRATIONS\|023\|data_status" tests/`), add:
    - Assert migration 023 entry has `id == "023_daily_ohlcv"` and its SQL
      contains `create_hypertable` and `ux_daily_ohlcv_symbol_time`.
    - Assert migration 024 entry has `id == "024_data_status_view_refresh"`
      and its SQL body is non-empty (does not duplicate the view string
      literally — references the pre-rendered constant).
  - [x] If an integration test exists that applies all migrations to a real
    DB, extend it to verify: after 023, `daily_ohlcv` is a hypertable with
    `chunk_time_interval = '7 days'`; after 024, `EXPLAIN SELECT * FROM
    data_status` references `daily_ohlcv` in the plan.
  - [x] `pytest` on the relevant test file passes.
  - [x] **Commit checkpoint:** `feat(143): add migrations 023/024 (daily_ohlcv hypertable + view refresh)`

- [x] **T3. Add `CaSnapshot` dataclass to `adjustment/k_factor.py`**
  - [x] Add `CaSnapshot` as a `@dataclass(frozen=True)` with fields:
    `symbol: str`, `splits: tuple[Split, ...]`, `dividends: tuple[Dividend, ...]`,
    `prev_closes: dict[date, Decimal]`, `snapshot_id: str`.
  - [x] Add docstring noting: not hashable (`dict` field); `frozen=True`
    prevents field reassignment; `snapshot_id` computed at construction time.
  - [x] `pyright --strict` on the file reports zero errors.

- [x] **T4. Add `compute_snapshot_id` to `adjustment/k_factor.py`**
  - [x] Implement `compute_snapshot_id(splits, dividends) -> str` per LLD D4
    (canonicalized JSON + SHA256 hex; keyed on `ex_date` + ratio/amount only;
    no `fetched_at`).
  - [x] Import `hashlib`, `json` at top of file (standard library only).
  - [x] `pyright --strict` on the file reports zero errors.

- [x] **T5. Test: `CaSnapshot` and `compute_snapshot_id`** ← gate for T6
  - [x] In `tests/unit/data/adjustment/test_k_factor.py` (or a new sibling
    `test_snapshot.py`), add:
    - `test_casnapshot_frozen`: assigning to any field raises `FrozenInstanceError`.
    - `test_casnapshot_not_hashable`: `hash(snapshot)` raises `TypeError`.
    - `test_compute_snapshot_id_ordering_invariant`: same splits/dividends in
      different iteration order produces identical digest.
    - `test_compute_snapshot_id_stable_across_processes`: use `subprocess.run`
      to invoke a small Python one-liner that imports and calls
      `compute_snapshot_id` with a fixed fixture; assert the printed hex
      matches the in-process result.
    - `test_compute_snapshot_id_ignores_fetched_at`: construct two `Split`
      lists identical except one has a synthesized `fetched_at` attribute
      monkey-patched on; assert digests are equal (confirms `fetched_at` is
      not read). Only needed if `Split` gains `fetched_at` in the future —
      document as a canary test.
  - [x] `pytest tests/unit/data/adjustment/` passes with zero failures.

- [x] **T6. Rename `k_factor` → `compute_k_factor`; add `ca_snapshot` overload**
  - [x] In `adjustment/k_factor.py`:
    - Rename the function `k_factor` → `compute_k_factor`.
    - Add a `ca_snapshot: CaSnapshot | None = None` keyword-only parameter.
    - When `ca_snapshot` is provided, extract `splits`, `dividends`,
      `prev_closes` from it and delegate to the existing positional logic.
    - When `ca_snapshot` is `None`, require all three positional arguments
      (existing call signature unchanged for backward compat).
    - The internal math is not modified — only the entry point and dispatch.
  - [x] `pyright --strict` on the file reports zero errors.
  - [x] **Do not edit any call sites yet** — that is T8/T9/T10.

- [x] **T7. Test: `compute_k_factor` rename and overload** ← gate for T8–T10
  - [x] Update all existing `k_factor(...)` import references in unit tests to
    `compute_k_factor`.
  - [x] Add a test asserting that `compute_k_factor(sym, d, ca_snapshot=snap)`
    returns the same `Decimal` as `compute_k_factor(sym, d, splits, divs,
    prev_closes)` for the same inputs (round-trip equivalence).
  - [x] `pytest tests/unit/data/adjustment/` passes with zero failures.
  - [x] **Commit checkpoint:** `feat(143): add CaSnapshot, compute_snapshot_id, rename k_factor`

- [x] **T8. Zero-diff guard test for `writer._attach_adjustment_columns`**
  - [x] Before changing `writer.py`, add a test in
    `tests/unit/data/acquisition/minute/` that:
    1. Constructs a synthetic `AdjustmentContext` (existing name) with known
       splits and dividends.
    2. Calls `_attach_adjustment_columns` (or the writer path that invokes it)
       with a fixed DataFrame of minute bars covering multiple trading dates.
    3. Captures the `adj_open`, `adj_close`, `k_factor` column values per date.
    4. Asserts specific `Decimal`-rounded values (pin the output).
  - [x] Test passes against the **current** (pre-refactor) code.
  - [x] This test must remain passing after T10 — that is the zero-diff proof.

- [x] **T9. Rename `AdjustmentContext` → `CaSnapshot` in `context.py`**
  - [x] In `src/manta_trading/data/adjustment/context.py`:
    - Rename class `AdjustmentContext` → `CaSnapshot` (the dataclass defined
      in `k_factor.py` is the canonical type; `context.py` now constructs and
      returns it rather than defining its own class).
    - Rename `load_adjustment_context` → `current_ca_snapshot`.
    - Signature: `current_ca_snapshot(symbol: str, *, settings: Settings) -> CaSnapshot`.
    - Extend the `splits` SELECT to produce the same fields as before (no
      `fetched_at` needed).
    - After loading splits/dividends/prev_closes, call
      `compute_snapshot_id(splits, dividends)` and pass the result as
      `snapshot_id` when constructing the returned `CaSnapshot`.
    - Keep the missing-`prev_close` WARNING log (matching current behaviour).
  - [x] `pyright --strict` on the file reports zero errors.

- [x] **T10. Update call sites to `compute_k_factor` + `CaSnapshot`**
  - [x] `src/manta_trading/data/adjustment/verify.py`: replace
    `k_factor(sym, d, ctx.splits, ctx.dividends, ctx.prev_closes)` with
    `compute_k_factor(sym, d, ca_snapshot=ctx)`.
  - [x] `src/manta_trading/data/adjustment/verify_eod.py`: same pattern.
  - [x] `src/manta_trading/data/acquisition/minute/writer.py`
    (`_attach_adjustment_columns`): replace the `k_factor(...)` call with
    `compute_k_factor(ctx.symbol, d, ca_snapshot=ctx)`.
  - [x] Verify with `grep -rn "k_factor(" src/` — only hits should be:
    the function definition line in `k_factor.py` and the alias in
    `__init__.py` (added in T11).
  - [x] `pyright --strict src/` reports zero errors.

- [x] **T11. Update `adjustment/__init__.py` exports**
  - [x] Export: `CaSnapshot`, `compute_k_factor`, `compute_snapshot_id`,
    `current_ca_snapshot`.
  - [x] Re-export deprecated aliases with inline comment
    `# deprecated — removed in slice 144`:
    - `k_factor = compute_k_factor`
    - `AdjustmentContext = CaSnapshot`
    - `load_adjustment_context = current_ca_snapshot`
  - [x] `pyright --strict` on the file reports zero errors.

- [x] **T12. Test: call-site migration and zero-diff guard**
  - [x] Run the guard test added in T8 — it must still pass unchanged
    (proves numeric output is identical after the refactor).
  - [x] Run `pytest tests/unit/data/adjustment/` — all tests pass.
  - [x] Run `pytest tests/unit/data/acquisition/minute/` — all tests pass.
  - [x] **Commit checkpoint:** `refactor(143): migrate call sites to compute_k_factor + CaSnapshot`

- [x] **T13. Integration test: `current_ca_snapshot`**
  - [x] Create `tests/integration/data/adjustment/test_current_ca_snapshot.py`.
  - [x] Test skips cleanly when `MT_MARKET_DB_URL` is not set
    (`pytest.importorskip` or a `skipif` fixture).
  - [x] When the env var is present: call `current_ca_snapshot('AAPL',
    settings=Settings())`, assert the returned `CaSnapshot` has:
    - `symbol == 'AAPL'`
    - `len(splits) >= 0` (no assertion on count — just that it's a tuple)
    - `snapshot_id` is a 64-character hex string
    - `prev_closes` is a dict (possibly empty if no dividends)
  - [x] `pytest tests/integration/data/adjustment/test_current_ca_snapshot.py -v`
    passes (or skips cleanly).

- [x] **T14. Integration test: `compute_k_factor` EODHD parity (AAPL, MSFT, GOOGL)**
  - [x] Create `tests/integration/data/adjustment/test_eodhd_parity.py`.
  - [x] Skips when `MT_MARKET_DB_URL` or `MT_EODHD_API_KEY` not set.
  - [x] For each of AAPL, MSFT, GOOGL over a fixed 30-day window in the
    slice 128 dry-run sample period:
    1. Fetch `/eod` from EODHD for the window.
    2. Compute `published_k = adjusted_close / close` per session.
    3. Call `compute_k_factor(sym, d, ca_snapshot=current_ca_snapshot(sym, ...))`
       per session.
    4. Assert `abs(stored_k - published_k) < ADJUSTMENT_DRIFT_EPSILON` for
       every session.
  - [x] MSFT result is the issue #10 regression check — must pass.
  - [x] `pytest tests/integration/data/adjustment/test_eodhd_parity.py -v`
    passes.

- [x] **T15. Integration test: issue #10 reproduction + resolution**
  - [x] Create `tests/integration/data/adjustment/test_issue_10_msft.py`.
  - [x] Skips when `MT_MARKET_DB_URL` or `MT_EODHD_API_KEY` not set.
  - [x] Step 1: load `current_ca_snapshot('MSFT', ...)`, then construct a
    stale snapshot by removing the most recent dividend from the tuple.
    Assert `abs(compute_k_factor('MSFT', d, ca_snapshot=stale) -
    published_k) > ADJUSTMENT_DRIFT_EPSILON` for a known drifting session.
  - [x] Step 2: using the full `current_ca_snapshot`, assert the same
    session's drift is `< ADJUSTMENT_DRIFT_EPSILON`.
  - [x] `pytest tests/integration/data/adjustment/test_issue_10_msft.py -v`
    passes.
  - [x] **Commit checkpoint:** `test(143): add integration tests (EODHD parity, issue-10 regression)`

- [x] **T16. Final validation pass**
  - [x] `pytest tests/unit/ -q` — zero failures (≥ 1162 baseline tests pass).
  - [x] `pyright --strict src/` — zero errors.
  - [x] `grep -rn "from manta_trading.data.adjustment import.*\bk_factor\b" src/`
    returns only the alias line in `__init__.py` (SC2).
  - [x] Apply migrations to dev DB: `mt data migrate-cold-start --skip-probe --yes`
    — expect migrations 023 and 024 applied.
  - [x] Verify hypertable: `psql $MT_TIMESCALE_URL -c "\d daily_ohlcv"` shows
    the expected column list; `chunk_time_interval = '7 days'`.
  - [x] Latency NFR: `psql $MT_TIMESCALE_URL -c "\timing" -c "SELECT COUNT(*)
    FROM data_status;"` returns in under 1 second (SC12).
  - [x] `EXPLAIN SELECT * FROM data_status WHERE symbol = 'AAPL'` plan
    references both `daily_ohlcv` and `minute_ohlcv` (SC8).
  - [x] **Commit checkpoint:** `feat(143): complete — compute_k_factor SSOT + daily_ohlcv hypertable`
