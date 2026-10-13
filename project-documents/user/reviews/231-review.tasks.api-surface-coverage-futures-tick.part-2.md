---
docType: review
layer: project
reviewType: tasks
slice: api-surface-coverage-futures-tick
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-2.md
aiModel: claude-opus-5-5
aiProfile: sdk
status: complete
dateCreated: 20261012
dateUpdated: 20261012
reviewedSha: 8a2beae645067ee060836b845494288636e95358
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 10
durationSeconds: 79.6
squadronVersion: 0.21.5
findings:
  - id: F001
    severity: concern
    category: test-coverage
    summary: "No task tests that `from` must be before `until`, though FR4 requires it"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-2.md:63-64"
  - id: F002
    severity: concern
    category: coverage-gap
    summary: "Nothing tests the two-product universe that the integration requirement for 232 calls for"
    location: "project-documents/user/slices/231-slice.api-surface-coverage-futures-tick.md:688-689"
  - id: F003
    severity: concern
    category: security
    summary: "FR11 says `/health` and `/status` must not leak the password, but only the `503` body is tested"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-2.md:158-183"
  - id: F004
    severity: concern
    category: sequencing
    summary: "Task 6.3 tests through a route that task 6.4 creates later"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-2.md:74-81"
  - id: F005
    severity: concern
    category: dry
    summary: "Task 6.2 creates a second error mapping and leaves \"outages\" vague"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-2.md:54-62"
  - id: F006
    severity: concern
    category: production-safety
    summary: "Existing load tests would read `MT_TICK_DB_URL` from `.env`, and the \"with a tick pool configured\" check is never wired up"
    location: "test/load/test_187_api_nfr.py:119"
  - id: F007
    severity: concern
    category: ci
    summary: "No CI task gates the new load tier, and the decision isn't recorded"
    location: ".github/workflows/ci.yml"
  - id: F008
    severity: note
    category: test-coverage
    summary: "FR8 and FR9 are only partly tested on the composed routes"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-2.md:176-205"
  - id: F009
    severity: note
    category: test-coverage
    summary: "Nothing explicitly tests that a request exactly at the ceiling succeeds"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-2.md:77-81"
  - id: F010
    severity: note
    category: test-coverage
    summary: "The time-series routes have no test for a calendar outage"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-2.md:128-138"
  - id: F011
    severity: pass
    category: coverage
    summary: "Most success criteria trace to tasks, and the tasks are well sized"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-2.md"
---

# Review: tasks — slice 231

**Verdict:** CONCERNS
**Model:** claude-opus-5-5

## Findings

### [CONCERN] No task tests that `from` must be before `until`, though FR4 requires it

FR4 says "`from` not before `until` is a `422` with a message naming the problem". Task 6.2 implements this check, but neither 6.3 nor 6.5 tests it, and part 1 has no time-series route to test it on. Add a case to 6.5: `from == until` and `from > until` each give `422`. Also add a naive-datetime case there, since 6.2 says a naive datetime is read as UTC.

### [CONCERN] Nothing tests the two-product universe that the integration requirement for 232 calls for

The design requires "a unit test [that] drives the routes from a two-product universe fixture" to show GC needs no API change. No task in part 1 or part 2 does this. 5.6's "a product with nothing held" uses the one seeded universe. Add a unit test, either to 6.9 or as a small separate task, that patches `TICK_UNIVERSE` to two entries. It should check that `/futures/products` lists both and that a product route resolves the second product with no literal.

### [CONCERN] FR11 says `/health` and `/status` must not leak the password, but only the `503` body is tested

FR11 covers "No `503`, `/health` or `/status` text". Part 1's 3.3 checks the `503` body only. Tasks 7.2 and 7.4 build `tick_detail` and `tick.detail` from probe or classify output, but neither checks for a password. Add one case to each: use a closed-port tick URL with a password in it, and assert the password is absent from `tick_detail` and from `tick.detail`.

### [CONCERN] Task 6.3 tests through a route that task 6.4 creates later

The integration tests in 6.3 change the ceiling with `app.dependency_overrides[get_max_bars]`. That override only works through a route that depends on `get_max_bars` (`src/manta_trading/api_server/deps.py:45`), and the first such route is added in 6.4. The paging test also needs a route to page through. There are two ways to fix it:
- move the integration half of 6.3 into 6.5; or
- have 6.3 call the helper directly with an explicit ceiling, and keep the override-based tests for 6.5.

### [CONCERN] Task 6.2 creates a second error mapping and leaves "outages" vague

Part 1 already maps outages to `503` and preflight errors to `422` in two catalog routes: 5.5, which says "do not restate the catch tuple a third time", and 5.7. Task 6.2 adds a third copy "in one place" for the time-series routes only. It also writes "outages → `TickServiceUnavailable(UNREACHABLE)`" without naming the types. TD2 names them: `OperationalError` other than `QueryCanceled`, and `TickCalendarError`. Because `QueryCanceled` is a subclass of `OperationalError`, it has to be matched first, and 6.2 doesn't say so. Fix: 6.2 should reuse or extend the mapping helper from part 1 (the one 5.7 uses), name the outage types explicitly, and say that `QueryCanceled` is matched first.

### [CONCERN] Existing load tests would read `MT_TICK_DB_URL` from `.env`, and the "with a tick pool configured" check is never wired up

`test_187`, `test_188` and `test_189` call `create_app(db_url=db_url)` with no tick URL. Under 3.2, `tick_db_url=None` means "resolve from `Settings`", and `Settings` loads `.env` (`src/manta_trading/config/__init__.py:106`). So after this slice, every existing load test would open a pool against the dev tick database. That breaks the task file's own rule, "Tests never read `MT_TICK_DB_URL`". The Technical Requirements also say the equities and Kalshi load tests must still pass *with a tick pool configured*. 9.2 only checks "no new failure beyond the 4 known ones", without configuring the pool. Add a sub-item to 9.2 that passes the seeded test tick URL explicitly to the existing load tests' `create_app` calls. Part 1 should also check the existing unit and integration tests that call `create_app(db_url=…)` for the same leak.

### [CONCERN] No CI task gates the new load tier, and the decision isn't recorded

Task 9.2 adds `test/load/test_231_futures_api_nfr.py`, but no task wires it into CI. Today `ci.yml` only publishes on tags and runs no tests, so the earlier load tiers aren't gated either. The Python rules say "CI must gate load tests for slices touching these paths". Either add a CI wiring task, or add a sub-item to 9.2 or 10.1 recording why there is no gate (the load tier needs the test cluster). Leaving it unsaid hides the gap.

### [NOTE] FR8 and FR9 are only partly tested on the composed routes

- 7.4 tests `/status` with the database unreachable, but FR8 also requires the not-configured case.
- 7.6 tests `/overview` with no tick URL configured, but not with the database unreachable (FR9).

Each is one more parametrized case using fixtures that already exist.

### [NOTE] Nothing explicitly tests that a request exactly at the ceiling succeeds

FR5's second sentence is "A request at or under it succeeds completely". The paging test passes through this boundary but doesn't assert it. Add one case to 6.3 or 6.5 with exactly 10 rows and the ceiling set to 10.

### [NOTE] The time-series routes have no test for a calendar outage

6.9 has no case where the calendar URL points at a closed port, so `product_plan`/`build_schedule` raising `TickCalendarError` → `503` goes untested. Part 1's 5.6 covers this for `/futures/products` only. Consider adding the same case to 6.9 once the error mapping is shared.

### [PASS] Most success criteria trace to tasks, and the tasks are well sized

- **Functional requirements:**
  - FR1–3, FR5 and FR6 map to 6.3–6.9.
  - FR7 and FR10 are covered in part 1.
  - FR8, FR9 and FR11 are partly covered; the gaps are in the findings above.
- **Integration requirements:** status and overview agreement with the CLI is in 7.4 and 7.6.
- **Documents:** the docs gate, value markers and enum markers are in 8.1–8.3. The 180 and 220 architecture updates are in 8.4–8.5.
- **Load tier and connection budget:** the four measured bounds, with the status cost as its own pass/fail test, are in 9.2. The connection-budget check is 9.3.
- **Walkthrough and design alignment:** 10.2 and 10.3.

The order of documents before load tier differs from the design's Development Approach, but nothing in 8.4 depends on load measurements, so it's harmless. The task sizes fit the slice. The extra `model_construct` measurement is something the design asks for, not scope creep.

### Run Digest

- Response length: 8112 chars
- Response is newline-free: no
- Tool calls made: 10
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 79.6 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 11
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 11
- Finding-shaped matches — surviving validation: 11
