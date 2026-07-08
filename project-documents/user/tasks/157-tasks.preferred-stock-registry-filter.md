---
docType: tasks
slice: preferred-stock-registry-filter
project: trading
lld: user/slices/157-slice.preferred-stock-registry-filter.md
dependencies: [156]
projectState: >
  Slice 156 complete and merged to main. Cold-start integrity is solid;
  migration runner is stable. Prod trading DB on <db-host> contains
  ~1,913 preferred stock rows in `instruments`. The `EodhdType` enum has
  four members including `PREFERRED_STOCK`. Migration numbering is at 039.
  Branch: `157-slice.preferred-stock-registry-filter` (create from main).
dateCreated: 20260512
dateUpdated: 20260512
status: complete
---

## Context Summary

- Removes `Preferred Stock` from the v1 instrument universe entirely.
- Three coordinated changes: (1) remove `PREFERRED_STOCK` from `EodhdType`,
  (2) add migration `040_drop_preferred_stock` that tightens the DB CHECK
  constraint and deletes the ~1,913 existing rows, (3) update tests that
  reference preferred stock in the universe layer.
- `_ALLOWED_TYPES` and `_eodhd_type_check_sql()` are both derived from
  `EodhdType` at module-import time — removing the enum member propagates
  automatically; no separate fixups needed.
- Migration is idempotent: DROP constraint IF EXISTS → DELETE → re-ADD
  constraint guarded by `pg_constraint` existence check.
- Slice 158 (`--universe` delisted filter) depends on this slice.

---

## Tasks

- [x] **T01 — Branch setup**
  - [x] Confirm on `main` and clean: `git status`, `git branch --show-current`
  - [x] Create branch: `git checkout -b 157-slice.preferred-stock-registry-filter`
  - [x] Success: clean branch from current main

- [x] **T02 — Remove `PREFERRED_STOCK` from `EodhdType`**
  - [x] Edit `src/manta_trading/data/universe/eodhd_classification.py`
  - [x] Delete the line: `PREFERRED_STOCK = "Preferred Stock"`
  - [x] Leave the docstring, remaining three members, and all other code unchanged
  - [x] `_ALLOWED_TYPES` requires no edit — it is derived from the enum
  - [x] Success: `EodhdType` has exactly three members: `COMMON_STOCK`, `ETF`, `INDEX`

- [x] **T03 — Unit tests: `test_eodhd_classification.py`**
  - [x] File: `test/unit/universe/test_eodhd_classification.py`
  - [x] `TestEodhdType.test_all_four_values` → rename to
    `test_all_three_values` and remove the `PREFERRED_STOCK` assertion
  - [x] `test_all_four_types_pass_through` → rename to
    `test_all_three_equity_types_pass_through`; remove the `"Preferred Stock"`
    row from the input list; assert `len(result) == 3`
  - [x] `test_parametrized_kept_types`: the `@pytest.mark.parametrize("type_",
    list(EodhdType))` line now iterates three values — no explicit edit needed,
    but confirm the test still passes for all three
  - [x] Add new test `test_preferred_stock_filtered`: assert
    `filter_v1_universe([{"Code": "PS1", "Type": "Preferred Stock",
    "Exchange": "US", "_delisted": False}])` returns `[]`
  - [x] Run: `pytest test/unit/universe/test_eodhd_classification.py -v`
  - [x] Success: all tests pass, no reference to `PREFERRED_STOCK` remains

- [x] **T04 — Unit test fixture: `conftest.py`**
  - [x] File: `test/unit/universe/conftest.py`
  - [x] Remove the 10-row Preferred Stock block (`PS00`–`PS09`) from the
    active rows fixture
  - [x] Remove the `DL003` preferred delisted row from the delisted rows fixture
  - [x] Run: `pytest test/unit/universe/ -v`
  - [x] Success: all universe unit tests pass

- [x] **T05 — Integration test: `test_rebuild_orchestrator.py`**
  - [x] File: `test/integration/test_rebuild_orchestrator.py`
  - [x] Remove the `_equity("PFD1", "Preferred Stock")` fixture row
  - [x] Remove any assertion that a preferred stock row passes through the
    rebuild pipeline
  - [x] Update any count assertions that assumed preferred stock was included
  - [x] Run: `pytest test/integration/test_rebuild_orchestrator.py -v`
  - [x] Success: all integration tests pass

- [x] **T06 — Add migration `040_drop_preferred_stock`**
  - [x] File: `src/manta_trading/market/schema/migrations/minute.py`
  - [x] Append the new migration dict to `TRACKS["minute"]` after the entry
    with `"id": "039_create_daemon_heartbeat"`
  - [x] Migration structure:
    ```python
    {
        "id": "040_drop_preferred_stock",
        "description": (
            "Remove Preferred Stock from instruments: drop CHECK constraint, "
            "delete preferred rows, re-add tightened CHECK derived from EodhdType."
        ),
        "up": f"""
            ALTER TABLE instruments
                DROP CONSTRAINT IF EXISTS instruments_eodhd_type_check;

            DELETE FROM instruments
            WHERE eodhd_type = 'Preferred Stock';

            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'instruments_eodhd_type_check'
                ) THEN
                    ALTER TABLE instruments
                        ADD CONSTRAINT instruments_eodhd_type_check
                        CHECK ({_eodhd_type_check_sql()});
                END IF;
            END $$;
        """,
        "down": "",
    },
    ```
  - [x] The f-string interpolation of `_eodhd_type_check_sql()` happens at
    module-import time — after T02 removes `PREFERRED_STOCK`, the rendered SQL
    will contain only `'Common Stock', 'ETF', 'INDEX'`
  - [x] Success: migration dict present in list with correct id and structure

- [x] **T07 — Full unit test run (1246 passed)**
  - [x] Run: `pytest test/unit/ -v`
  - [x] Success: all unit tests pass with no new failures

- [x] **T08 — Apply migration to `trading_test`**
  - [x] Trigger via any command that runs `apply_migrations` on the minute
    track against `trading_test`, e.g.:
    `MT_TIMESCALE_DB_URL=<trading_test_url> mt data migrate apply`
  - [x] Verify preferred rows deleted:
    ```sql
    SELECT COUNT(*) FROM instruments WHERE eodhd_type = 'Preferred Stock';
    -- expect 0
    ```
  - [x] Verify constraint tightened (must not list 'Preferred Stock'):
    ```sql
    SELECT pg_get_constraintdef(oid)
    FROM pg_constraint
    WHERE conname = 'instruments_eodhd_type_check';
    ```
  - [x] Verify insert is rejected:
    ```sql
    INSERT INTO instruments (canonical_id, symbol, asset_class, venue,
      currency, eodhd_type, eodhd_exchange, delisted_at_eodhd)
    VALUES ('TEST.US','TEST','equity','US','USD','Preferred Stock','US',false);
    -- expect: ERROR violates check constraint
    ```
  - [x] Verify migration is idempotent:
    `MT_TIMESCALE_DB_URL=<trading_test_url> mt data migrate apply`
    (second run) — no error, no duplicate work
  - [x] Success: all four SQL checks above confirm expected state

- [x] **T09 — Apply migration to prod DB (<db-host>)**
  - [x] Run `apply_migrations` against prod:
    `MT_TIMESCALE_DB_URL=<prod_url> mt data migrate apply`
  - [x] Confirm preferred count before (run SQL against prod first if desired):
    `SELECT COUNT(*) FROM instruments WHERE eodhd_type = 'Preferred Stock';`
    (expect ~1,913)
  - [x] After migration, re-run count — expect 0
  - [x] Run `mt data instruments rebuild --skip-finnhub` against prod to
    confirm clean registry state (type_counts output shows no Preferred Stock)
  - [x] Success: prod instruments table contains no preferred rows; CHECK
    constraint matches `trading_test`

- [x] **T10 — Full integration test run**
  - [x] Run: `pytest test/integration/ -v`
  - [x] Success: all integration tests pass

- [x] **T11 — pyright type check (zero new errors)**
  - [x] Run: `pyright src/ test/`
  - [x] Success: zero new errors introduced by this slice

- [x] **T12 — Commit**
  - [x] Stage: `eodhd_classification.py`, `migrations/minute.py`, all
    modified test files
  - [x] Commit message: `feat: remove Preferred Stock from v1 instrument universe`
  - [x] Success: clean commit on `157-slice.preferred-stock-registry-filter`

- [x] **T13 — Update task file and slice plan**
  - [x] Mark this task file `status: complete`
  - [x] Mark slice plan entry 17 in
    `140-slices.data-quality-operations.md` as `[x]`
  - [x] Success: both documents reflect completed state
