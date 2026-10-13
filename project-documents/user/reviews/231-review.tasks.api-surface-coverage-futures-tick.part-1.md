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
sourceDocument: project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-1.md
aiModel: claude-opus-5-5
aiProfile: sdk
status: complete
dateCreated: 20261012
dateUpdated: 20261012
reviewedSha: 8a2beae645067ee060836b845494288636e95358
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 12
durationSeconds: 87.2
squadronVersion: 0.21.5
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Functional requirements trace to tasks across both parts"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-1.md"
  - id: F002
    severity: pass
    category: sequencing
    summary: "Commit checkpoints and test-with pattern"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-1.md:171-389"
  - id: F003
    severity: concern
    category: sequencing
    summary: "Task 3.1 (test fixtures) comes before the seam it needs (3.2)"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-1.md:179-191"
  - id: F004
    severity: concern
    category: correctness
    summary: "The moved `classify` keeps the CLI's timeout figure, which will be wrong in `/status`"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-1.md:138-159"
  - id: F005
    severity: concern
    category: testing
    summary: "Model drift pins cover only the top-level keys of two of the four `roll_payload` dictionaries"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-1.md:283-295"
  - id: F006
    severity: concern
    category: coverage
    summary: "No task covers the two-product universe Integration Requirement"
    location: "unverified"
  - id: F007
    severity: concern
    category: security
    summary: "FR11 is tested only for `503` bodies, not for `/health` and `/status`"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-2.md:158-183"
  - id: F008
    severity: concern
    category: ci
    summary: "Load tier exists but CI gating is left implicit"
    location: ".github/workflows/ci.yml"
  - id: F009
    severity: note
    category: safety
    summary: "The migrations-pending test deletes a row; the target database must be a throwaway"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-1.md:235-238"
  - id: F010
    severity: note
    category: clarity
    summary: "The schedule route doesn't say how it finds the held bounds"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-1.md:369-371"
  - id: F011
    severity: note
    category: correctness
    summary: "Pool `configure` and the per-checkout migration check must leave the connection idle"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-1.md:202-218"
  - id: F012
    severity: note
    category: scope
    summary: "The connection budget is checked against dev clusters only"
    location: "project-documents/user/tasks/231-tasks.api-surface-coverage-futures-tick-1.md:127-129"
---

# Review: tasks — slice 231

**Verdict:** CONCERNS
**Model:** claude-opus-5-5

## Findings

### [PASS] Functional requirements trace to tasks across both parts

- FR7: tasks 5.6 and 5.8.
- FR8, FR9 and FR10: tasks 3.3 and 7.2.
- FR11 for `503` bodies: task 3.3.
- FR1, FR2, FR3, FR4, FR5 and FR6: tasks 6.3, 6.5 and 6.9 in part 2.
- Technical Requirements: the docs gate is 8.1–8.3, the load tier is 9.2, the connection budget is 9.3 (using values gathered in 1.1), and the 180/220 document edits are 8.4–8.5.

There is no scope creep. Every task traces to the design's Technical Scope or its Development Approach.

### [PASS] Commit checkpoints and test-with pattern

- Each implementation task is followed straight away by its test task: 2.1→2.2, 3.2→3.3, 4.1→4.2, 5.1→5.2, 5.3→5.4, 5.5→5.6, 5.7→5.8.
- Each section ends in a commit: 2.4, 3.4, 4.3 and 5.9.

There are no circular dependencies.

### [CONCERN] Task 3.1 (test fixtures) comes before the seam it needs (3.2)

- 3.1's fixtures call `create_app(db_url=…, tick_db_url=…)`, a parameter that 3.2 adds.
- The task even says "the fixture comes after 3.2's seam exists".
- Its success criterion (a smoke test through each fixture) can't pass until 3.2 is done. A junior agent working top to bottom will hit a failure here.

Fix: swap the order (3.1 builds `tick_access.py` and the seam; 3.2 adds the fixtures), or merge the fixtures into the start of 3.3.

### [CONCERN] The moved `classify` keeps the CLI's timeout figure, which will be wrong in `/status`

- Task 2.1 moves `classify` "keeping … the `QueryCanceled` wording … exactly". Today that wording names `TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS` (10 s) (`src/manta_trading/cli/tick_composed.py:77-80`).
- In the API, task 7.3 runs `accounting_lines` on a pooled connection, under the pool's API `statement_timeout`. It then classifies read failures with this function. A cancelled status read would therefore report "statement timeout (10 s)" when a different timeout actually applied.
- The test in 2.2 (line 155) would lock that wording in.

Fix: have the moved `classify` take the timeout as a parameter (or leave the timeout prefix out of the shared function). The CLI wrapper passes the composed constant; the API passes its own.

### [CONCERN] Model drift pins cover only the top-level keys of two of the four `roll_payload` dictionaries

TD5 says "a test pins each model's field set to the matching dictionary's keys, so the CLI JSON and the API cannot drift". Task 4.2 pins only `ActiveLineRecord` against `active_line_dict` and the schedule model against `schedule_dict`. Three nested shapes are left unpinned:
- `answers` and `next_roll` (`contract_dict`);
- `unresolved` (`unresolved_dict`);
- the schedule's `segments` (`_segment_dict`, which includes `expiration`, unlike TD5's time-series `SegmentRecord`).

All of these are in `src/manta_trading/data/tick/roll_payload.py:21-101`. Drift in a nested record would pass the test.

Fix: add pins for the nested models against `contract_dict`, `unresolved_dict` and the segment dictionary.

### [CONCERN] No task covers the two-product universe Integration Requirement

The design's Integration Requirements say: "232 needs no API change for GC; a unit test drives the routes from a two-product universe fixture." Neither part 1 nor part 2 has this test.

Task 5.6's "a product with nothing held" case (part 1, lines 358) is the natural place to add it, because it already needs a second universe entry. Without the fixture, the hand-off promise to 232 is untested.

Fix: add a step to 5.6 (and one route check in 6.9). Patch `TICK_UNIVERSE` to two entries, then assert:
- `/futures/products` lists both;
- the second product's `contracts` and `schedule` routes resolve, with no `404`.

### [CONCERN] FR11 is tested only for `503` bodies, not for `/health` and `/status`

- FR11 says that no `503`, `/health` or `/status` text ever contains the tick URL's password.
- Task 3.3 (part 1, lines 246-247) covers the `503` body.
- Task 7.2 (`tick_detail`) and task 7.4 (`tick.detail`) have no password assertion. Those are separate code paths: `probe_tick` and 7.3's classify call.

Fix: add the "password in a closed-port URL is absent" assertion to 7.2 and 7.4.

### [CONCERN] Load tier exists but CI gating is left implicit

- Task 9.2 adds `test/load/test_231_futures_api_nfr.py` (the project uses `test/load/`, not `tests/load/`).
- The only workflow, `.github/workflows/ci.yml`, publishes on tags and runs no test tier at all. In practice, task 10.1's manual run is the only gate.
- That may be the right call, since the load tier needs the private test cluster. But neither task file says so.

Fix: make the gate explicit. Either state in 10.1 that the manual load-tier run is the merge gate, and why CI can't run it, or add a task or open question for the Project Manager on CI wiring.

### [NOTE] The migrations-pending test deletes a row; the target database must be a throwaway

- Task 3.3 deletes, and later restores, the newest tick-track migration's ledger row. Task 7.2 reuses the same approach.
- Under the project's database rules this is allowed only on a database the test process created.
- If the 3.1 fixture shares a session-scoped tick test database, a failed restore would break the tests that run after it.

Fix: tell 3.3 to use a function-scoped throwaway tick database created by the fixture (the pattern `test/tick_support/database.py` provides).

### [NOTE] The schedule route doesn't say how it finds the held bounds

- Task 5.7 says that when `start` or `end` is omitted, "the held bounds are used, as `product_plan` does".
- `build_schedule` requires both dates (`src/manta_trading/data/tick/active_contract.py:160-167`).

Fix: name the read, `held_session_bounds(conn, shape_of(entry))` on the pooled connection, and the response when nothing is held (a `422` or an empty schedule), so the agent doesn't have to invent it.

### [NOTE] Pool `configure` and the per-checkout migration check must leave the connection idle

- psycopg_pool discards a connection that `configure` leaves inside a transaction.
- If `missing_migrations` runs before task 6.2's `REPEATABLE READ` transaction and leaves an implicit transaction open, setting the isolation level will fail.

Task 1.1 already asks whether `missing_migrations` needs autocommit. Fix: carry the answer into 3.2 as an explicit step (commit, or use autocommit around the check) so it isn't lost.

### [NOTE] The connection budget is checked against dev clusters only

- Task 1.1 reads `max_connections` on the dev clusters.
- The budget matters under `mt serve --workers N` in production, which 234 wires up.

Fix: record the dev values as dev values, and pass the production check to 234 as a named item, rather than implying production is covered.

### Run Digest

- Response length: 8732 chars
- Response is newline-free: no
- Tool calls made: 12
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 87.2 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 12
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 12
- Finding-shaped matches — surviving validation: 12
