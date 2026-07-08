---
docType: slice-design
slice: preferred-stock-registry-filter
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [156]
interfaces: [158]
dateCreated: 20260512
dateUpdated: 20260512
status: complete
---

# Slice Design: Preferred Stock Registry Filter

## Overview

Remove `Preferred Stock` from the v1 instrument universe. This involves
removing `PREFERRED_STOCK` from `EodhdType`, tightening the DB CHECK
constraint via an idempotent migration, deleting the ~1,913 existing
preferred rows from the instruments table, and re-running
`instruments rebuild` to produce the clean state that slice 158 depends on.

## Value

Preferred stocks are not tradable equities for the purposes of this
platform. Including them pollutes the active universe count, wastes bar
storage, and introduces noise into slice 158's delisted-filter logic.
Removing them reduces the active registry from ~14,848 to ~12,935 active
symbols and ensures every downstream consumer of `instruments` can assume
`eodhd_type` is one of `Common Stock | ETF | INDEX`.

## Technical Scope

**Included:**
- Remove `EodhdType.PREFERRED_STOCK` from the enum in `eodhd_classification.py`
- The `_ALLOWED_TYPES` frozenset is derived from `EodhdType`; no separate change required
- Add migration `040_drop_preferred_stock` that:
  - DROPs the existing `instruments_eodhd_type_check` constraint
  - DELETEs `instruments` rows where `eodhd_type = 'Preferred Stock'`
  - Re-ADDs the constraint using `_eodhd_type_check_sql()` (which no longer includes Preferred Stock)
  - Is idempotent: safe to re-run (constraint DROP is guarded by `IF EXISTS`; DELETE is safe when zero rows match)
- Update all tests that reference `PREFERRED_STOCK` or `"Preferred Stock"` in the universe layer

**Excluded:**
- Any change to `ohlcv_minute`, `ohlcv_daily`, or `data_gaps` tables — rows for deleted instruments are left to cascade (FK behavior) or be cleaned up by a future vacuum slice
- No re-fetch from EODHD within this slice's code; `instruments rebuild` is run post-migration as an operator step

## Dependencies

### Prerequisites
- Slice 156 (cold-start integrity) — migration runner and DB schema must be stable

### Interfaces Required
- `_eodhd_type_check_sql()` in `migrations/minute.py` — must be called at migration-apply time (after enum change), not at module-import time, so the rendered SQL reflects the updated enum
- Migration runner (`apply_migrations`) — standard track mechanism used by all prior migrations

## Architecture

### Component Structure

```
eodhd_classification.py          ← remove PREFERRED_STOCK from EodhdType
migrations/minute.py             ← add migration 040
```

`_eodhd_type_check_sql()` already derives its SQL from `EodhdType` at call
time. Removing the enum member automatically tightens the rendered constraint
— no separate change to the helper is needed.

### Data Flow

1. Developer removes `PREFERRED_STOCK` from `EodhdType`
2. Migration `040` is applied:
   a. DROP constraint `instruments_eodhd_type_check` IF EXISTS
   b. DELETE FROM instruments WHERE eodhd_type = 'Preferred Stock'
   c. ADD CONSTRAINT `instruments_eodhd_type_check` CHECK (eodhd_type IN ('Common Stock', 'ETF', 'INDEX'))
3. Operator runs `mt data instruments rebuild --skip-finnhub` to confirm clean state

## Technical Decisions

### Migration naming and numbering

The next available migration ID is `040`. The migration is named
`040_drop_preferred_stock` to match the `{nnn}_{description}` convention
used throughout `minute.py`. It is appended to the `TRACKS["minute"]` list
after `039_create_daemon_heartbeat`.

### Constraint re-render strategy

The CHECK constraint value is derived from `EodhdType` at the time
`_eodhd_type_check_sql()` is called inside the migration dict's `up` SQL
string. Since migration dicts in `minute.py` are constructed at module-import
time as a list of dicts with string `up` values, and `_eodhd_type_check_sql()`
is called when that list is built, removing the enum member before the module
is imported produces the correct constraint text. This matches the pattern
already used for migration 016.

### Cascade behavior for bar data

`ohlcv_minute` and `ohlcv_daily` reference `instruments` via `symbol` (not
a FK in the current schema — data is joined by symbol string). Deleting
preferred rows from `instruments` leaves any orphan bar rows in place; they
become unreachable through the registry but do not cause errors. A future
vacuum/cleanup slice can purge them if needed. This is acceptable for a 1/5
effort slice.

### Idempotency

- `DROP CONSTRAINT IF EXISTS` — safe if constraint is already gone
- `DELETE WHERE eodhd_type = 'Preferred Stock'` — safe when zero rows match
- `ADD CONSTRAINT ... CHECK (...)` — guarded by checking `pg_constraint` first (same pattern as migration 016's constraint block)

## Implementation Details

### Migration `040_drop_preferred_stock`

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

Note: The f-string interpolation of `_eodhd_type_check_sql()` happens at
module-import time, after `PREFERRED_STOCK` has been removed from the enum.
This matches the existing pattern for migration 016.

### EodhdType change

Remove `PREFERRED_STOCK = "Preferred Stock"` from the enum in
`eodhd_classification.py`. `_ALLOWED_TYPES` is derived via
`frozenset(t.value for t in EodhdType)` and requires no further change.

### Test updates

| File | Change |
|------|--------|
| `test/unit/universe/test_eodhd_classification.py` | Remove `PREFERRED_STOCK` from `test_all_four_values`; rename `test_all_four_types_pass_through` to `test_all_three_equity_types_pass_through` and drop the `Preferred Stock` row; remove preferred from `test_parametrized_kept_types` parametrize list (it is now filtered out); add `test_preferred_stock_filtered` asserting `filter_v1_universe([row("Preferred Stock")])` returns `[]` |
| `test/unit/universe/conftest.py` | Remove the 10-row Preferred Stock block and the `DL003` preferred delisted row |
| `test/integration/test_rebuild_orchestrator.py` | Remove `PFD1` fixture row and remove the assertion that preferred stock passes through |

## Integration Points

### Provides to Other Slices
- Slice 158 (`--universe` delisted filter) depends on this slice to ensure the
  instruments table contains only `Common Stock | ETF | INDEX` rows, so its
  active-universe count (~12,935) is stable and meaningful

### Consumes from Other Slices
- Slice 156 migration runner and schema must be applied before `040` can run

## Success Criteria

### Functional Requirements
- `EodhdType` has exactly three members: `COMMON_STOCK`, `ETF`, `INDEX`
- `filter_v1_universe` drops rows with `Type = 'Preferred Stock'`
- Migration `040` applied to prod trading DB removes ~1,913 rows and the CHECK constraint no longer permits `'Preferred Stock'`
- `mt data instruments rebuild --skip-finnhub` completes with zero preferred rows reported
- All 1,246+ existing tests continue to pass

### Technical Requirements
- No migration down SQL required (one-way data change consistent with prior migrations)
- Migration is idempotent: applying it twice leaves the DB in the same state
- `pyright` strict mode passes with no new errors

### Verification Walkthrough

**Step 1 — Confirm preferred count before migration (prod):**
```sql
SELECT COUNT(*) FROM instruments WHERE eodhd_type = 'Preferred Stock';
-- expect ~1,913
```

**Step 2 — Apply migration via CLI (which auto-applies pending migrations):**
```bash
mt data instruments rebuild --dry-run
# Should show 0 preferred rows in type_counts output
```

Or trigger via any CLI command that calls `apply_migrations` on the minute track.

**Step 3 — Confirm preferred rows deleted:**
```sql
SELECT COUNT(*) FROM instruments WHERE eodhd_type = 'Preferred Stock';
-- expect 0

SELECT COUNT(*) FROM instruments WHERE eodhd_type NOT IN ('Common Stock', 'ETF', 'INDEX');
-- expect 0

SELECT COUNT(*) FROM instruments;
-- expect ~55,000–57,000 (varies by EODHD snapshot)
```

**Step 4 — Confirm constraint is tightened:**
```sql
SELECT pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conname = 'instruments_eodhd_type_check';
-- expect: CHECK ((eodhd_type = ANY (ARRAY['Common Stock'::text, 'ETF'::text, 'INDEX'::text])))
-- 'Preferred Stock' must NOT appear
```

**Step 5 — Confirm insertion is rejected:**
```sql
INSERT INTO instruments (canonical_id, symbol, asset_class, venue, currency, eodhd_type, eodhd_exchange, delisted_at_eodhd)
VALUES ('TEST.US', 'TEST', 'equity', 'US', 'USD', 'Preferred Stock', 'US', false);
-- expect: ERROR: new row violates check constraint "instruments_eodhd_type_check"
```

**Step 6 — Run test suite:**
```bash
pytest test/unit/universe/ test/integration/ -v
# All tests pass
```

## Implementation Notes

### Development Approach
1. Remove `PREFERRED_STOCK` from `EodhdType` in `eodhd_classification.py`
2. Add migration `040_drop_preferred_stock` to `minute.py` TRACKS list
3. Update unit tests in `test_eodhd_classification.py` and `conftest.py`
4. Update integration test `test_rebuild_orchestrator.py`
5. Run full test suite — confirm pass
6. Apply migration against trading DB
7. Run `mt data instruments rebuild --skip-finnhub` to confirm clean state
