---
docType: review
layer: project
reviewType: code
slice: cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/167-slice.cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr.md
aiModel: z-ai/glm-5.2
status: complete
dateCreated: 20260727
dateUpdated: 20260727
findings:
  - id: F001
    severity: concern
    category: uncategorized
    summary: "`source_table` parameter interpolated into SQL via public API"
    location: src/manta_trading/market/maintenance/cagg_freshness.py#_raw_max
  - id: F002
    severity: concern
    category: uncategorized
    summary: "`_restore_probe_timeout` resets to DEFAULT, clobbering caller's statement_timeout"
    location: src/manta_trading/market/maintenance/cagg_freshness.py#_restore_probe_timeout
  - id: F003
    severity: concern
    category: uncategorized
    summary: "Inconsistent JSON shape for `coverage` between empty and full report paths"
    location: src/manta_trading/cli/commands/data.py:770-785
  - id: F004
    severity: concern
    category: uncategorized
    summary: "`cagg_parity.py` is truncated in the diff and cannot be fully reviewed"
    location: src/manta_trading/market/maintenance/cagg_parity.py
  - id: F005
    severity: note
    category: uncategorized
    summary: "Missing `from None` on one `typer.Exit` raise"
    location: src/manta_trading/cli/commands/data.py:3080
  - id: F006
    severity: note
    category: uncategorized
    summary: "`caggs_verify` uses literal exit code `1` instead of a named constant"
    location: src/manta_trading/cli/commands/data.py:3126
  - id: F007
    severity: note
    category: uncategorized
    summary: "`caggs_verify` does not catch `KeyboardInterrupt` while `caggs_repair` does"
    location: src/manta_trading/cli/commands/data.py#caggs_verify
  - id: F008
    severity: note
    category: uncategorized
    summary: "`print` used directly for progress callback instead of `print_result`/`Console`"
    location: src/manta_trading/cli/commands/data.py:3271
  - id: F009
    severity: note
    category: uncategorized
    summary: "`assert` used for runtime invariant in production code"
    location: src/manta_trading/market/maintenance/cagg_parity.py
  - id: F010
    severity: note
    category: uncategorized
    summary: "`list[dict]` should be `list[dict[str, object]]` per typing rules"
    location: src/manta_trading/cli/commands/data.py:3148
  - id: F011
    severity: note
    category: uncategorized
    summary: "Tests not visible in this diff — coverage requirements unverified"
    location: unverified
  - id: F012
    severity: note
    category: uncategorized
    summary: "`_emit_empty` is a nested closure capturing `coverage` and `json_output`"
    location: src/manta_trading/cli/commands/data.py:768
---

# Review: code — slice 167

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.2

## Findings

### [CONCERN] `source_table` parameter interpolated into SQL via public API

`assert_cagg_fresh` exposes a public `source_table: str | None` parameter that flows unchecked into `_raw_max` and `_max_probe`, where it's interpolated into f-strings:

```python
cur.execute(
    f"SELECT time_bucket(%s::interval, max(time)) FROM {source_table}",  # noqa: S608
    (bucket_width,),
)
...
cur.execute(f"SELECT max({column}) FROM {relation}")  # noqa: S608
```

The `# noqa: S608` comments and the docstring justifications (identifiers cannot be bound parameters, `source_table` arrives resolved through `GRANULARITY_SOURCE`) hold for the *internal* path. But the public `source_table` seam bypasses `_resolve_source_table`'s validation. The docstring says it's "for integration tests" with scratch tables, but the API surface is public and unguarded — any future caller passing user-influenced data would introduce SQL injection. Consider validating `source_table` against a whitelist (e.g. `GRANULARITY_SOURCE.values()` or `COVERAGE_SOURCE_TABLE.values()`) when it's supplied, or renaming it to `_source_table` to signal test-only use. The `now` parameter is also exposed similarly; while not a security risk, it warrants the same "test-only" signaling.

### [CONCERN] `_restore_probe_timeout` resets to DEFAULT, clobbering caller's statement_timeout

`_set_probe_timeout` uses plain `SET statement_timeout = '10s'` (session-scoped) and `_restore_probe_timeout` resets via `SET statement_timeout = DEFAULT`. The docstring of `_set_probe_timeout` acknowledges this is intentional for autocommit connections, but the restore semantics are wrong for any caller that had set its own non-default `statement_timeout` *before* calling `assert_cagg_fresh`:

- If a caller did `SET statement_timeout = '60s'` (session level) and then called `assert_cagg_fresh`, after the call the session value would be `DEFAULT` (the postgresql.conf setting), not `'60s'`.
- `build_minute_coverage_index` happens to call `assert_cagg_fresh` *before* its own `SET LOCAL`, so the current usage works, but this is fragile — any future reader that pre-sets a timeout will silently lose it.

The fix is straightforward: capture the current value with `SHOW statement_timeout` before setting the probe timeout and restore that exact value. Otherwise document explicitly that callers must not pre-set `statement_timeout`.

### [CONCERN] Inconsistent JSON shape for `coverage` between empty and full report paths

`_emit_empty` emits a flat `coverage_stale` boolean:
```python
payload: dict[str, object] = {"message": msg, **extra}
if coverage is not None:
    payload["coverage_stale"] = coverage.is_stale
```

But the populated-report path (via `status_report_to_json` in `status_table.py`) emits a nested `coverage` object with `is_stale` inside it:
```python
d["coverage"]["is_stale"] = report.coverage.is_stale
```

So a JSON consumer sees `{"message": ..., "coverage_stale": true}` on an empty result but `{"rows": [...], "coverage": {"is_stale": true, ...}}` on a populated one. The "no data" and "have data" payloads have differently-shaped coverage fields for the same logical signal. Either nest `coverage` in the empty path (e.g. `payload["coverage"] = {"is_stale": coverage.is_stale}`) or document the divergence as intentional. The "single source of truth" rule in CLAUDE.md argues against two shapes for one concept.

### [CONCERN] `cagg_parity.py` is truncated in the diff and cannot be fully reviewed

The diff shows the file content truncated at 100KB partway through `_raw_bounds`, with the comment `[truncated at 100KB — file too large for API review]`. Functions like `compute_parity`, window iteration, backend cancellation, and the timeout connection wrapper's full behavior cannot be verified. This file contains the prod-path parity queries that the docstrings claim run under explicit `statement_timeout` with backend cancellation — reviewers cannot confirm that discipline is actually applied to every query. Re-split the file (it's already ~409 lines per the `+0,0 +1,409` header, which is over the project's ~300 line guideline) or provide the file separately for review.

### [NOTE] Missing `from None` on one `typer.Exit` raise

In `_resolve_minute_granularities`, the first invalid-token path uses `raise typer.Exit(1) from None` but the second (non-minute cagg) path omits `from None`:
```python
if gran not in MINUTE_CAGG_GRANULARITIES:
    print_error(...)
    raise typer.Exit(1)   # missing "from None"
```
The original `ValueError` chain will surface in the traceback. The sibling raise two lines above shows the intended pattern.

### [NOTE] `caggs_verify` uses literal exit code `1` instead of a named constant

```python
if not settings.timescale_db_url:
    print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
    raise typer.Exit(1)
```

Both `_resolve_minute_granularities` raises also use literal `1`. The repair command defines `_EXIT_REPAIR_PREFLIGHT = 1` for the same condition; verify defines `_EXIT_PARITY_FAILURE = 2` for its failure code but leaves the preflight as a magic `1`. Per CLAUDE.md ("Never scatter comparison values across code"), define a `_EXIT_VERIFY_PREFLIGHT = 1` constant.

### [NOTE] `caggs_verify` does not catch `KeyboardInterrupt` while `caggs_repair` does

`caggs_repair` has an explicit `except KeyboardInterrupt` that prints "Interrupted — backend cancelled. Re-run..." and exits 130. `caggs_verify` relies on `_TimeoutConnection.__exit__` to cancel the backend but lets the `KeyboardInterrupt` propagate raw to Typer. The verify command's docstring claims "cancels its server-side backend on interrupt" — the cancellation happens, but the operator gets no friendly message or documented exit code. Symmetry with `caggs_repair` would improve operator experience; the standing rule documentation also references both commands together.

### [NOTE] `print` used directly for progress callback instead of `print_result`/`Console`

```python
result = run_repair(
    ...
    progress=lambda msg: print(msg, flush=True),
)
```
This bypasses the centralized output helpers and would not be redirected consistently if `--json` were ever added to `repair`. Minor, but the rest of the CLI routes through `print_result`/`print_error` for a reason.

### [NOTE] `assert` used for runtime invariant in production code

```python
row = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()
assert row is not None  # pg_backend_pid always returns one row
```
`assert` is removed under `python -O`. While `pg_backend_pid` always returns a row, an explicit `if row is None: raise RuntimeError(...)` would survive optimization. The comment makes the intent clear, so impact is low.

### [NOTE] `list[dict]` should be `list[dict[str, object]]` per typing rules

```python
report_rows: list[dict] = [
    {"start": w.start.isoformat(), ...}
    ...
]
```
Python rules require `dict[str, object]` rather than bare `dict`. The sibling assignment a few lines below (`report_rows = [...]` in the `else` branch) is unannotated, which is fine, but the annotated one should be parameterized.

### [NOTE] Tests not visible in this diff — coverage requirements unverified

The diff excludes test files (the filter `:!*.md` etc. doesn't exclude `tests/**`, but no test hunks appear). The freshness module, parity module, and new CLI commands all have non-trivial branching (D1-D6 signals, window grid alignment, exit-code matrix) that the project's testing standards require to be covered. Slice 168's design references specific test fixtures (the `now` clock seam, `source_table` scratch table). Cannot verify that those tests exist, parametrize edge cases, or cover the `StalenessSignal` dispatch. Please confirm `tests/` for these modules is included in the slice's full commit set.

### [NOTE] `_emit_empty` is a nested closure capturing `coverage` and `json_output`

Defining `_emit_empty` inside `data_status` creates a closure over `coverage` and `json_output` from the enclosing scope. This works but couples the helper to its definition site and makes the captured bindings implicit. A module-level `_emit_empty(msg, *, coverage, json_output, **extra)` would make the dependencies explicit and is more testable in isolation. The `data_status` function is already long; extracting this helper would also reduce its line count.
