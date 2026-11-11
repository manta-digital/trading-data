---
docType: review
layer: project
reviewType: tasks
slice: tick-bars-and-compact-tick-serving
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: FAIL
verdictSource: stated
sourceDocument: project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md
aiModel: claude-haiku-5-5
aiProfile: sdk
status: complete
dateCreated: 20261111
dateUpdated: 20261111
reviewedSha: 929e967ae42e1e29519d668977f4339b4343fc20
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 29
effort: xhigh
turns: 30
promptTokens: 1178985
cachedTokens: 1010383
cacheWriteTokens: 168584
completionTokens: 53059
durationSeconds: 228.3
costUsd: 0.340866
costBasis: provider
runId: run-20261111-p5-c058f9f9
squadronVersion: 0.24.26
findings:
  - id: F001
    severity: fail
    category: requirement-coverage
    summary: "Success criterion 12 (Phase 7 measurement) has no task"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:50-52"
  - id: F002
    severity: fail
    category: nfr-coverage
    summary: "Slice-restated performance NFRs have no load test or CI gate"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md"
  - id: F003
    severity: concern
    category: design-consistency
    summary: "Reader duplicates the `stream_rows` loop, departing from D3"
    location: "src/manta_trading/data/tick/tick_reads.py:107-117"
  - id: F004
    severity: concern
    category: clarity
    summary: "Disconnect and window-identity error paths are left open"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:330-336"
  - id: F005
    severity: concern
    category: dry
    summary: "The shared response envelope has no home, so the models will be copied"
    location: "src/manta_trading/api_server/models/futures.py:208"
  - id: F006
    severity: concern
    category: proportionality
    summary: "The file is heavy, and tests repeat across layers"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md"
  - id: F007
    severity: note
    category: clarity
    summary: "The oracle task points at an integration-tier helper"
    location: "test/integration/data/test_tick_bar_helpers.py"
  - id: F008
    severity: note
    category: performance
    summary: "The until-stop check builds a bar object for every trade"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:210-214"
  - id: F009
    severity: note
    category: accuracy
    summary: "`futures.py` is stated as 274 lines"
    location: "src/manta_trading/api_server/models/futures.py"
  - id: F010
    severity: note
    category: commit-cadence
    summary: "Group 4 has no commit marker"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md:283-309"
  - id: F011
    severity: pass
    category: requirement-coverage
    summary: "Criteria 1–11 are covered, and the prerequisites hold"
    location: "project-documents/user/tasks/308-tasks.tick-bars-and-compact-tick-serving.md"
---

# Review: tasks — slice 308

**Verdict:** FAIL
**Model:** claude-haiku-5-5

## Findings

### [FAIL] Success criterion 12 (Phase 7 measurement) has no task

Criterion 12 requires the Phase 7 measurement at 95–100% of the trade limit, recorded in `user/notes/308-tick-bar-measurement.md`. Lines 50–52 exclude it and defer it to issue #75. I could not verify #75. No planning document mentions it, and the slice design has no #75 reference. The deferral leaves the criterion and its notes file with no owner. CLAUDE.md treats production runs as agent work. Fix: add a final task group that runs after the release commit. It should pick START/END from `mt data tick coverage --json` per D9, time the N=1000 request against 10 s, run the concurrent `/products` probe within 100 ms of idle, write the notes file, and commit. If the work is meant to live in #75, the design must say so and the issue must exist.

### [FAIL] Slice-restated performance NFRs have no load test or CI gate

The slice restates NFRs: the request completes in under 10 s at the trade limit (D9), `/products` answers within 100 ms during that request (D9, D10), and the wire cost is about 10 gzipped bytes per tick. The breakdown adds no load test. The repo's load tier is `test/load/`, not `tests/load/` as the Python rules say. Precedent exists in `test/load/test_231_futures_api_nfr.py`. The Python rules require a load test for code on concurrency paths, and CI must gate it. D10's executor hand-off and pool checkout are such paths. `.github/` contains no reference to `load`, so the breakdown also needs a CI wiring task that names the gate. I did not establish how the existing load tests are gated. Fix: add a task group that writes `test/load/test_308_tick_bar_nfr.py`, asserting a latency bound at a stated realistic configuration and `/products` responsiveness under concurrent tick-bar reads, plus a CI wiring task. The held-ES measurement stays in the Phase 7 group.

### [CONCERN] Reader duplicates the `stream_rows` loop, departing from D3

Task 3.2 (lines 196–200) writes its own named-cursor loop, because `stream_rows` takes a synchronous `on_batch` and cannot await `stop` or an executor push. That reasoning is correct. But D3 and Interfaces Required (design lines 197 and 80) say the reader runs through `stream_rows`, and the second reader is meant to depend on that named contract. The result is two copies of the cursor and fetch loop, against the DRY rule, and the design is not amended. Fix: extend `stream_rows` with an optional awaited pre-fetch check and an executor-run batch handler, so `read_ticks` and `read_tick_bars` share one loop. Or amend D3 and Interfaces Required in the same commit as the decision.

### [CONCERN] Disconnect and window-identity error paths are left open

Task 5.1 says a disconnect "ends the request without a response and without logging an error", with no mechanism given. An uncaught `ReadAbandoned` reaches Starlette's server-error middleware, which logs it as an error. The route has to catch `ReadAbandoned` explicitly, return a response, and carry a comment justifying the swallow, per the exception rule. The window identity may sit "on the exception or the reader's context", which leaves a choice open. Fix: name the catch, the response, and where the identity is carried. Add a test asserting that a disconnect logs no ERROR record. Task 5.6 checks only the pool.

### [CONCERN] The shared response envelope has no home, so the models will be copied

Task 4.2 (lines 298–300) builds `FuturesTickBarsResponse` "the way `FuturesTicksResponse` builds the shared envelope (reuse, do not copy)". No task extracts that envelope. Today it is built inline where `"ticks": _records(TickRecord, TICK_COLUMNS, rows)` sits. Task 6.2 then rewrites `FuturesTicksResponse`. A junior will copy the envelope fields unless the breakdown names a base. Fix: in 4.2, extract a base model such as `FuturesSeriesEnvelope` holding `target` through `count`. Have `FuturesTickBarsResponse` and, in 6.2, the columnar `FuturesTicksResponse` subclass it.

### [CONCERN] The file is heavy, and tests repeat across layers

The file runs about 500 lines, roughly 444 non-empty. For comparison, 306's task file has about 205 non-empty lines and 302's about 250. Much of the weight is design prose restated as steps: the D2 table in 2.2 and 2.3, the D5 error string in 3.5 and 5.1, and the D3/D10 loop mechanics in 3.2. Behaviours are also tested twice:
- the sentinel-price case (3.3 at lines 234–235, 5.6 at lines 374–376);
- the stop-and-release pool check (3.3 at lines 232–233, 5.6 at lines 377–378);
- the columnar round trip (6.3 at lines 421–423, 6.5 at lines 437–438).

What to cut: point to the design's D-sections instead of restating them. Keep the sentinel and pool-release tests at the reader layer. Keep route-level tests to HTTP mapping only (422, 500 with the log, 503). Keep the round trip in 6.5 as a route-level check that uses the 6.3 decoder.

### [NOTE] The oracle task points at an integration-tier helper

Task 2.1 (lines 104–105) says to read the decode pattern in `test_dbn_file.py` and `test_tick_bar_helpers.py`. The second file exists only at `test/integration/data/`, not beside the oracle in `test/unit/data/tick/`. Reusing it from a unit module crosses the tier boundary. Fix: give the real path, and lift any shared decoder into `test/tick_support/`.

### [NOTE] The until-stop check builds a bar object for every trade

Task 3.2 copies D3's condition "if `cutter.current()` is `None`". `current()` builds a `TickBar` on every call, and this check runs once per trade on the path D9 measures against 10 s. Specify a cheap open-bar check on the cutter, such as a boolean property. The load test from the second finding should catch any regression.

### [NOTE] `futures.py` is stated as 274 lines

Task 4.2 (line 291) and the design say 274 lines. Counting non-empty lines gives at least 288. The conditional in 4.2 still holds, but the number is stale. Put the new classes in `models/futures_tick_bars.py` directly, since adding them would take the file past 300.

### [NOTE] Group 4 has no commit marker

Group 4 has no `**Commit**:` marker. Its models are committed with group 5's marker at line 384. That is acceptable, since the group 5 message names both. If the models are tested and committable before the routes exist, as the 4.3 unit tests suggest, add a marker to group 4.

### [PASS] Criteria 1–11 are covered, and the prerequisites hold

Mapping: 1→2.4; 2→2.4, 5.4; 3→2.4, 5.6; 4→4.3, 5.3; 5→3.3, 5.3; 6→3.7, 5.5; 7→6.3, 6.5; 8→2.5, 7.7; 9→each run-the-area step and 7.7; 10→7.1–7.5; 11→7.6. No task falls outside the slice. Sequencing is valid: groups run 1→2→3→4→5→6→7 with no back-references, and each implementation group is followed by its tests. Prerequisites check out:
- 306 is merged (fast-forward in `.git/logs/HEAD`);
- `verdicts_for` exists at `tick_status_scope.py:53`;
- `contract_plan` and `product_plan` take `family` (`get_plan.py:233`, `280`);
- `stream_rows` is public (`tick_reads.py:107`);
- the `UNDEF_INT64` uses at `constants.py:302-303` match task 1.3;
- `TICK_PRICE_SCALE`, `price_text` and `ts_text` exist;
- both README rows sit at lines 167 and 818;
- `reference.md` has §6.8 as the append point.

The 300 architecture already carries the partial-bar rule at line 78, so D2's amendment is in place.

### Run Digest

- Response length: 10417 chars
- Response is newline-free: no
- Tool calls made: 29
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: xhigh
- Turns: 30
- Tokens — prompt / cached / cache write / completion / reasoning: 1178985 / 1010383 / 168584 / 53059 / not computed
- Duration: 228.3 s
- Cost: $0.34 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 11
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 11
- Finding-shaped matches — surviving validation: 11
