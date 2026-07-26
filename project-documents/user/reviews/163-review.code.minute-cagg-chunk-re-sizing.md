---
docType: review
layer: project
reviewType: code
slice: minute-cagg-chunk-re-sizing
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/163-slice.minute-cagg-chunk-re-sizing.md
aiModel: claude-fable-5
status: complete
dateCreated: 20260726
dateUpdated: 20260726
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "Pre-flight checks 1 and 4 make the default `--granularity all` repair impossible to complete in one run"
    location: src/manta_trading/market/maintenance/cagg_repair.py:153-198
  - id: F002
    severity: concern
    category: correctness
    summary: "Sweep order contradicts the documented \"smallest-first: 4h → 1h → 15m → 5m\" order"
    location: src/manta_trading/market/maintenance/cagg_repair.py:446
  - id: F003
    severity: concern
    category: efficiency
    summary: "`compute_parity` re-runs every raw `COUNT(*)` window scan once per cagg — 4× the heaviest prod queries"
    location: src/manta_trading/market/maintenance/cagg_parity.py:359-395
  - id: F004
    severity: concern
    category: dry
    summary: "Duplicated definitions: `MINUTE_CAGG_GRANULARITIES` and the `minute_ohlcv` table name are each defined in multiple modules"
    location: src/manta_trading/market/maintenance/cagg_parity.py:42-51
  - id: F005
    severity: concern
    category: error-handling
    summary: "`RepairError` is never raised; the documented \"window rebuild failed\" exit path is unreachable and non-operational DB errors escape unhandled"
    location: src/manta_trading/market/maintenance/cagg_repair.py:54-56
  - id: F006
    severity: concern
    category: static-analysis
    summary: "New CLI region adds mypy errors on a slice-touched file (merge bar is zero)"
    location: src/manta_trading/cli/commands/data.py:3182-3329
  - id: F007
    severity: note
    category: error-handling
    summary: "`_TimeoutConnection.__enter__` leaks the connection if setup fails after connect"
    location: src/manta_trading/market/maintenance/cagg_parity.py:206-216
  - id: F008
    severity: note
    category: test-coverage
    summary: "Test name contradicts its assertion; uncompressed crash-window chunks rely on the paused columnstore policy being resumed"
    location: test/unit/market/test_cagg_repair.py:373-385
  - id: F009
    severity: note
    category: structure
    summary: "`data.py` continues to grow far past the project's file/function size guidance"
    location: src/manta_trading/cli/commands/data.py
  - id: F010
    severity: pass
    category: correctness
    summary: "Resumability, prod-query discipline, and constant hygiene are well executed"
    location: src/manta_trading/market/maintenance/cagg_repair.py:296-322
  - id: F011
    severity: pass
    category: test-coverage
    summary: "Test quality on the new modules is high"
    location: test/unit/market/test_cagg_repair.py
---

# Review: code — slice 163

**Verdict:** CONCERNS
**Model:** claude-fable-5

## Findings

### [CONCERN] Pre-flight checks 1 and 4 make the default `--granularity all` repair impossible to complete in one run

`preflight` runs per cagg at the start of that cagg's sweep. Check 1 (cagg_repair.py:236) requires the *target's* refresh policy paused; check 4 (`_check_coverage_index_available`) requires the 4h coverage cagg's refresh policy **scheduled** whenever the target is any other cagg. Since the 4h cagg is both a repair target and the coverage-index source, no static pause configuration satisfies an all-cagg sweep: pause all four up-front → refused at the first cagg (coverage paused); leave 4h running → the first three repair, then the 4h pre-flight refuses (its own jobs scheduled) after what could be hours of sweeping. The only success path is racy mid-run operator intervention (pausing 4h while another cagg's sweep runs), which nothing documents. The failure is safe (explicit refusal, completed windows preserved via parity), which is why this is not a FAIL — but the CLI's advertised default (`--granularity` defaults to `all`, and the help text says "the cagg's refresh policy AND columnstore policy must be paused" with no mention of the 4h exception) is guaranteed to end in a pre-flight refusal. Either order the sweep 4h-first and auto-resume its policy (with catch-up refresh) before proceeding, or make `all` orchestrate the pause/resume explicitly, or refuse `all` up-front with instructions to run per-granularity.

### [CONCERN] Sweep order contradicts the documented "smallest-first: 4h → 1h → 15m → 5m" order

`run_repair`'s docstring states "the CLI passes smallest-first: 4h → 1h → 15m → 5m", and the comment at cagg_parity.py:45-46 says "4h → 1h → 15m → 5m is the sweep order; parity reporting keeps ascending granularity". But `MINUTE_CAGG_GRANULARITIES` is `(M5, M15, H1, H4)`, `run_repair` defaults to it unmodified, and `_resolve_minute_granularities` (data.py:3079) canonicalizes user input to that same order — so the actual sweep is 5m → 15m → 1h → 4h, the *largest* cagg first. Nothing anywhere reverses the tuple for repair. If the design intent was to validate the approach on the cheapest cagg first (and to repair/release the coverage-index 4h cagg early, which would also soften the finding above), the code violates it; if the code order is intended, both docstrings are wrong. Tests assert the canonical order but never the documented sweep order, so the contradiction is uncaught.

### [CONCERN] `compute_parity` re-runs every raw `COUNT(*)` window scan once per cagg — 4× the heaviest prod queries

`compute_parity` enumerates the ~117 windows once but calls `_window_counts(conn, view_name, windows)` inside the per-granularity loop, and `_window_counts` issues the raw `COUNT(*)` over `minute_ohlcv` (4.4B rows) for every window *per cagg*. The raw side is cagg-independent, so a default `mt data caggs verify` runs ~468 raw window counts where ~117 suffice — roughly 4× the most expensive scans on the prod box whose query discipline this slice otherwise carefully respects (each count can approach the measured hundreds-of-seconds range on dense windows). Compute the raw counts once and reuse them across the four reports. (`_window_parity` in the repair sweep re-probing raw per window is correct — raw can grow during a long sweep — this finding is about `verify` only.)

### [CONCERN] Duplicated definitions: `MINUTE_CAGG_GRANULARITIES` and the `minute_ohlcv` table name are each defined in multiple modules

Project rule: "Changing a value should require editing exactly one place." `MINUTE_CAGG_GRANULARITIES` is defined in cagg_parity.py:47 *and* pre-exists in rechunk.py:45 (same four granularities, same purpose family). The raw table name `"minute_ohlcv"` now has three module-level definitions — `rechunk.RECHUNK_TABLE`, `cagg_parity.RAW_TABLE`, `cagg_repair._RAW_TABLE` (cagg_repair.py:88, which already imports five names from cagg_parity yet redefines this one) — despite `GRANULARITY_SOURCE[Granularity.M1]` in constants.py:203 already being the canonical mapping the slice itself uses for the cagg view names. Consolidate to one definition each.

### [CONCERN] `RepairError` is never raised; the documented "window rebuild failed" exit path is unreachable and non-operational DB errors escape unhandled

`RepairError`'s docstring promises "the failing window is identified in the message", and the CLI maps it to exit code 2 (data.py:3400) — but no code path in `cagg_repair.py` raises it. A rebuild failure surfaces only if it happens to be a `psycopg.OperationalError` (caught at data.py:3411); any other `psycopg.Error` during `_rebuild_window` (e.g. `ProgrammingError`, `InternalError` from a failed `drop_chunks`/`refresh_continuous_aggregate`) propagates as a raw traceback with no window identification and an undocumented exit code, contradicting the command's documented exit-code contract. Wrap per-window failures in `RepairError` with the window bounds (as `rechunk.RechunkError` evidently intends for its sibling), or delete the dead class and fix the CLI/docs.

### [CONCERN] New CLI region adds mypy errors on a slice-touched file (merge bar is zero)

pyproject.toml documents "Zero errors on files touched by a slice is the merge bar." `mypy src/manta_trading/cli/commands/data.py` reports 44 errors, of which 7 fall in the new verify/repair region (lines 3182, 3212, 3214, 3327, 3329 among them): `print_result` is annotated to accept `dict | list` but is called with `str` and `Table`. The new code follows the file's existing (broken) convention, and the correct fix is widening `print_result`'s signature in `cli/output.py` rather than changing call sites — but as written the slice adds new instances of the violation on a touched file. Ruff also flags one new issue in the region: `UP037` at data.py:3038 (quoted `tuple["Granularity", ...]` annotation is unnecessary under `from __future__ import annotations`).

### [NOTE] `_TimeoutConnection.__enter__` leaks the connection if setup fails after connect

If the `SET statement_timeout` or `pg_backend_pid()` query raises after `psycopg.connect` succeeds, `__exit__` never runs (the `with` body was not entered) and the connection is left to the GC. A `try/except: conn.close(); raise` around the post-connect setup would close it deterministically. Low impact — failures here are rare and the process typically exits — hence NOTE.

### [NOTE] Test name contradicts its assertion; uncompressed crash-window chunks rely on the paused columnstore policy being resumed

`test_kill_before_compress_only_recompresses` asserts that *nothing* is recompressed (the window reads DONE by parity and is skipped), the opposite of what the name suggests. The underlying behavior is deliberate per the comment ("compression is the columnstore policy's job"), but note the operational consequence: a Ctrl-C between refresh and compress leaves a 70-day chunk uncompressed until the operator remembers to resume the columnstore policy the pre-flight required them to pause. Rename the test and ensure the runbook/CLI resume messaging covers resuming the paused jobs.

### [NOTE] `data.py` continues to grow far past the project's file/function size guidance

The file is now ~3,330 lines against the project's ~300-line guideline, and `caggs_verify` (~130 lines) and `caggs_repair` (~100 lines) exceed the ~50-line function guidance — `caggs_verify` interleaves JSON payload construction and table rendering that would extract cleanly into helpers or a `data_caggs.py` command module. Pre-existing debt that this slice worsens by +315 lines; flagged as NOTE since splitting the module is its own chore.

### [PASS] Resumability, prod-query discipline, and constant hygiene are well executed

The parity-derived (not bookkept) resume model matches the journal's crash-window analysis; every prod query runs under a measured, documented `statement_timeout` with backend cancellation on interrupt (the 300s→1800s resizing docstring in constants.py:124-146 is exemplary — sized from the observed 5m-sweep failure, not an estimate); migrations 044/045 resolve caggs by view name from `GRANULARITY_SOURCE` with a fail-fast existence guard; and the cold-start integration test asserts the migrations' effects against the real catalog, including a regression guard for the actual prod syntax-error incident (untyped `7 days` interval). The coverage-index pre-flight guard directly encodes the 2026-07-25 re-seed-loop incident with an actionable refusal message including the catch-up refresh step.

### [PASS] Test quality on the new modules is high

Tests cover the three D1 crash-window outcomes, drop→refresh→compress ordering (asserted on call order, not SQL text), dry-run zero-mutation, resume-skip, all pre-flight refusal branches including the cross-granularity coverage guard and its message content, epoch-grid straddling, canonical-order resolution, and CLI exit codes. The mock `fetchall` in `test_schema_migrations.py` is query-aware to avoid breaking sibling autocommit migrations — a thoughtful touch. Gap noted in the CONCERN above: no test pins the documented sweep order, which is exactly where code and docs diverged.
