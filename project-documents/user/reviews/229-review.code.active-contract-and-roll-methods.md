---
docType: review
layer: project
reviewType: code
slice: active-contract-and-roll-methods
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/229-slice.active-contract-and-roll-methods.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261012
dateUpdated: 20261012
reviewedSha: 634668ea2ac987b3e215289be1e459383343fec4
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 0
diffTruncated: false
durationSeconds: 18.3
squadronVersion: 0.21.5
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "Roll lines in `status` can be swallowed as `error` while the broad failure catch hides unexpected causes"
    location: "src/manta_trading/data/tick/active_contract.py:latest_active"
  - id: F002
    severity: concern
    category: design
    summary: "Two near-identical closures duplicate setup in `tick_active`"
    location: "src/manta_trading/cli/commands/tick_active_cmds.py:129-150"
  - id: F003
    severity: concern
    category: design
    summary: "Private-to-public renames widen the API of `tick_store_cmds`"
    location: "src/manta_trading/cli/commands/tick_store_cmds.py:55-60"
  - id: F004
    severity: concern
    category: dry
    summary: "Duplicate `_EXTEND` constant and magic string"
    location: "src/manta_trading/data/tick/roll_resolve.py:30"
  - id: F005
    severity: concern
    category: testing
    summary: "Tests monkeypatch module-level constants that the code reads at call time, which makes them fragile"
    location: "test/integration/data/test_tick_active_cli.py:153"
  - id: F006
    severity: concern
    category: async
    summary: "Blocking synchronous work in the async path is not guaranteed to be under 1 ms"
    location: "src/manta_trading/data/tick/active_contract.py:_inputs"
  - id: F007
    severity: concern
    category: typing
    summary: "Recording script uses `assert`-free but loosely typed `Any` and relies on `__doc__` being non-None"
    location: "scripts/record_229_roll_fixture.py:main"
  - id: F008
    severity: note
    category: style
    summary: "`assert isinstance(...)` used for type narrowing in production code"
    location: "src/manta_trading/data/tick/roll_resolve.py:196"
  - id: F009
    severity: note
    category: design
    summary: "Sound design choices"
    location: "src/manta_trading/data/tick/active_contract.py"
---

# Review: code — slice 229

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Roll lines in `status` can be swallowed as `error` while the broad failure catch hides unexpected causes

`latest_active` catches `psycopg.OperationalError`, `TickCalendarError`, `TickPreflightError` and `RollInputError`, and returns them as `error=str(exc)` on every line. The inline comment justifies this, and the exception types are specific, so it fits rule (b). It does not log, though. For a status command this is acceptable, but a `logger.warning` or `logger.exception` would keep the failure diagnosable in non-JSON runs. `psycopg.errors.QueryCanceled` is a subclass of `OperationalError`, so the timeout test passes. Other `psycopg.Error` subclasses, such as `InterfaceError` after a dropped connection, propagate and fail the whole status command. That is inconsistent with the "never raises" docstring.

### [CONCERN] Two near-identical closures duplicate setup in `tick_active`

`schedules()` and `lines()` both call `universe_entries(product)` and `_urls(settings)` before diverging. This breaks the DRY rule in CLAUDE.md. The function also asserts `span is not None` inside the closure, which is a type-narrowing workaround. Two small module-level async functions that take `span` as a parameter would be cleaner and easier to test.

### [CONCERN] Private-to-public renames widen the API of `tick_store_cmds`

`_JSON_OPTION`, `_PRODUCT_OPTION`, `_DATE_FORMAT`, `_entries` and `_read_storage` became public so that `tick_active_cmds` could import them. That is reasonable. However, `tick_active_cmds.py` also defines its own `_START_OPTION` and `_END_OPTION`, which duplicate the ones in `tick_store_cmds` apart from `...` versus `None` defaults. This is acceptable, but the shared option definitions would be better placed in a common module.

### [CONCERN] Duplicate `_EXTEND` constant and magic string

`_EXTEND = "run mt data extend"` is defined in both `roll_resolve.py` and `active_contract.py`. CLAUDE.md requires a single definition for shared values. Define it once, for example in `roll_types.py`, and import it. The `tick_active_cmds` epilog also hard-codes the same remedy text.

### [CONCERN] Tests monkeypatch module-level constants that the code reads at call time, which makes them fragile

`monkeypatch.setattr(active_contract, "TICK_ROLL_STATEMENT_TIMEOUT_SECONDS", 1)` works only because `roll_conninfo` reads the module global at call time. Importing it by name means any refactor that captures the value earlier silently breaks the timeout tests. It is also still correct today. Passing the timeout as a parameter would remove this coupling.

### [CONCERN] Blocking synchronous work in the async path is not guaranteed to be under 1 ms

The calendar read and resolution correctly run in `asyncio.to_thread`. However, `_Resolver.__init__` in `roll_resolve.py` indexes all volumes, and `RollSchedule.segments()` runs on the loop, called from `print_schedules`, which is in the CLI after the loop finishes. For the 1,300-session cap this is probably fine. `_lines` also runs in a thread. Nothing in the diff shows a load test, though. The Python rules require one for code on the simulation, network and concurrency paths.

### [CONCERN] Recording script uses `assert`-free but loosely typed `Any` and relies on `__doc__` being non-None

`__doc__.splitlines()[0]` fails type checking under pyright strict, because `__doc__` is `str | None`. The script uses `Any` widely for envelopes. It is manual, developer-run only and read-only, so the risk is low. It also relies on `Settings()` for secrets, which is correct.

### [NOTE] `assert isinstance(...)` used for type narrowing in production code

Several `assert isinstance(answer, ActiveContract)` statements narrow types in production code. They are stripped under `python -O`. Here they only narrow types after `_differs` has already checked, so a restructured `_differs` that returns the narrowed value would remove the need for them.

### [NOTE] Sound design choices

The roll path uses its own bounded, read-only, `REPEATABLE READ` connections, queries are parameterized, and failures are explicit rather than silent fallbacks. The pure modules are kept pure, and a test enforces that import direction. Provider equivalence is pinned against recorded real data. This meets the project's no-silent-fallback and DB-safety rules.

### Run Digest

- Response length: 5063 chars
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
- Duration: 18.3 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9

## Resolution (2026-10-12)

- **F001, declined.** `OperationalError` is TD9's failure contract: an unreachable host, `QueryCanceled` and a lost peer all raise it, and `exit_code_for` maps the same class. `InterfaceError` comes only from reusing a closed connection, which is a code defect and should propagate. The failure is not hidden: it prints as the line's `error:` and `status` exits 3.
- **F002, fixed.** `tick_active` has no closures now: `_targets` (products, migration check, URLs) is shared by the module-level `_schedules` and `_latest`, and the `assert span is not None` is gone.
- **F003, declined.** `active`'s `--start/--end` are optional together, while `coverage`'s are required. They differ in default and help, so they are not duplicates.
- **F004, fixed.** `EXTEND_REMEDY` is defined once in `roll_types.py` and imported by `roll_resolve` and `active_contract`. CLI epilogs stay prose.
- **F005, declined.** Patching a module global that is read at call time is the ordinary seam. If a refactor captured the value earlier, the tests would wait for the real 30 s timeout. They would not pass silently.
- **F006, declined.** Resolution, including `_Resolver.__init__`, runs in `asyncio.to_thread`. `segments()` runs after the event loop has ended. A full held-range schedule was measured at 0.05 s (`user/notes/229-walkthrough.md`).
- **F007, declined.** mypy is clean on the script. It uses the same `__doc__` pattern as `record_databento_fixtures.py`, and `Any` is only on JSON envelopes.
- **F008, fixed.** `_changed` returns the differing contract or `None`, so the forecasts need no `assert isinstance`.
