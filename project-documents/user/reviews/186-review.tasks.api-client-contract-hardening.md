---
docType: review
layer: project
reviewType: tasks
slice: api-client-contract-hardening
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/tasks/186-tasks.api-client-contract-hardening.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260803
dateUpdated: 20260803
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "All twelve success criteria have corresponding tasks"
    location: 186-tasks.api-client-contract-hardening.md
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Test-with pattern is respected throughout"
    location: 186-tasks.api-client-contract-hardening.md
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Cross-task dependency on Task 8 → Task 10 is correctly sequenced"
    location: 186-tasks.api-client-contract-hardening.md:Task 8
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Task 13 explicitly carries the SC2 measurement-and-raise protocol"
    location: 186-tasks.api-client-contract-hardening.md:Task 13
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Constants are derived, not literal per-granularity"
    location: 186-tasks.api-client-contract-hardening.md:Task 2
  - id: F006
    severity: pass
    category: uncategorized
    summary: "Diff-against-landed-code framing is preserved through the tasks"
    location: 186-tasks.api-client-contract-hardening.md:Context Summary
  - id: F007
    severity: pass
    category: uncategorized
    summary: "Sequencing: no circular dependencies; commit checkpoints distributed"
    location: 186-tasks.api-client-contract-hardening.md:Task 14
  - id: F008
    severity: pass
    category: uncategorized
    summary: "No scope creep detected against the slice's Excluded list"
    location: 186-slice.api-client-contract-hardening.md:Technical Scope
  - id: F009
    severity: pass
    category: uncategorized
    summary: "SC11 (auth/CORS posture) is satisfied by the design, not by a task"
    location: 186-slice.api-client-contract-hardening.md:D8
  - id: F010
    severity: concern
    category: scope-clarity
    summary: "Task 13's \"Rewrite the walkthrough in the design\" mixes phase discipline"
    location: 186-tasks.api-client-contract-hardening.md:Task 13
  - id: F011
    severity: concern
    category: test-coverage
    summary: "SC8 has no test asserting that the freshness probe's internal timeout cannot reach the 504 handler"
    location: 186-tasks.api-client-contract-hardening.md:Task 10
  - id: F012
    severity: concern
    category: test-coverage
    summary: "Task 8's empty-window branch holds a connection for a primary-key seek — no assertion that it doesn't on the non-empty path under all granularities"
    location: 186-tasks.api-client-contract-hardening.md:Task 8
  - id: F013
    severity: note
    category: scope-clarity
    summary: "Task 6 says \"no literal `'512MB'` or `'300s'` remains in `app.py`\" but the slice design keeps them as the *defaults* in constants"
    location: 186-tasks.api-client-contract-hardening.md:Task 6
  - id: F014
    severity: note
    category: nfr-coverage
    summary: "No NFR / load-test NFR restated in the slice; no `tests/load/` task required"
    location: 186-slice.api-client-contract-hardening.md:Success Criteria
  - id: F015
    severity: note
    category: ci-coverage
    summary: "No CI wiring task required because CI is publish-on-tag only"
    location: 186-slice.api-client-contract-hardening.md:D7
  - id: F016
    severity: pass
    category: uncategorized
    summary: "Effort estimates are realistic for a junior AI; no task is too large or too granular"
    location: 186-tasks.api-client-contract-hardening.md
---

# Review: tasks — slice 186

**Verdict:** PASS
**Model:** minimax/minimax-m3

## Findings

### [PASS] All twelve success criteria have corresponding tasks

Every numbered success criterion from the slice design traces to at least one task. SC1 spans Tasks 2/5/6/13 (constants, plumbing, lifespan wiring, prod verification); SC2 maps to Task 13 step 3 (the measurement-driven timeout raise); SC3 is covered by Tasks 4 and 11; SC4–SC7 are each covered by dedicated implementation + test pairs in Tasks 7–8; SC8 spans Tasks 8 and 10 (the QueryCanceled test in Task 8 is correctly sequenced ahead of the handler in Task 10 via xfail); SC9 is Task 9; SC10 is Tasks 10/11/12; SC12 is Task 14.

### [PASS] Test-with pattern is respected throughout

Each implementation task is paired with its test task before the next implementation task begins: Task 2 (constants) → test in Task 2; Task 3 (config knobs) → test in Task 3; Task 4 (version helper) → test in Task 4; Task 5 (DB session plumbing) → test in Task 5; Task 6 (lifespan wiring) → test in Task 6; Task 7 (range cap) → test in Task 7; Task 8 (empty-window contract) → test in Task 8; Task 9 (error bodies) → test in Task 9; Task 10 (504 handler) → test in Task 10; Task 11 (openapi artifact) → drift test in Task 11.

### [PASS] Cross-task dependency on Task 8 → Task 10 is correctly sequenced

Task 8's empty-window test includes an `xfail` for the QueryCanceled→504 mapping because that handler is registered in Task 10. The task itself documents the alternative ("or sequence this assertion into Task 10's test"), so the dependency is explicit and the test is not silently waiting on undefined behavior. This is good practice for an across-task contract.

### [PASS] Task 13 explicitly carries the SC2 measurement-and-raise protocol

Success criterion 2 says "8 s or raise and record." Task 13 step 3 names the threshold, names the action ("raise `API_SERVING_SESSION.statement_timeout`"), and names the obligation ("record the numbers in design D1"). The criterion is closed-loop, not just "verify."

### [PASS] Constants are derived, not literal per-granularity

Task 2's success criterion states "no per-granularity max span is written as a literal anywhere" and pins the intraday values via the formula 960/192/64/16/4. This prevents the common drift where someone hand-edits the cap for `1m` but forgets `5m`. The derivation is asserted, the literals are not.

### [PASS] Diff-against-landed-code framing is preserved through the tasks

The Context Summary explicitly tells the implementer that `is_stale`, `deps.get_db_pool`, and the `CAGG_BASE_GRANULARITY` probe branch in `bars.py` already exist. Tasks 1, 6, 7, and 8 are written against the post-185 codebase (e.g., Task 8 references "the 185 D8a pattern" for scoped checkout, Task 6 references `get_db_pool`). No task reintroduces 185's work.

### [PASS] Sequencing: no circular dependencies; commit checkpoints distributed

Tasks are linearly ordered with each depending only on its predecessors. Commits are not batched at the end — Task 11 commits the openapi artifact and its drift test as part of its own close-out, and Task 14 only regenerates the artifact and merges. `task-checker` is invoked per task during the close-out, not as a single end-of-slice sweep.

### [PASS] No scope creep detected against the slice's Excluded list

The slice explicitly excludes pagination, auth/rate-limiting/caching, pool sizing, background refresh, and symbols/gaps response semantics. Tasks 1, 6, 7, 9, and 10 all read correctly within those bounds — Task 6 builds a `get_max_bars` accessor (D9), Task 7 does the range cap (D4), Task 9 widens only the HTTPException handler without touching `symbols`/`gaps` routes, and Task 10's handler registration is global but the schema `responses=` declaration is restricted to the four data routes. No task introduces a feature the slice deferred to 187 or Future Work.

### [PASS] SC11 (auth/CORS posture) is satisfied by the design, not by a task

SC11 says "the posture ... is recorded in this document — no code change." The decision is already written in D8 of the slice design. No task is needed because the criterion is satisfied by the design document existing. The Context Summary correctly tells the implementer "D8 keeps the auth/CORS posture unchanged. A task that seems to need any of these is a misreading." This is appropriate handling of a "no code" criterion.

### [CONCERN] Task 13's "Rewrite the walkthrough in the design" mixes phase discipline

Task 13 asks the implementer to run the Verification Walkthrough *and* rewrite it in the design document with "actual commands, observed output, and any caveats found." The walkthrough rewrite is a doc edit to a slice-design file, not a code task. It is also an open-ended rewrite with no discrete success criterion beyond "every step passes or its deviation is recorded." This blurs the boundary between "verify what was built" and "edit the design doc post-hoc." Consider splitting: (a) run walkthrough and record results in the task itself, (b) a small follow-on to update the design's Verification Walkthrough section. Alternatively, accept that this slice's phase discipline (Phase 6 close refines the design) treats the rewrite as in-scope — but make that explicit in the task so a junior AI doesn't try to skip it or expand it into a redesign.

### [CONCERN] SC8 has no test asserting that the freshness probe's internal timeout cannot reach the 504 handler

D10 says "Freshness probes cannot reach this handler. `cagg_freshness` catches `psycopg.Error` internally and converts a timeout into a stale verdict (168 D3, 185 D9), so a `504` always means a **data** query was cancelled." This is a load-bearing claim for the 504's meaning — without it, a `504` could mean a stale-coverage timeout, which is not actionable as "narrow the requested range." The walkthrough (step 7b) exercises a data-query cancellation, but there is no test asserting that a `cagg_freshness` `QueryCanceled` does *not* surface as 504. Consider adding a one-line test in Task 10 that simulates a freshness-probe cancellation and asserts the response is the normal `/health` body with `is_stale=true`, not a 504.

### [CONCERN] Task 8's empty-window branch holds a connection for a primary-key seek — no assertion that it doesn't on the non-empty path under all granularities

D5 says "The lookup runs **only** on the already-empty path." Task 8's test asserts "The lookup runs only when the frame is empty." But the *connection* check (D1 / 185 D8a — "the non-empty path holds none") is asserted in Task 8's success criterion ("the non-empty path still checks out no connection for raw granularities") rather than as a test. For the freshness probe path, `get_db_pool` is already used by `deps.get_db_pool` — but that path is exercised for both empty and non-empty bars responses. Add a parametrized test that, for a non-empty `1m/5m/15m/1h/4h/1d` response, asserts `symbol_exists` is never called and `get_db_pool`'s connection context is not entered (only the class pool is). This pins the architectural claim that bars responses have at most one DB connection in flight, which is the whole point of D1/D2/D5's design.

### [NOTE] Task 6 says "no literal `'512MB'` or `'300s'` remains in `app.py`" but the slice design keeps them as the *defaults* in constants

This is consistent — Task 2 moves those literals into `DB_BULK_SESSION` and `API_SERVING_SESSION`, and Task 6's check is that `app.py` no longer references them directly. Reading the two tasks together, the chain is correct: Task 2 owns the literals, Task 6 owns the call sites. Mentioning it as a note so a reviewer doesn't read Task 6 in isolation and think the slice is removing bulk-session values.

### [NOTE] No NFR / load-test NFR restated in the slice; no `tests/load/` task required

The parent slice 180's architecture document is the NFR source. The slice design explicitly defers pool *sizing* to slice 187 (D2), which is where the load-test tier gets built. Slice 186's success criteria are correctness-oriented, not load-oriented. There is no restated NFR requiring a load test in this slice. The Task 13 prod walkthrough is the empirical substitute for SC1 and SC2. No gap.

### [NOTE] No CI wiring task required because CI is publish-on-tag only

The slice design (D7, final note) explicitly states CI is publish-on-tag only and runs no test job. The drift test (`test_openapi_artifact.py`) gates in the local suite, like every other test. The standard "if load test exists, CI gate must exist" rule does not apply because no load test exists in this slice. No gap.

### [PASS] Effort estimates are realistic for a junior AI; no task is too large or too granular

The largest single unit of work is Task 7 (range cap implementation + test, effort 3 each = 6 total). The cap's complexity is genuine: estimator, two rejections, error message that names three numbers from the live ceiling, all derived from constants. It is at the boundary of "should be split" but stays inside it because the implementation and test are each single coherent units. Task 13 (effort 3+1=4) is large in scope (running a multi-step walkthrough and rewriting it in the design) but is one coherent phase activity. No task is too granular — there are no single-line tasks that should be merged.
