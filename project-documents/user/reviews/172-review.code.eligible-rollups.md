---
docType: review
layer: project
reviewType: code
slice: eligible-rollups
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/172-slice.eligible-rollups.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261016
dateUpdated: 20261016
reviewedSha: 04b0ed328282b45989aaaa9bcd279342bbdc107f
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 0
diffTruncated: false
turns: 1
promptTokens: 80990
cachedTokens: 3232
cacheWriteTokens: 77756
completionTokens: 2584
durationSeconds: 21.7
costUsd: 0.343608
costBasis: provider
squadronVersion: 0.23.0
findings:
  - id: F001
    severity: concern
    category: design
    summary: "Cutover script imports private names from cagg_repair"
    location: "scripts/cutover_172_build.py:29-34"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Timers are not released if the hold fails part-way"
    location: "scripts/cutover_172_eligible_rollups.py:325-331"
  - id: F003
    severity: concern
    category: error-handling
    summary: "Report-writing failures and unexpected exceptions are not covered by the step boundary"
    location: "scripts/cutover_172_eligible_rollups.py:333-337"
  - id: F004
    severity: concern
    category: error-handling
    summary: "Build step failure leaves `_repair_one` pause/resume unprotected against the pause itself"
    location: "scripts/cutover_172_eligible_rollups.py:178-197"
  - id: F005
    severity: concern
    category: typing
    summary: "Missing type annotations and `Any` in the new script code"
    location: "scripts/cutover_172_eligible_rollups.py:166"
  - id: F006
    severity: concern
    category: error-handling
    summary: "`assert` used for runtime control flow in a production script"
    location: "scripts/cutover_172_eligible_rollups.py:107"
  - id: F007
    severity: concern
    category: configuration
    summary: "Raw `os.environ` copy plus settings object disagree on configuration source"
    location: "scripts/cutover_172_eligible_rollups.py:100-104"
  - id: F008
    severity: concern
    category: testing
    summary: "`CAGG_NEXT_SUFFIX` test hard-codes the string \"no next target\""
    location: "test/unit/cli/commands/test_data_caggs.py (`test_rebuild_coverage_daily_next_is_refused`), test/unit/market/test_coverage_rebuild.py (`test_daily_next_raises`)"
  - id: F009
    severity: concern
    category: dry
    summary: "Duplicated and stale 4h-view derivations"
    location: "scripts/cutover_172_build.py:45-48"
  - id: F010
    severity: concern
    category: security
    summary: "Raw SQL in `row_counts` interpolates view names via f-string"
    location: "scripts/cutover_172_build.py:150"
  - id: F011
    severity: note
    category: structure
    summary: "`scripts/cutover_172_eligible_rollups.py` exceeds the preferred module size and mixes concerns"
    location: "scripts/cutover_172_eligible_rollups.py"
  - id: F012
    severity: note
    category: style
    summary: "Import ordering workarounds with `# noqa: E402`"
    location: "scripts/cutover_172_eligible_rollups.py:56-75"
  - id: F013
    severity: note
    category: testing
    summary: "Test coverage is strong"
    location: "test/integration/test_migration_063_eligible_rollups_next.py"
  - id: F014
    severity: note
    category: error-handling
    summary: "Magic fallback in `row_counts`"
    location: "scripts/cutover_172_build.py:151"
---

# Review: code — slice 172

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Cutover script imports private names from cagg_repair

`_PROC_REFRESH` and `_resolve_cagg_jobs` are underscore-private members of `cagg_repair`. `coverage_rebuild.py` also already imports `_resolve_cagg_jobs` from it. Coupling to private names makes a rename in `cagg_repair` break the script silently, because `scripts/` is outside the package's checks. The `jobs()` wrapper in the build module hides the dependency without removing it. Expose public names (`resolve_cagg_jobs`, `PROC_REFRESH`) from `cagg_repair`.

### [CONCERN] Timers are not released if the hold fails part-way

`hold_timers()` runs before the `try/finally`. If `systemctl stop` fails on the second timer, or `wait_for_unit_to_end` raises, the first timer stays stopped and nothing restarts it. The function returns `held` only on success, so the caller can't clean up. Production acquisition would stay paused. Move the hold inside the `try`, or have `hold_timers` restore its own partial state on failure.

### [CONCERN] Report-writing failures and unexpected exceptions are not covered by the step boundary

`run_steps` catches a fixed list of exception types. An unexpected exception, such as `KeyError` from `r["view"]` in `step_verify`, or `StepFailed` raised from `_maintenance_url` inside `_report`'s `_connect()`, propagates without writing a report. In `_report`, `build.BuildRefused` and `OSError` are not caught, though `_connect` can raise them. A failed run can then end with no report. The "process boundary" comment is accurate only for the listed types. Either widen `_report`'s handling to match, or make `main` write a best-effort report on any failure.

### [CONCERN] Build step failure leaves `_repair_one` pause/resume unprotected against the pause itself

`set_scheduled(conn, view_jobs, False)` runs before the `try`. If pausing fails part-way across a view's jobs, such as a connection drop after the first `alter_job`, the already-paused jobs are not resumed. The next run's `paused_next_jobs` recovers them, but only on a rerun. Move the pause inside the `try` (resuming idempotently), or document that rerun is the recovery.

### [CONCERN] Missing type annotations and `Any` in the new script code

`_repair_one(..., gran: Any, ...)` should be `Granularity`. `disk_usage: Callable[[str], Any]` in `headroom_gb` is better typed with `shutil._ntuple_diskusage` or a small Protocol. The tests also use untyped helper signatures such as `_load(name) -> Any` and `def _fake_repair(url, grans, **kw)`. The Python rules require strict pyright, which includes `tests`, and these would fail it.

### [CONCERN] `assert` used for runtime control flow in a production script

`assert proc.stdout is not None` is stripped under `python -O`, and it is a runtime guard, not an invariant check. Use an explicit check that raises, or restructure so the type is narrowed without an assert.

### [CONCERN] Raw `os.environ` copy plus settings object disagree on configuration source

The code copies the whole `os.environ` into the child and injects `MT_TIMESCALE_DB_URL` for the owner path. The project rules say not to pass ambient environment wholesale to child processes, and to pass an explicit list of named variables. The child inherits every variable, including any production credentials. Build an env containing only the variables the `mt` CLI needs (PATH, HOME, the DB URL variables, etc.).

### [CONCERN] `CAGG_NEXT_SUFFIX` test hard-codes the string "no next target"

The tests match the message text `"no next target"`. This ties them to the wording of the error that `family_view` builds from `CaggTarget.NEXT.value`. Build the expected text from `CaggTarget.NEXT.value` to follow the single-definition rule.

### [CONCERN] Duplicated and stale 4h-view derivations

`BUILD_ORDER` re-derives `_next` view names with `target_view_name(GRANULARITY_SOURCE[g], CaggTarget.NEXT)`, a derivation that `eligible_rollups_sql.py` also performs (`NEXT_ROLLUP_VIEWS`, `NEXT_COVERAGE_PARENT`, `_rollup_refresh_policy_sql`). Use `cagg_view(g, CaggTarget.NEXT)` from `cagg_parity`, which already encapsulates this, or a single shared helper.

### [CONCERN] Raw SQL in `row_counts` interpolates view names via f-string

The names come from module constants, so injection is not practical. The `# noqa: S608` documents the exception, but the project rule is to never f-string SQL. Use `psycopg.sql.Identifier` to make the safety structural, as the neighbouring code could do too (`coverage_rebuild._source_bounds`).

### [NOTE] `scripts/cutover_172_eligible_rollups.py` exceeds the preferred module size and mixes concerns

At about 350 lines it is above the ~300-line guideline, though the build helpers were already split out. It is a one-shot cutover script, so the cost is low.

### [NOTE] Import ordering workarounds with `# noqa: E402`

The `sys.path` insertion and the E402-suppressed imports follow the existing `cutover_*` script pattern, so this is consistent with the project, if not ideal.

### [NOTE] Test coverage is strong

The migration has catalog, partial-rerun and value-level tests, including real off-tape fixture days. `--target`/`--order` are covered at the CLI, the unit level and against real caggs, and the byte-for-byte check on the extracted 051 SQL guards the refactor. The production-DB rules are respected: the fixtures use throwaway databases.

### [NOTE] Magic fallback in `row_counts`

`int(row["n"]) if row else 0` silently turns a missing row into 0. `count(*)` always returns a row, so the branch is dead. It conflicts with the "no silent fallback values" rule, so raise instead.

### Run Digest

- Response length: 6777 chars
- Response is newline-free: no
- Tool calls made: 0
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 1
- Tokens — prompt / cached / cache write / completion / reasoning: 80990 / 3232 / 77756 / 2584 / not computed
- Duration: 21.7 s
- Cost: $0.34 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 14
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 14
- Finding-shaped matches — surviving validation: 14
