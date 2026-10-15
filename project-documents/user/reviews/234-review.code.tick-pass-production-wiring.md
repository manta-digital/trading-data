---
docType: review
layer: project
reviewType: code
slice: tick-pass-production-wiring
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, shell.md, sql.md, testing.md]
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/234-slice.tick-pass-production-wiring.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261015
dateUpdated: 20261015
reviewedSha: 75d911b0258c7fc3a9e2561d8842ff75b116b15f
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 0
diffTruncated: false
durationSeconds: 20.9
squadronVersion: 0.21.9
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "Cutover step 2 mutates production archive and uses the sampler thread without error handling"
    location: "scripts/cutover_234_host.py:150-171"
  - id: F002
    severity: concern
    category: error-handling
    summary: "`assert` used for runtime validation in host code"
    location: "scripts/cutover_234_host.py:78"
  - id: F003
    severity: concern
    category: security
    summary: "`step_fire` reads the production DB URL name and reuses ambient state loosely; `.env` read for maintenance URL"
    location: "scripts/cutover_234_tick_wiring.py:112-117"
  - id: F004
    severity: concern
    category: error-handling
    summary: "Exception handling in `run_steps` catches `KeyError`/`StopIteration`/`ValueError` broadly"
    location: "scripts/cutover_234_tick_wiring.py:241-262"
  - id: F005
    severity: concern
    category: concurrency
    summary: "Tick run: heartbeat `progress` runs on worker threads against a synchronous recorder with no error handling"
    location: "src/manta_trading/cli/tick_run.py:117-126"
  - id: F006
    severity: concern
    category: error-handling
    summary: "Duplicate `Exception` catch at tick_run boundary lacks narrowing"
    location: "src/manta_trading/cli/tick_run.py:173-178"
  - id: F007
    severity: concern
    category: typing
    summary: "`tick_run_cmds._Reported` and `_print_end` use `Any`, weakening strict typing"
    location: "src/manta_trading/cli/commands/tick_run_cmds.py:44-62"
  - id: F008
    severity: concern
    category: security
    summary: "Shell script test hook allows privilege-prefix injection through environment"
    location: "deploy/add-tick-env.sh:21-35"
  - id: F009
    severity: concern
    category: correctness
    summary: "Cutover journal-cursor change alters shared helper semantics"
    location: "scripts/cutover_common.py:200"
  - id: F010
    severity: note
    category: testing
    summary: "Rule compliance and test coverage are good"
    location: "test/unit/cli/test_tick_run.py"
  - id: F011
    severity: note
    category: style
    summary: "Magic values and `sed` in `mt-run`"
    location: "deploy/mt-run:24"
---

# Review: code — slice 234

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Cutover step 2 mutates production archive and uses the sampler thread without error handling

`CgroupSampler._loop` runs on a daemon thread and calls `slice_cgroup()`, `_read` and `oom_kills()`. These can raise `OSError`, `ValueError` or `CalledProcessError`. An exception kills the thread silently. `memory_max` then stays `None` and the step fails later with the misleading message "memory.max ≠ MemoryMax". Catch the specific exceptions and record them on the sampler, or re-raise from `stop()`. This matches the project rule that failures must be explicit. `stop()` also calls `slice_cgroup()` unguarded, so a failure there masks the original step error raised inside the `try`.

### [CONCERN] `assert` used for runtime validation in host code

`assert max_conn and reserved and live` is stripped under `python -O`. It also treats a legitimate `count(*)` of 0 as a failure, because `live` is a row tuple and not the count. A `None` row would then fail with an opaque `AssertionError`. Raise an explicit error for each `None` row.

### [CONCERN] `step_fire` reads the production DB URL name and reuses ambient state loosely; `.env` read for maintenance URL

`step_migrate` reads `MT_TIMESCALE_MAINTENANCE_URL` from the checkout `.env` and opens a connection directly. `migrate()` in `cutover_common` has already verified the target, so this is guarded. However, the URL comes from `.env` through the private helper `_env_value`, which is imported across modules. Make it public. Also confirm that `psycopg` errors on this path cannot echo the URL into the report. `step.seen.append(str(exc))` writes exception text to the report, and `OperationalError` messages can include connection details.

### [CONCERN] Exception handling in `run_steps` catches `KeyError`/`StopIteration`/`ValueError` broadly

The handler is documented as a process boundary. Catching `StopIteration` and `KeyError` can hide programming bugs, such as a bad key in `_tick_last_run`, as step failures with only the message text and no traceback. The handler records `str(exc)` but never calls `logger.exception`, so tracebacks are lost. Log the traceback before the step is marked failed.

### [CONCERN] Tick run: heartbeat `progress` runs on worker threads against a synchronous recorder with no error handling

The heartbeat `report` callback calls `recorder.progress` (synchronous psycopg) directly from ingest worker threads, and `add()` calls it from within a COPY batch loop. A recorder failure on a worker thread would propagate into `ingest_unit`, abort that unit and potentially fail the pass. This contradicts the "best-effort recording" statement in the module docstring. The docstring's claim that "two reports cannot overlap" also depends on the interval spacing. Several workers can pass the interval check at nearly the same time, because `_last` is updated under the lock but the report runs outside it. Two workers can therefore call `progress` concurrently on the same non-thread-safe connection, if the recorder shares one. Verify the recorder's thread safety, or wrap the call to swallow and log recorder errors.

### [CONCERN] Duplicate `Exception` catch at tick_run boundary lacks narrowing

`except Exception` logs with `logger.exception` and re-raises, so it conforms to rule (a). However, `row.close` runs inside the handler. If it raises, the original exception is replaced by the close failure. Guard it so the original error is preserved. `BaseException` subclasses such as `asyncio.CancelledError` and `KeyboardInterrupt` also leave the row open. This matters for a systemd `TimeoutStartSec` kill.

### [CONCERN] `tick_run_cmds._Reported` and `_print_end` use `Any`, weakening strict typing

`Any` is used for the inner phase, the `run()` return value and `print_result`. The Python rules call for strict pyright. Use `PassPhase[TickRun]` and a `Callable[[PassResult, int], None]` for `print_result`. `FakeRecorder` and other test doubles are also passed as `Any`.

### [CONCERN] Shell script test hook allows privilege-prefix injection through environment

`$PRIV` is expanded unquoted and executed as a command prefix. It is documented as a test hook "never set in production". The script runs as root through sudo, though, and sudo normally resets the environment. An inherited `MT_ADD_TICK_ENV_PRIV_PREFIX` or `MT_ADD_TICK_ENV_TARGET` could still redirect a root script if `env_keep` is configured or the script is invoked directly as root. Consider gating the hooks on a single explicit test flag, or confirming that sudoers resets the environment. Also, `exec > >(tee -a "$LOG")` creates the log file under `/var/log` with default permissions. The log holds only key names, so this is low risk.

### [CONCERN] Cutover journal-cursor change alters shared helper semantics

`journal_cursor()` now takes the end of the whole journal rather than the unit's. The `unit` parameter is dropped and the call sites updated. This is sound for `--after-cursor` reads. The 227 and 267 cutover scripts share this helper, though, and the diff adds no regression test for `fire_unit` or `fire_unit_until`. A test that asserts the new argv would lock it in.

### [NOTE] Rule compliance and test coverage are good

Tests accompany every new module. They are parametrized over all outcome pairs and cover the migration rewind fixture, the heartbeat concurrency case and the deploy script, including a check that no secret value reaches any output. The prod-URL guard and DB-safety rules are respected: the integration tests use throwaway databases. No load test was added for the ingest heartbeat or ingest worker path, which touches concurrency. The rules ask for a `tests/load/` test there.

### [NOTE] Magic values and `sed` in `mt-run`

`sed -n '2,12p'` uses hard-coded header line numbers. The next header edit will silently truncate the usage text. The `sed` is a simple substitution, but the line range is fragile.

### Run Digest

- Response length: 6689 chars
- Response is newline-free: no
- Tool calls made: 0
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 20.9 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 11
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 11
- Finding-shaped matches — surviving validation: 11

## Disposition

- F001 fixed: sampler records a read failure; `stop()` re-raises it.
- F002 fixed: explicit `ValueError` replaces the `assert`. (The "count of 0 fails" premise is wrong: `(0,)` is truthy.)
- F003 fixed in part: `_env_value` is now public `env_value`. psycopg connection errors name host, port and user, never the password; no change.
- F004 fixed: `run_steps` prints the traceback before marking the step failed.
- F005 not changed: the premise is wrong. `PassRunRecorder.progress` catches `psycopg.Error` (recording never aborts a pass, Design 922), and the repository opens a connection per statement, so there is no shared connection.
- F006 not changed: a failing `close` chains onto the original exception (`__context__`), it does not hide it. A systemd stop sends SIGTERM, which raises nothing; the next run's sweep closes the row.
- F007 fixed: `PassPhase[TickRun]`, `PhaseReport`, `Callable[[PassResult, int], None]` replace `Any`.
- F008 not changed: sudo's default `env_reset` drops the hook variables; a caller already root needs no hook.
- F009 fixed: test asserts the `journal_cursor` argv.
- F010 not changed: the heartbeat is a lock-guarded counter with a 30 s report spacing; its concurrency case has a unit test.
- F011 fixed: `mt-run` usage prints the header up to the first code line instead of a fixed line range.
