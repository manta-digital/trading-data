---
docType: review
layer: project
reviewType: code
slice: tick-restore-drill
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/228-slice.tick-restore-drill.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20261009
dateUpdated: 20261009
reviewedSha: cc2e7ae1591df4f943bf8917a5f1e58c56942112
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
diffTruncated: false
durationSeconds: 53.4
squadronVersion: 0.21.2
findings:
  - id: F001
    severity: concern
    category: security
    summary: "Rebuild subprocess inherits the full environment and runs in the checkout that holds the production `.env`"
    location: "scripts/drill_228_context.py:rebuild_env"
  - id: F002
    severity: concern
    category: correctness
    summary: "The `tick_trade` fingerprint does not pin session text settings"
    location: "scripts/drill_228_fingerprint.py:fingerprint"
  - id: F003
    severity: concern
    category: naming
    summary: "Local `sql` shadows the imported `psycopg.sql` module"
    location: "scripts/drill_228_steps.py:step_switch"
  - id: F004
    severity: concern
    category: typing
    summary: "Test code would not pass strict pyright"
    location: "test/unit/test_drill_228_host.py:80"
  - id: F005
    severity: concern
    category: duplication
    summary: "The \"how many failures to show\" limit is defined in two places"
    location: "scripts/drill_228_steps.py:step_compare_production"
  - id: F006
    severity: note
    category: error-handling
    summary: "Runtime invariants are written as `assert`"
    location: "scripts/drill_228_steps.py:step_restore_archive"
  - id: F007
    severity: note
    category: testing
    summary: "`LIKE '%%unit_id'` works only by accident"
    location: "test/integration/data/test_drill_228_bookkeeping_pins.py:60"
  - id: F008
    severity: note
    category: correctness
    summary: "`backed_up_set` and `parse_restic_ls` may count symlinks differently"
    location: "scripts/drill_228_host.py:backed_up_set"
  - id: F009
    severity: note
    category: correctness
    summary: "Report date and drill stamp use different clocks"
    location: "scripts/drill_tick_restore.py:report_path"
  - id: F010
    severity: pass
    category: uncategorized
    summary: "Destructive-path safety, error handling and parser testing"
    location: "scripts/drill_228_lifecycle.py"
---

# Review: code — slice 228

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Rebuild subprocess inherits the full environment and runs in the checkout that holds the production `.env`

`rebuild_env` returns `os.environ | {...}` and `_run_mt` runs `mt` with `cwd=ctx.checkout`. `mt` therefore sees the whole ambient environment, and its settings loader can read the production `.env` from that directory. Only four variables are overridden: the two tick URLs, the archive directory, and the read-only calendar URL. Any other production credential or URL, such as the Timescale maintenance URL or provider API keys, stays reachable by the commands that run `migrate apply`, `tick pass` and `tick ingest`.

The project rules say never to inject a whole `.env` into a child process and to pass an explicit list of named variables. Build the child environment from a named allow-list (PATH, HOME, the uv variables, plus the four overrides). Alternatively, assert that no other `MT_*_URL` points at production.

### [CONCERN] The `tick_trade` fingerprint does not pin session text settings

`table_md5` calls `pin_text_settings` because production runs in America/Denver and the scratch server in GMT, and `fingerprint` runs the same kind of `ROW(...)::text` hash on both. It never pins those settings. I could not confirm which column types `tick_trade` has. `ts_event` is integer nanoseconds. If any hashed column is a timestamp, date or float type, the md5 depends on the session's `TimeZone`, `DateStyle` or `extra_float_digits`. The step 5 comparison against production would then show false differences, or the drill would pass or fail for the wrong reason. Call `pin_text_settings` inside `fingerprint`, and add `extra_float_digits` to `TEXT_SETTINGS` if floats are involved.

### [CONCERN] Local `sql` shadows the imported `psycopg.sql` module

`step_switch` assigns `sql = "SELECT pg_walfile_name(...)"`, while the module imports `from psycopg import sql` and uses it in `_row_counts`. This works today, but a later edit that composes a query inside `step_switch` would hit the wrong object. Rename the local variable, for example `query`.

### [CONCERN] Test code would not pass strict pyright

The Python rules include tests in strict pyright and make zero errors a merge blocker. Several test signatures break that. `excludes: list` and `files: list` are missing type arguments, and `_pg_ctl` has no return annotation. `seeded: Any` and `pair: Any` appear across the integration tests. `_public(conn: psycopg.Connection, ...)` in `test_drill_228_bookkeeping_pins.py:25` leaves out the type parameter. `list[re.Pattern[str]]` and `psycopg.Connection[Any]` would fix most of these.

### [CONCERN] The "how many failures to show" limit is defined in two places

Step 5 slices `diffs[:5]`, while `drill_228_rebuild.py` uses `_SHOWN = 10` for the same purpose. Define the limit once, for example in `drill_228_context`, as the project rule on scattered values requires. `READ_ONLY` also exists in `drill_228_context` and as the `options` string pattern in the tests.

### [NOTE] Runtime invariants are written as `assert`

`assert ctx.live_set is not None`, `assert ctx.base is not None` and `assert row is not None` in `table_md5` are stripped under `python -O`. The rest of the module raises `StepFailed` for the same kind of precondition, and `need_drill()` already shows the pattern. Use explicit `StepFailed` checks for consistency.

### [NOTE] `LIKE '%%unit_id'` works only by accident

The query is executed without parameters, so psycopg does not collapse `%%` to `%`. Two consecutive wildcards match the same rows as one, so the test passes. The WHERE clause also mixes `AND` and `OR` without grouping the whole condition. Use a single `%` and parenthesize the condition.

### [NOTE] `backed_up_set` and `parse_restic_ls` may count symlinks differently

`os.walk` counts symlinked files, but restic lists them as `type: "symlink"`, and the parser counts only `type == "file"`. An archive containing a symlink would fail step 1 and step 3 with a count mismatch. Archive job directories probably hold no symlinks, so it's unlikely to matter. A comment, or a skip of symlinks on both sides, would make the assumption explicit.

### [NOTE] Report date and drill stamp use different clocks

The drill directory stamp uses UTC, while the report filename uses `datetime.now().date()` in local time. A run near midnight can give names that disagree by a day. This is cosmetic.

### [PASS] Destructive-path safety, error handling and parser testing

`remove_drill_dir` and `stop_scratch_server` act only on a resolved, marked, direct child of the drill root, and the tests cover unmarked, `..`, symlink and inside-directory attempts. `STEP_ERRORS` is a named tuple with a process-boundary comment on the single catch. `BLE001` is not tripped, and cleanup and the report run even when a step fails. The parsers are tested against captured real restic and recovery-log output, which satisfies the project's parsing rule. The fingerprint test also covers automatic column inclusion.

### Run Digest

- Response length: 6076 chars
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
- Duration: 53.4 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
