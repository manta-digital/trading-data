---
docType: review
layer: project
reviewType: code
slice: proof-on-existing-data
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/226-slice.proof-on-existing-data.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20261004
dateUpdated: 20261004
reviewedSha: b9588277c94ec810ea115cf8ec8e080f3c7e7abd
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
diffTruncated: false
durationSeconds: 66.6
squadronVersion: 0.18.2
findings:
  - id: F001
    severity: concern
    category: design-dry
    summary: "Duplicated memory-floor constant across harness modules"
    location: "scripts/proof_226/workers.py:35"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Thread-boundary and sampler error handling is partly silent"
    location: "scripts/proof_226/host.py:98"
  - id: F003
    severity: concern
    category: security
    summary: "Dynamic SQL built with f-strings in the harness"
    location: "scripts/proof_226/layouts.py:63"
  - id: F004
    severity: concern
    category: security
    summary: "Production URL is read by harness code"
    location: "scripts/proof_226/contention.py:30"
  - id: F005
    severity: concern
    category: structure
    summary: "Several functions and files exceed the size guidance"
    location: "scripts/provision_tick_cluster.sh"
  - id: F006
    severity: concern
    category: typing
    summary: "Heavy use of `type: ignore` and `sys.path` insertion in a strict-pyright project"
    location: "scripts/proof_226/queries.py:100"
  - id: F007
    severity: concern
    category: testing
    summary: "Test busy-waits and relies on private state"
    location: "test/unit/test_proof_226_contention.py:239"
  - id: F008
    severity: note
    category: design
    summary: "Production behaviour change from configuration edit"
    location: "src/manta_trading/data/tick/universe.py:49"
  - id: F009
    severity: note
    category: error-handling
    summary: "Header-refusal handling is consistent and well tested"
    location: "src/manta_trading/data/tick/file_days.py"
  - id: F010
    severity: note
    category: security
    summary: "Destructive-statement guard and migration are well designed"
    location: "scripts/proof_226/guard.py"
  - id: F011
    severity: note
    category: testing
    summary: "Load test pins archive paths and job IDs"
    location: "test/load/test_226_tick_query_nfr.py:50"
---

# Review: code — slice 226

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Duplicated memory-floor constant across harness modules

`MEM_AVAILABLE_FLOOR = 16 * 1024**3` is defined in `workers.py` and again in `contention_run.py:21`. Both are the same "16 GiB available" guard. The project rule is that changing a value should mean editing exactly one place. `common.py` already holds the shared `DATA_FREE_FLOOR_BYTES`, so this belongs there too. Other host-specific values are hard-coded in the modules that use them. These are `DEVICES = ("nvme0n1", "nvme1n1")` in `contention_sampler.py`, `EXPECTED_ROWS` in `common.py`, and `EXPECTED_TIER_UNITS` in `rebuild.py`. They are acceptable for a one-off harness, but they are magic values that go stale silently.

### [CONCERN] Thread-boundary and sampler error handling is partly silent

`PeakSampler._loop` runs in a daemon thread with no exception handling. If `cgroup_rss_bytes` or `cpu_times` raises, for example because `cgroup.procs` disappears during a restart, the thread dies without a trace. The report then shows a peak RSS and CPU that look valid but are understated. Record the failure and surface it in `__exit__`, as `TickLoop` does.

`TickLoop._loop` in `contention.py:96` uses `except Exception`. It does log with `logger.exception` and records the failure, so it satisfies rule (c), a documented boundary handler. Ruff's `BLE001` will still flag it unless there is an explicit `# noqa: BLE001`. The comment is there but the noqa is not, so add it.

### [CONCERN] Dynamic SQL built with f-strings in the harness

`set_layout` interpolates `layout.segment_by` and `layout.order_by` into `ALTER TABLE` via an f-string. `queries.py` interpolates `STATEMENT_TIMEOUT` into `SET statement_timeout`. The `_bars` helper there also builds SQL by string composition. All inputs are module constants, so this is not exploitable today. It still contradicts the "never f-string SQL" rule. Use `psycopg.sql.Identifier`/`SQL` composition, as `teardown.drop_proof` already does. In the same vein, `provision_tick_cluster.sh` builds `ALTER ROLE ... PASSWORD '%s'` with a password that may be read back from `.env`. A password containing `'` would break or inject. Generated hex passwords are safe, but the `.env`-sourced path is unvalidated.

### [CONCERN] Production URL is read by harness code

`PRODUCTION_URL_ENV = "MT_TIMESCALE_DB_URL"` is read from `.env` to sample production. The access is read-only (`default_transaction_read_only=on`, 5 s timeout, `SELECT` only), and the contention step needs it by design. It sits outside the test tiers, so it does not break the "tests never read the production URL" rule. It should still be explicitly designated by the Project Manager. The `.env` is read through `dotenv_values`, so the "one value, one source" rule is respected.

### [CONCERN] Several functions and files exceed the size guidance

The provisioning script is 362 lines. `rebuild.run` is about 60 lines. `contention_run.Run._firing` is about 50 lines with deeply nested loops. `contention_report.write_report` is about 50 lines. These are over the ~300-line and ~50-line guidance. Splitting the provisioning script by concern (cluster, hba, roles, env) would also make each step independently testable. Today only `env_add_keys` and `log_scrub` have tests.

### [CONCERN] Heavy use of `type: ignore` and `sys.path` insertion in a strict-pyright project

`queries.py`, `final.py`, `rebuild.py` and `size.py` carry many `# type: ignore[...]` comments on `execute`/`fetchone` results. These can mask real bugs under strict pyright. `proof_226_tick.py` and four test files mutate `sys.path` to import from `scripts/`. If `scripts` is outside pyright's `include = ["src", "tests"]`, none of this is checked at all, so the zero-error gate is not enforced for it. I could not confirm the pyproject setting from the diff. Prefer typed row helpers and make `proof_226` an importable package (a `pyproject` path entry or `pythonpath` in pytest config).

### [CONCERN] Test busy-waits and relies on private state

`test_a_stop_during_reset_starts_no_ingest` spins in `while not loop._stop.is_set(): pass`, a CPU-bound busy loop with no timeout. It can hang the suite if the stop never lands. Use a bounded `Event.wait`. Several tests also reach into `_stop`, `_thread` and `_proc`. Expose a small test seam instead.

### [NOTE] Production behaviour change from configuration edit

`TICK_UNIVERSE` for ES moves from `tier=None` to `TBBO` with a range of 2024-11-02 to 2025-01-01. This changes what `mt data tick pass` plans. The comment says the range equals what is already held, so no purchase occurs. Tests that depended on the untiered state were moved to `tick_support.universe.UNTIERED`, which is the correct isolation. The `test_universe` assertion checks shape only, which is sensible.

### [NOTE] Header-refusal handling is consistent and well tested

`ValueError`/`TypeError` raised for bad headers in `dbn_file.py` becomes `TickFileDecodeError`. Adopt, delivery and verify each catch only that specific type. The unclaimed days fail deterministically instead of being mislabelled as provider holes. Tests cover all six header-refusal kinds, on both v1 real data and v3 synthetic data, and the adopt, verify, ingest and delivery paths. One small oddity: `adopt._files_by_day` builds its map with `for rel in (f"{job_id}/{path.name}",)`, a single-element-tuple trick. A plain helper variable would read better.

### [NOTE] Destructive-statement guard and migration are well designed

`@destructive` checks `current_database()` against the constant name and registers each function. A parametrised test asserts that every registered function refuses another database before sending any SQL. `drop_proof` and `compress_eligible` have their own verified guards. Migration `tick_007` is tested on both empty and populated databases. It is irreversible once chunks are compressed, which the migration comment documents. The bash `env_add_keys` writes atomically and enforces mode and owner. `log_scrub` reports line numbers only, and both are tested, including the failure path and the missing-final-newline case.

### [NOTE] Load test pins archive paths and job IDs

The test hard-codes `/data/tick-archive` and four job IDs. It fails instead of skipping when files are absent, as documented. It reads no production URL, and the existing guard test covers the tier. It meets the load-tier rule with a 1 s latency assertion.

### Run Digest

- Response length: 7157 chars
- Response is newline-free: no
- Tool calls made: 2
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 66.6 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 11
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 11
- Finding-shaped matches — surviving validation: 11
