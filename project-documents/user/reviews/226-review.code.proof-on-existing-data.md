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
reviewedSha: fdc2a89859241d9ecae72cc711c3a1b39cba3407
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 1
diffTruncated: false
durationSeconds: 34.5
squadronVersion: 0.18.2
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "Tick-loop thread can die silently or outlive `stop()`"
    location: "scripts/proof_226/contention.py:97-125"
  - id: F002
    severity: concern
    category: design
    summary: "SQL for Q5 is built with string `.replace` hacks"
    location: "scripts/proof_226/queries.py:36-55"
  - id: F003
    severity: concern
    category: design
    summary: "Harness reaches into private names across modules"
    location: "scripts/proof_226/layouts.py:80-86"
  - id: F004
    severity: concern
    category: design
    summary: "Guard literals repeat values defined as constants"
    location: "scripts/proof_226/contention_run.py:119-129"
  - id: F005
    severity: concern
    category: error-handling
    summary: "`.env` password lookup accepts one URL scheme while the production-URL regex accepts both"
    location: "scripts/provision_tick_cluster.sh:268"
  - id: F006
    severity: concern
    category: correctness
    summary: "`verdict` in batch only reports a faster budget"
    location: "scripts/proof_226/batch.py:77-92"
  - id: F007
    severity: note
    category: design
    summary: "Constant patching in `ingest_runs` is safe for the names used"
    location: "scripts/proof_226/ingest_runs.py:38"
  - id: F008
    severity: note
    category: security
    summary: "Shell helpers and provisioning script look sound"
    location: "deploy/lib/env_add_keys.sh"
  - id: F009
    severity: note
    category: testing
    summary: "Header-refusal handling is consistent and well covered"
    location: "src/manta_trading/data/tick/file_days.py"
---

# Review: code — slice 226

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Tick-loop thread can die silently or outlive `stop()`

`TickLoop._loop` runs in a daemon thread with no exception handling. If `reset(self._urls)` raises, the thread dies and nothing reports it. The run then carries on with no load but still labels its firings "overlapped", which would give a false "no contention" verdict. Ingest subprocess output goes to `DEVNULL` and the exit code is never checked. `iterations` counts failed ingests as successes, so a loop that fails instantly looks healthy. There is also a race in `stop()`. If `_stop` is set while `reset()` is running, `_loop` still reaches `Popen`. `stop()` saw `_proc is None` and sent no SIGINT, so `join()` blocks until a full ingest finishes. Capture the thread's exception and the ingest exit codes, and surface them in the report or as a trip. Re-check `_stop` right before `Popen`.

### [CONCERN] SQL for Q5 is built with string `.replace` hacks

`_BARS` has `{minute}` substituted by `.replace`. Q5 is derived by `.replace("%(lo)s", "%(month_lo)s")` on that string. This is the kind of fragile string patching CLAUDE.md forbids. Several `# type: ignore` markers are also used to get past `LiteralString`. Define Q2 and Q5 as two explicit statements, or use one template with named bounds. Under strict pyright, `# type: ignore` should carry a rule code.

### [CONCERN] Harness reaches into private names across modules

`layouts.py` uses `size._query`, `size._ROWS_BY_CHUNK` and `size._CHUNK_BYTES`. `ingest_runs.py` imports `_ingest` from `tick_store_cmds`. `test_proof_226_steps.py` also uses the `size._*` names. Make the shared pieces public. For `_ingest`, expose a supported entry point, since the harness's results depend on its behaviour.

### [CONCERN] Guard literals repeat values defined as constants

The messages "beyond 75 min" and "2× its weekly maximum" restate `TIMER_HORIZON` and `KALSHI_TRIP_SECONDS`. `contention_report.py` hardcodes "13,083 symbols" in `NOT_A_CLEARANCE`. Format these from the constants. `_refuse` always raises, so annotate it `-> NoReturn`. That removes the `assert elapse is not None` that follows it.

### [CONCERN] `.env` password lookup accepts one URL scheme while the production-URL regex accepts both

`env_password` matches only `^postgresql://`. `production_ts_version` accepts `postgres(ql)://`. An existing `postgres://` URL in `.env` would be treated as having no password, and a new password would be generated and applied to the role. That would silently diverge from the stored URL, because `env_add_keys` keeps existing keys. Use one shared pattern.

### [CONCERN] `verdict` in batch only reports a faster budget

The module docstring says to change the budget if another one "moves the units' time by more than 10 %". The code also requires `took < seconds[current]`, so a slower budget never counts. The behaviour is reasonable, but the docs and code disagree. Align them. Also, `budget_memory` fits a line through three points, and `verdict` indexes `seconds[current]`. Nothing checks that `TICK_DECODE_BATCH_BYTES` is one of `BUDGETS`, so a changed constant would raise a bare `KeyError`.

### [NOTE] Constant patching in `ingest_runs` is safe for the names used

`patched` mutates `constants.<name>`. I checked that `dbn_file.py:154` and `tick_store_cmds.py:116` read these through the module at call time, so the patching works. Any future by-name `from ... import` of these constants would silently defeat it.

### [NOTE] Shell helpers and provisioning script look sound

Atomic write with a 0600 temporary file and a RETURN trap cleanup looks correct. Passwords go through stdin or `PGPASSWORD`, never argv, and `log_scrub` prints line numbers only. The committed provision log contains no credentials. The unit tests cover the failure, idempotence and mode cases.

### [NOTE] Header-refusal handling is consistent and well covered

`file_days` is shared by adopt and delivery. Refusals are typed as `TickFileDecodeError` and tested for every bad-header kind, against both v1 and v3 files. The `@destructive` guard is registered and tested for every guarded function.

### Run Digest

- Response length: 4734 chars
- Response is newline-free: no
- Tool calls made: 1
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 34.5 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
