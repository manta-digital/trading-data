---
docType: review
layer: project
reviewType: code
slice: daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md
aiModel: moonshotai/kimi-k2.7-code
status: complete
dateCreated: 20260811
dateUpdated: 20260811
reviewedSha: 40b1a290a55f307031827a1d92bd8f24d8a91610
findings:
  - id: F001
    severity: pass
    category: design
    summary: "Registry abstraction centralizes table-specific rechunk configuration"
    location: src/manta_trading/market/maintenance/rechunk.py#RechunkTargetSpec
  - id: F002
    severity: pass
    category: testing
    summary: "Rechunk integration tests no longer read the production DB URL"
    location: test/integration/test_rechunk_driver.py#scratch_db
  - id: F003
    severity: pass
    category: testing
    summary: "Cold-start and migration tests verify chunk interval constants"
    location: test/integration/test_cold_start.py:267
  - id: F004
    severity: fail
    category: error-handling
    summary: "Missing hypertable causes `TypeError` instead of `PreflightError`"
    location: src/manta_trading/market/maintenance/rechunk.py#_assert_dimension_interval
  - id: F005
    severity: note
    category: testing
    summary: "No load test exercises the rechunk driver's concurrency path"
    location: test/integration/test_rechunk_driver.py
---

# Review: code — slice 170

**Verdict:** CONCERNS *(PM override — reviewer returned FAIL on F004 alone,
which was disproven by execution; see Disposition)*
**Model:** moonshotai/kimi-k2.7-code

## Disposition (2026-08-11)

| Finding | Disposition |
|---|---|
| F001, F002, F003 | Pass. F002 confirms the fix made after the first review. |
| F004 missing-hypertable `TypeError` | **Rejected — false positive, disproven by execution.** |
| F005 no load test | **Considered and declined**, with reasoning recorded in the slice design. |

### F004 is incorrect — this is the second time the same model raised it

It was F001 in the first review pass and is re-raised verbatim here. The
`None` guard it asks for **is present**, and was present at
`40b1a290a55f307031827a1d92bd8f24d8a91610` — the exact SHA this review
records as reviewed:

```console
$ git show 40b1a290:src/manta_trading/market/maintenance/rechunk.py | sed -n '208,212p'
        row = cur.fetchone()
    if row is None:
        raise PreflightError(f"{table} is not a hypertable on this database")
    if row[0] != interval:
```

The guard precedes the `row[0]` access, so the described `TypeError` cannot
occur. The finding makes a falsifiable prediction — that
`test_missing_hypertable_is_a_preflight_error` "will fail" — and it does not:

```console
$ uv run pytest test/unit/market/test_rechunk.py::TestPreflightMigrationId -q
4 passed
```

Executed directly against a cursor returning `None`, the function raises
`PreflightError: no_such_table is not a hypertable on this database` — the
exact behavior the finding says is missing. **No code change was made.** The
reviewer's FAIL verdict rested entirely on this finding, so the PM overrode it
to CONCERNS; the `[FAIL]` entry for F004 below is the reviewer's original text,
retained unedited for the record.

### F005 — considered, declined

Recorded in the slice design under "Load-tier consideration". Summary: the
driver is a manually-invoked maintenance command with no concurrent callers,
so there is no throughput budget to defend; its concurrency guarantee is
correctness (a writer is *blocked*, not silently lost), already asserted by
`test_concurrent_writer_blocked_during_window` driving the real race through
the `after_stage` seam; and it has now completed two production runs — 7.27 B
rows (166) and 65.6 M across 337 windows (170) — with zero errors. Revisit if
the driver is ever automated or run against a live writer.

## Findings

### [PASS] Registry abstraction centralizes table-specific rechunk configuration

The new `RechunkTarget` `StrEnum` and `RechunkTargetSpec` frozen dataclass move all table-specific values (hypertable name, chunk interval, dependent cagg views, and migration ID) into the `RECHUNK_TARGETS` registry. This satisfies the "one value, one source" rule, makes the driver genuinely table-agnostic, and lets the CLI dispatch through the enum rather than fragile string comparisons.

### [PASS] Rechunk integration tests no longer read the production DB URL

Both the original minute suite and the new daily suite build scratch hypertables inside the `ephemeral_db` fixture's throwaway database and no longer read `MT_TIMESCALE_DB_URL`. The tests therefore cannot reach real `minute_ohlcv` or `daily_ohlcv`, and `test_rechunk_driver.py` is correctly removed from `ALLOWED_PROD_URL_READERS` in `test_integration_prod_url_guard.py`.

### [PASS] Cold-start and migration tests verify chunk interval constants

New assertions in `test_cold_start.py` confirm both `minute_ohlcv` and `daily_ohlcv` land at their configured chunk intervals from migrations alone. `test_migration_050.py` and the new `TestMigration023DailyChunkIntervalFromConstant`/`TestMigration050DailyChunkInterval` unit tests guard that the interval derives from the single constant, not a hardcoded literal.

### [FAIL] Missing hypertable causes `TypeError` instead of `PreflightError`

`_assert_dimension_interval` calls `row[0]` immediately after `cur.fetchone()` without checking for `None`. If the target hypertable does not exist, the function raises a raw `TypeError: 'NoneType' object is not subscriptable` instead of a helpful `PreflightError`. The new unit test `TestPreflightMigrationId.test_missing_hypertable_is_a_preflight_error` in `test/unit/market/test_rechunk.py` expects a `PreflightError` whose message contains "not a hypertable", so it will fail. Add an explicit guard such as `if row is None: raise PreflightError(f"{table} is not a hypertable")` before the interval comparison.

### [NOTE] No load test exercises the rechunk driver's concurrency path

The Python rules require a load test for code on the concurrency / environment-layer path. The rechunk driver acquires per-window `EXCLUSIVE` locks and already has a functional concurrency test, but no new `tests/load/` test verifies latency, throughput, or lock contention under a realistic window count. Given that this is a manual maintenance command, the omission may be acceptable, but it should be explicitly considered.
