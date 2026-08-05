---
docType: incident-note
project: trading-data
dateCreated: 20260804
dateUpdated: 20260804
status: restore-executed — see "Restore execution record" at bottom
severity: high
---

# Incident: production metadata truncated by an integration-test fixture

**Date:** 2026-08-04
**Database:** `trading` on 192.168.1.144 (production)
**Status:** cause identified; restore tooling written but **NOT yet run**
**Caused by:** Claude (AI assistant) running `pytest test/integration` with
`MT_TIMESCALE_DB_URL` loaded from `.env`

---

## TL;DR for whoever picks this up

1. **No bar data was lost.** `minute_ohlcv` = 4,415,312,550 rows,
   `daily_ohlcv` = 34,659,757. Verified post-incident. The 85 GB database is
   intact.
2. Six metadata tables were truncated and four migrations were deleted from
   the ledger, which took three minute caggs + `minute_coverage` with them.
3. Everything lost is **re-derivable** from EODHD or from the bars.
4. Restore tooling exists at `mt data restore assess` / `mt data restore run`
   (new, committed, **not yet executed**).
5. **The root-cause fix is NOT settled** — see "Open question" below. Do not
   assume the one-line URL change is correct.

---

## What happened

While closing out slice 187, I ran the integration test tier:

```
uv run python <scratchpad>/runtests.py test/integration -q
```

That runner loads `.env` with `dotenv` and injects every variable into the
child environment — including `MT_TIMESCALE_DB_URL`, which points at
production `trading`.

`test/integration/conftest.py` line 14 read:

```python
TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")
```

and its `instruments_clean_db` fixture then ran, against that URL:

```sql
TRUNCATE TABLE provider_symbol_mapping, instruments RESTART IDENTITY CASCADE;
ALTER TABLE instruments DROP CONSTRAINT IF EXISTS instruments_eodhd_type_check;
ALTER TABLE instruments DROP CONSTRAINT IF EXISTS instruments_eodhd_exchange_check;
ALTER TABLE instruments ALTER COLUMN eodhd_type DROP NOT NULL;
ALTER TABLE instruments ALTER COLUMN eodhd_exchange DROP NOT NULL;
ALTER TABLE instruments ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE;
DELETE FROM schema_migrations WHERE migration_id IN
  ('015_instruments_lifecycle_columns',
   '016_instruments_eodhd_type_not_null',
   '017_instruments_drop_active');
```

The fixture runs `_reset()` **before and after** each test, so it fired more
than once.

**Timeline note:** the PM reported the DB was fine the previous day and only
daily daemon pulls had been running. The OOM crash (104 GB peak on a 128 GB
box) happened around the same window and is plausibly the *same* event — a
`TRUNCATE ... CASCADE` over a hypertable with thousands of chunks, under
`work_mem=512MB` × `max_parallel_workers_per_gather=16`, is a credible way to
reach 104 GB. Not proven; `track_commit_timestamp` is `off` so exact commit
times are unavailable.

---

## How it was diagnosed (evidence, so it can be re-checked)

`relfilenode` grouping was the decisive evidence. A relfilenode changes only on
`TRUNCATE`, `VACUUM FULL`, `CLUSTER`, or drop/recreate:

| table | relfilenode | verdict |
| --- | --- | --- |
| `schema_migrations` | 721,008 | original |
| `minute_ohlcv` | 721,785 | original |
| `trading_calendars` | 721,829 | original |
| `trading_holidays` | 721,838 | original |
| `backfill_state` | 721,875 | original |
| `acquisition_state` | 721,899 | original |
| `daily_ohlcv` | 721,913 | original |
| `daemon_heartbeat` | 724,118 | original |
| `universe_members` | 1,427,728 | original (but 0 rows — separate/earlier) |
| `provider_symbol_mapping` | **315,522,039** | REWRITTEN |
| `instruments` | **315,522,043** | REWRITTEN |
| `data_gaps` | **315,522,191** | REWRITTEN |
| `trading_sessions` | **315,522,310** | REWRITTEN (since repopulated, 4,560) |
| `splits` | **315,522,321** | REWRITTEN |
| `dividends` | **315,522,330** | REWRITTEN |

One contiguous ~315.52M block = one operation. `instruments` shows
`n_tup_ins=0, n_tup_del=0`, 40 kB, `reltuples=-1` (never analyzed since) — the
profile of a truncated-and-never-refilled table, not a crash artifact. An OOM
cannot drop objects or change relfilenodes.

Only `instruments` and `provider_symbol_mapping` are named in the TRUNCATE; the
other four were pulled in by `CASCADE` via FKs to `instruments`.

**Ruled out along the way:**
- Migration replay — newest ledger entry is `049`, applied 2026-07-26. Nothing
  ran on 08-04.
- `migrate_cold_start.py` — truncates `minute_ohlcv`/`daily_ohlcv`/
  `acquisition_state`, i.e. exactly the tables that *survived*.
- Slice 187 code — read-only; its load tier writes only to UUID-named
  ephemeral DBs via `create_app(db_url=...)`.
- A recreated database — 85 GB with all compressed chunks intact, same
  `schema_migrations` history back to 2026-05.

---

## Current damage (verified 2026-08-04, `mt data restore assess`)

```
Preserved (must be non-zero):
  minute_ohlcv                4,415,312,550
  daily_ohlcv                    34,659,757
  acquisition_state                  24,923
  schema_migrations                      48
Truncated by the incident:
  instruments                             0   <-- empty
  provider_symbol_mapping                 0   <-- empty
  data_gaps                               0   <-- empty
  trading_sessions                    4,560   (repopulated by a calendar extend)
  splits                                  0   <-- empty
  dividends                               0   <-- empty
Migrations absent from the ledger:
  033_create_minute_caggs
  034_create_daily_caggs
  035_cagg_refresh_policies
  036_copy_splits_dividends_from_marketdb
Continuous aggregates absent:
  minute_15min_ohlcv
  minute_1hour_ohlcv
  minute_4hour_ohlcv
  minute_coverage
```

Surviving caggs: `minute_5min_ohlcv`, `daily_coverage`.

`universe_members` is 0 rows but retains its **original** relfilenode — emptied
by a plain `DELETE` at some *other* time, by something else. ~~Unexplained~~
**RESOLVED (2026-08-04, same day):** unit-tier fixtures
(`test/unit/universe/test_tracking.py`, `data/test_equity_universe.py`,
`cli/commands/test_data_universes.py`) run `DELETE FROM universe_members WHERE
universe_name = 'sp500'` against `MT_TIMESCALE_DB_URL`. Prod held only sp500
rows, so the scoped DELETE emptied the table without changing its relfilenode —
during the same session's `pytest test/unit` run, which reported 1855 green.
All three fixtures now run on ephemeral databases; see the guardrails scoping
note.

---

## What was built to fix it (written, committed, NOT run)

**`src/manta_trading/data/quality/restore_metadata.py`** — new module.
- `assess(conn)` — read-only damage survey.
- `restore(pool, dry_run=)` — replays absent migrations via the ordinary
  migration runner. **Never deletes.** Every step is an upsert or an
  `IF NOT EXISTS` DDL replay, so it is safe to re-run and to interrupt.
- `RestoreRefused` — raised if the preserved tables are empty, i.e. the URL
  points somewhere other than the damaged DB. Deliberately fatal: seeding prod
  reference data into the wrong database is the exact failure being repaired.
- Takes a `pool` from the caller; **never reads the URL from the environment.**

**CLI:** `mt data restore assess` and `mt data restore run [--dry-run]`
(added to `src/manta_trading/cli/commands/data.py`, new `restore_app`).

### Restore sequence (none of this has been executed yet)

```bash
mt data restore assess            # read-only, confirm damage
mt data restore run --dry-run     # confirm plan
mt data restore run               # replays migrations 033-036 -> recreates caggs
mt data instruments rebuild       # repopulates instruments (idempotent upsert
                                  #   on canonical_id; has --dry-run)
mt data ca update                 # splits + dividends
mt data universes refresh         # index membership
```

`data_gaps` needs no step — the daemon reseeds it on its next cycle.

**Caution:** recreating `minute_4hour_ohlcv` etc. triggers materialization over
4.4 B rows. That is expensive and, given `work_mem=512MB` × 16 parallel
workers, is plausibly what OOM'd the box. **Consider lowering `work_mem` and
`max_parallel_workers_per_gather` before running the cagg step.**

---

## Open question — the root-cause fix is NOT settled

I changed `test/integration/conftest.py` line 14 from `MT_TIMESCALE_DB_URL` to
`MT_TIMESCALE_TEST_URL` (uncommitted at time of writing). **The PM stopped me,
correctly, saying this may not be the right fix.** Reasons to doubt it:

- `MT_TIMESCALE_TEST_URL` points at `postgres` (the admin DB used to
  create/drop throwaway databases), not at a database with this schema. The
  fixture would truncate tables that don't exist there.
- The fixture is **destructive by design** — it truncates and rolls back schema
  to a pre-141 state. That belongs on an `ephemeral_db`, not on any shared
  database.
- Other integration tests may have the same pattern; I only grepped for
  `TRUNCATE` of the six affected tables. **A full audit of `test/integration/`
  for prod-URL reads has not been done.**

The PM has stated the intent to "go farther than just fixing root cause."
Treat the URL change as a **stopgap flag, not a solution.**

### Hardening candidates (PM said scope this properly)

1. `test_integration_tier_never_references_prod_db_url` — mirror of the load
   tier's `test_load_tier_never_references_prod_db_url`, which has existed
   since slice 167 and is exactly why the load tier was safe. The integration
   tier had no equivalent. **This is the single highest-value guard.**
2. Rewrite `instruments_clean_db` onto `ephemeral_db` so it cannot target a
   shared DB at all.
3. A defense-in-depth guard: refuse any destructive DDL/DML in tests when the
   target DB name is `trading` (or matches a configured prod list).
4. Revoke DDL/TRUNCATE rights from the app role on prod; give tests a role that
   cannot truncate.
5. **Backups.** A backup exists but is "not as current or well maintained as I
   would like" (PM, 2026-08-04); improving it is a stated priority. Caveats so
   it is prioritised for the right reason: a backup would **not have prevented**
   this, and restore-from-backup was never the recovery path — the bars survived
   and everything lost is re-derivable from EODHD or from the bars. What a
   current backup buys is (a) cheaper recovery of `instruments`/`splits`/
   `dividends` than re-syncing, and (b) a known floor if a future truncation
   reaches something **not** re-derivable. **Action: enumerate which tables
   cannot be rebuilt from providers or raw bars, and let that list drive backup
   currency** — not this incident's blast radius, which happened to be benign.
6. Server sizing: `work_mem=512MB`, `max_parallel_workers_per_gather=16`,
   `shared_buffers=32GB` on 128 GB. `work_mem` is per *node* per *worker*, so
   one query can claim tens of GB. The API pool clamps itself to 64 MB
   (186 D1); CLI/daemon sessions inherit the 512 MB default.

---

## Slice 187 status (unrelated to the incident, don't lose it)

Branch `187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier`,
12 commits, **unmerged**. Implementation complete and verified against prod
*before* the incident: endpoint 2.7–4.0 s → 16–35 ms, 28/28 symbol
equivalence, load tier 13/13 green. Code review addressed (6 findings fixed,
2 rejected with evidence) in `187-review.code.*.md`.

**Outstanding for 187:**
- Integration + load tiers need a re-run once prod metadata is restored. **Do
  not run them until the fixture problem is resolved** — that is what caused
  this.
- `test/unit` was last green at 1855 passed / 0 failed.
- Pre-existing, unrelated: `test/unit/data/test_locking.py` and
  `test/integration/data/test_locking.py` share a basename with no
  `__init__.py`, so `pytest test/unit test/integration` fails at collection.
  Run the tiers separately.

---

## My responsibility

I ran the integration suite without auditing what its fixtures target, against
an environment I had deliberately loaded with production credentials. The
`.env` loading was mine (a scratchpad runner built to work around a `$_`
password-parsing issue). The fixture was pre-existing and the missing guard was
pre-existing, but the action that fired it was mine.
