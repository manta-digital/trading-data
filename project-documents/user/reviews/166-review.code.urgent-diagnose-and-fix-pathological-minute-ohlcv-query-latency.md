---
docType: review
layer: project
reviewType: code
slice: urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/166-slice.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md
aiModel: claude-fable-5
status: complete
dateCreated: 20260719
dateUpdated: 20260719
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "Rewrite cycle can silently lose rows written by concurrent application writers"
    location: src/manta_trading/market/maintenance/rechunk.py:187-215
  - id: F002
    severity: concern
    category: tooling
    summary: "Mandatory ruff and pyright configuration blocks are absent from pyproject.toml"
    location: unverified
  - id: F003
    severity: concern
    category: test-coverage
    summary: "No load-test asserting the latency outcome the slice exists to fix"
    location: src/manta_trading/market/maintenance/rechunk.py
  - id: F004
    severity: note
    category: error-handling
    summary: "`raise typer.Exit(...)` inside except blocks without `from exc` (B904)"
    location: src/manta_trading/cli/commands/data.py:1047-1059
  - id: F005
    severity: note
    category: test-structure
    summary: "Integration tests rely on in-file definition order; interrupt test duplicates fixture SQL"
    location: test/integration/test_rechunk_driver.py:748-886
  - id: F006
    severity: note
    category: style
    summary: "Line-length and import-order lint violations in rechunk.py"
    location: src/manta_trading/market/maintenance/rechunk.py:32
  - id: F007
    severity: pass
    category: design
    summary: "Chunk interval is a genuine single source of truth"
    location: src/manta_trading/constants.py:42-53
  - id: F008
    severity: pass
    category: correctness
    summary: "Rewrite cycle is defensively transactional with explicit invariant checks"
    location: src/manta_trading/market/maintenance/rechunk.py:187-215
---

# Review: code — slice 166

**Verdict:** CONCERNS
**Model:** claude-fable-5

## Findings

### [CONCERN] Rewrite cycle can silently lose rows written by concurrent application writers

Pre-flight (`_resolve_paused_job_violations`, rechunk.py:162) verifies only that TimescaleDB *background jobs* (compression policy, cagg refresh) are paused. Nothing checks for, blocks, or documents concurrent **application writers** — e.g. a running `mt data pull` or the slice-162 coverage-aware seeding, which backfills *historical* windows (exactly the compressed REWRITE windows this driver targets). The race in `_rewrite_window`: the stage `CREATE TEMP TABLE ... AS SELECT` captures a snapshot; a writer that commits an insert into the window after that snapshot but before `drop_chunks` takes its exclusive lock has its rows destroyed by the drop and never reinserted. The `reinserted != staged` check cannot catch this — both counts equally exclude the concurrent rows — so the loss is silent, precisely the failure mode recorded in the project lesson "cagg refresh during restructuring silently loses rows." Minimum fix: pre-flight should assert no other sessions hold locks / have active queries against the table (`pg_stat_activity`/`pg_locks`), or take an explicit `LOCK TABLE ... IN EXCLUSIVE MODE` per window transaction before staging; at minimum the CLI docstring in `data.py#data_rechunk` must instruct the operator to stop all fetch/seed processes, which it currently does not.

### [CONCERN] Mandatory ruff and pyright configuration blocks are absent from pyproject.toml

The Python rules require every project to carry `[tool.ruff]`/`[tool.ruff.lint]` (selecting at least `E,F,W,I,UP,BLE,ASYNC,B`) and a strict `[tool.pyright]` block, and instruct reviewers to flag their absence before substantive work. On this branch `pyproject.toml` contains only `[tool.hatch.build.targets.wheel]` and `[tool.pytest.ini_options]` — no ruff, no pyright. This is pre-existing rather than introduced by the slice, but it means none of the mechanically-enforced rules (blind-except, line length, import order) actually gate this new code, and several small violations shipped as a result (see NOTE findings below). Add the two copy-paste baseline blocks from the rules. (`location: unverified` because pyproject.toml was outside the reviewed diff; absence verified via `git show <branch>:pyproject.toml`.)

### [CONCERN] No load-test asserting the latency outcome the slice exists to fix

The load-test rule requires code on the simulation/network/environment paths to have at least one `test/load/` test asserting latency, throughput, or resource bounds. A `test/load/` tier already exists (`test_146_part*_nfrs.py`), and this slice's whole purpose is a latency NFR (planning ~14 min → sub-second; chunk count 25,256 → ~1,200). The integration tests verify functional correctness (chunk reduction, data integrity, idempotency, resume) but nothing asserts a post-rechunk latency or chunk-count bound on a realistic configuration, so a future regression (e.g. a migration reverting the interval, or re-fragmentation) would pass CI. The unit test pinning `MINUTE_OHLCV_CHUNK_INTERVAL == timedelta(days=7)` guards the constant, not the outcome. This matches the already-noted open "NFR follow-up" — it should land within this slice, not after.

### [NOTE] `raise typer.Exit(...)` inside except blocks without `from exc` (B904)

The three new `except ... raise typer.Exit(...)` blocks in `data_rechunk` omit `raise ... from exc`, which ruff rule B904 (in the mandated `B` set) flags. The pattern matches existing handlers elsewhere in `data.py`, and each block does print the error before exiting — so this is consistent-but-unenforced style rather than a bug. Worth fixing wholesale when the ruff config lands.

### [NOTE] Integration tests rely on in-file definition order; interrupt test duplicates fixture SQL

`TestRechunkDriver` is an ordered scenario (`test_e` → `test_a` → `test_b` → `test_c`) that depends on pytest's definition-order execution against one module-scoped fixture; the letter names actively suggest a different (alphabetical) order than what runs, and any randomizing/xdist plugin breaks the chain. Separately, `TestRechunkInterrupt.test_d` (lines 826–864) re-creates the scratch table by copy-pasting most of the `scratch_db` fixture SQL — a DRY violation the in-test comment acknowledges with a garbled sentence (lines 819–821, "via the module fixture being function-scoped here would be wasteful"). Extracting a `_build_scratch(conn, with_cagg: bool)` helper would fix both the duplication and the comment.

### [NOTE] Line-length and import-order lint violations in rechunk.py

Line 32 (`from manta_trading.constants import Granularity, GRANULARITY_SOURCE, MINUTE_OHLCV_CHUNK_INTERVAL`, 95 chars) and line 283 (the `todo` comprehension, 97 chars) exceed the 88-character limit; line 32's names are also not in isort order (`GRANULARITY_SOURCE` sorts before `Granularity`). Would be caught mechanically once the ruff config exists.

### [PASS] Chunk interval is a genuine single source of truth

`MINUTE_OHLCV_CHUNK_INTERVAL` is defined once with a documented rationale and consumed by migration 001c, migration 043 (via `_minute_chunk_interval_sql()` in `minute.py`), the rechunk driver, and the tests — no restated literals. The unit tests explicitly guard against a hardcoded "4 hours" reappearing. Exemplary compliance with the no-scattered-values rule.

### [PASS] Rewrite cycle is defensively transactional with explicit invariant checks

Stage→drop→reinsert runs inside one `conn.transaction()`; the dropped-chunk count is checked against the catalog snapshot and the reinserted rowcount against the staged count, with any mismatch raising inside the transaction so the whole cycle rolls back. Window state is re-derived from the catalog every run, making the driver idempotent and resumable (including the crash-between-commit-and-compress case via `COMPRESS_ONLY`), and the integration suite exercises dry-run, interrupt/resume, no-op re-run, and pre-flight refusal against a gap-faithful scratch hypertable that never touches `minute_ohlcv`.

## Resolution (2026-07-19, Senior AI)

- **F001 — FIXED.** `_rewrite_window` now takes `LOCK TABLE <hypertable> IN
  EXCLUSIVE MODE` as the first statement of every window transaction, *before*
  the stage snapshot — a concurrent application writer physically cannot
  commit into the window between stage and drop (writers block ~seconds per
  window; readers unaffected). Verified by a new deterministic integration
  test (`test_concurrent_writer_blocked_during_window`) using an
  `after_stage` test seam that attempts an INSERT from a second connection
  inside the critical span and asserts `LockNotAvailable`. The CLI docstring
  now also instructs operators to stop fetch/seed processes (the lock is the
  guarantee; the instruction avoids ~1,175 windows of writer stalls). Note:
  the completed prod run was not exposed — daemon stopped and no writers
  active — but the tool is reusable and is now safe by construction.
- **F002 — PARTIALLY FIXED (remainder recorded as debt).** `[tool.ruff]` /
  `[tool.ruff.lint]` with the mandated select set added to pyproject.toml;
  all slice-166 files pass it. For the type checker the project's established
  choice is **mypy** (the rules' "or mypy" alternative to strict pyright); a
  `[tool.mypy]` block now records that choice. Repo-wide, the newly-active
  ruff config surfaces **1,730 pre-existing violations (752 autofixable)** in
  legacy code — fixing those is a separate chore for the PM to schedule, not
  a slice-166 change; until then the config gates new code by review
  convention rather than CI.
- **F003 — REJECTED, decision re-affirmed.** The no-load-test decision was
  made explicitly at task review (166 task-review F004, recorded in task D2):
  the load-test tier covers simulation/network/concurrency/environment paths,
  and the NFR is measured against the 126 GB prod table that CI cannot
  reproduce — a latency assertion on a fixture DB does not exercise the NFR.
  The named regression vectors are already guarded where CI *can* see them:
  a migration reverting the interval fails
  `test_dimension_interval_equals_constant` and
  `test_001c_chunk_interval_from_constant`; a constant change fails
  `test_constant_is_seven_days`. NFR regression coverage placement is
  explicitly assigned to follow-up slice 167 (cagg-backed `bars_summary`),
  where the PM decides tier placement.
- **F004 — FIXED.** All three `except` blocks in `data_rechunk` now
  `raise typer.Exit(...) from exc` (B904 clean). Legacy handlers elsewhere in
  data.py are part of the F002 debt sweep.
- **F005 — FIXED.** Integration tests rewritten: shared
  `_build_scratch(conn, with_cagg=...)` helper (duplication and the garbled
  comment gone), function-scoped fixtures, every test independent of
  definition order (the ordered a/b/c/e scenario is dissolved —
  `test_rerun_is_noop` performs its own first run). Suite passes under the
  new structure (6/6 including the new F001 test).
- **F006 — FIXED.** Import order and line lengths corrected in rechunk.py and
  the test files; all slice files pass the now-active mandated ruff set.
