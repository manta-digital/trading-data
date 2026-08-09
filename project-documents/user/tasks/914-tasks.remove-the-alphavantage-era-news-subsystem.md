---
docType: tasks
slice: remove-the-alphavantage-era-news-subsystem
project: trading-data
lld: user/slices/914-slice.remove-the-alphavantage-era-news-subsystem.md
dependencies: []
projectState: Slice 913 (least-privilege DB roles) complete and merged to main. The news subsystem (src/manta_trading/news/, src/manta_trading/agents/newsagent.py) is a non-functional stub left over from AlphaVantage's removal in slice 152 — every entry point raises unconditionally. Four integration tests and the pymongo server-RTT background thread contribute permanent noise and a timeout hazard to every test run.
dateCreated: 20260808
dateUpdated: 20260809
status: complete
review: revised-per-914-review-tasks-fail
---

# Tasks: Remove the AlphaVantage-Era News Subsystem

## Context

Working on slice 914 of the Foundation & Cleanup initiative (900 band). This
slice deletes the news subsystem outright: 7 source modules (1,546 lines) and
8 test files (1,008 lines), plus the `pymongo`/`motor` dependencies they alone
required. The subsystem cannot run — the stub raises unconditionally — so
there is no behavior to preserve, only removal to verify. Full rationale and
the verified isolation (zero consumers outside the subsystem) are in the
slice design; tasks below reference it rather than repeating it.

**Dependencies**: None.
**Delivers**: A codebase with no dead news code, no `pymongo`/`motor`
dependency, and an integration tier with 4 fewer permanent failures and no
30-second timeout hazard from the news test suite.
**Next slice**: None specified by the slice plan; 905/906/907/910/911 remain
open in the 900 band with no ordering dependency on this slice.

**Commit granularity note**: source deletion (Phase 1) and test deletion
(Phase 2) are split into separate commits rather than the single combined
commit the slice design's Implementation Notes suggest. This is a deliberate
choice — it keeps the source-only commit bisectable — and does not change
end state or leave the tree broken at either checkpoint.

**Pre-slice baseline (captured 20260809, task 1.1)**:
- ruff (`src/ test/`): 1788 errors
- mypy (`uv run --extra dev mypy`): 93 errors in 19 files (149 source files checked)
- unit (`test/unit/`): 1892 passed, 45 skipped, 0 failed
- integration (`test/integration/`): 12 passed, 6 failed, 250 skipped, 2 errors
  — the 6 failures are exactly the expected set: 4 news-related
  (`testNewsIntegration.py::test_agent_command`,
  `testNewsIntegration.py::test_invalid_command`,
  `testNewsIntegration.py::test_verify_news_db`,
  `testnewsdbmigrationintegration.py::testMigrationBatch`) plus 2 unrelated
  (`test_cli_lists.py::test_lists_ls_includes_priority1`,
  `test_cli_lists.py::test_lists_show_priority1_emits_ten_symbols`)
- `mt --help` captured to `/tmp/mt-help-before.txt` (21 lines)

## Tasks

### Phase 1: Capture Baseline, Then Delete Source

- [x] **1.1 Capture the pre-slice baseline**
  - [x] Run `ruff check src/ test/` and record the violation count
  - [x] Run the project's mypy invocation per current gate configuration and
    record the error count
  - [x] Run the unit test suite and record pass/fail counts
  - [x] Run the integration test suite and record pass/fail counts —
    expected 6 pre-existing failures (4 news-related + 2 unrelated
    `testcli_lists.py` failures) per slice 913's wrap-up; record the actual
    numbers observed, since this is the number Phase 4 must reproduce minus
    4
  - [x] Run `mt --help > /tmp/mt-help-before.txt` and keep the file — it is
    the reference output Task 4.5 diffs against
  - [x] Write the ruff/mypy/unit/integration counts into a scratch note
    (e.g. the top of this task file's Context section, or a local file) —
    they are the reference values Phase 4.1 and 4.2 compare against
  - [x] Success: ruff, mypy, unit, and integration baseline counts are
    recorded, and `/tmp/mt-help-before.txt` exists and is non-empty

- [x] **1.2 Delete the news package**
  - [x] Delete `src/manta_trading/news/` in full: `__init__.py`, `news.py`,
    `newsdb.py`, `newsdbmigrationutility.py`, `newsfields.py`,
    `newsservice.py`, `newsutility.py` (includes any `__pycache__/` under it)
  - [x] Delete `src/manta_trading/agents/newsagent.py` (and its
    `__pycache__/` entry if present)
  - [x] Success: `find src/manta_trading/news src/manta_trading/agents/newsagent.py`
    reports both paths do not exist

- [x] **1.3 Verify no dangling source references (gate — must pass before commit)**
  - [x] Run `grep -ri news src/ test/ --include="*.py"`
  - [x] Confirm the only hits are the literal string `"NEWSTOCK"` in
    `test/unit/test_chunking_strategy.py` (an unrelated arbitrary test
    symbol, per the slice design's Migration Plan) — no other matches
  - [x] If any other match appears, stop and resolve it before committing —
    do not proceed to the Phase 1 commit with an unresolved reference
  - [x] Success: grep output contains only `test_chunking_strategy.py` lines,
    nothing from `cli/`, `api_server/`, `config/`, or any other module

**Commit**: `refactor: remove AlphaVantage-era news subsystem source`

### Phase 2: Test Deletion

- [x] **2.1 Delete news unit tests**
  - [x] Delete `test/unit/testnews.py`
  - [x] Delete `test/unit/testnewsdb.py`
  - [x] Delete `test/unit/testnewsdbmigration.py`
  - [x] Delete `test/unit/testnewsagent.py`
  - [x] Delete `test/unit/testnewsservice.py`
  - [x] Success: none of the five files exist under `test/unit/`

- [x] **2.2 Delete news integration tests**
  - [x] Delete `test/integration/testnewsdbmigrationintegration.py`
  - [x] Delete `test/integration/testnewsdbintegration.py`
  - [x] Delete `test/integration/testNewsIntegration.py`
  - [x] Success: none of the three files exist under `test/integration/`

- [x] **2.3 Verify no dangling test references**
  - [x] Re-run `grep -ri news test/ --include="*.py"` and confirm the only
    remaining hit is `test_chunking_strategy.py`'s `"NEWSTOCK"` symbol
  - [x] Success: no test file imports from `manta_trading.news` or
    `manta_trading.agents.newsagent`

- [x] **2.4 Verify test collection is clean after deletion (gate — must pass before commit)**
  - [x] Run `pytest test/unit/ --collect-only` — confirm collection succeeds
    with no import errors (catches a `conftest.py` or fixture that still
    referenced a deleted module)
  - [x] Run `pytest test/integration/ --collect-only` — same check for the
    integration tier
  - [x] If either collection step errors, stop and resolve it before
    committing
  - [x] Success: both tiers collect without error; no `ModuleNotFoundError`
    or `ImportError` referencing `news` or `newsagent`

**Commit**: `test: remove AlphaVantage-era news subsystem tests`

### Phase 3: Dependency Cleanup

- [x] **3.1 Remove pymongo and motor from pyproject.toml**
  - [x] Remove `"pymongo>=4.9.2"` from `[project] dependencies`
  - [x] Remove `"motor>=3.6.0"` from `[project.optional-dependencies] dev`
  - [x] Leave `chromadb` untouched — confirmed unused but explicitly out of
    scope for this slice (see slice design, Technical Scope)
  - [x] Success: `grep -i "pymongo\|motor" pyproject.toml` returns nothing

- [x] **3.2 Refresh the lockfile and verify resolution**
  - [x] Run `uv lock` to regenerate `uv.lock` against the pruned dependency
    set
  - [x] Run `uv sync` and confirm it completes without error
  - [x] Run `grep -i "pymongo\|motor" uv.lock` and confirm no matches
  - [x] Success: `uv sync` succeeds cleanly; neither package appears in
    `uv.lock`

**Commit**: `chore: remove pymongo and motor dependencies`

### Phase 4: Full Verification Pass

- [x] **4.1 Run static checks against the 1.1 baseline**
  - [x] Run `ruff check src/ test/` — compare against the violation count
    recorded in task 1.1; confirm the count is equal or lower (file count
    dropped, so it should not increase)
  - [x] Run the project's mypy invocation per current gate configuration —
    compare against the error count recorded in task 1.1; confirm no new
    errors
  - [x] Success: both checks are at or below the exact counts recorded in
    task 1.1

- [x] **4.2 Run the full test suite per subpackage against the 1.1 baseline**
  - [x] Run unit tests: confirm pass count matches expectations (5 fewer
    test files than the task 1.1 baseline, no new failures)
  - [x] Run integration tests: confirm failure count is exactly 4 lower than
    the task 1.1 baseline, with no new failures — this should leave only
    the unrelated `testcli_lists.py` failures recorded in 1.1
  - [x] Success: integration failure count is exactly (1.1 baseline − 4),
    unit failure count is unchanged from 1.1, no new failures anywhere

- [x] **4.3 Run the load test suite**
  - [x] Run the project's load test tier (`test/load/`) per its documented
    invocation (e.g. `MT_RUN_LOAD_TESTS=1` gate, per the 900-slice plan's
    slice 907 notes)
  - [x] Confirm pass count matches the pre-slice state — this tier has no
    news-subsystem tests, so the count should be unchanged
  - [x] Success: load tier passes with the same results as before this
    slice's changes

- [x] **4.4 Confirm the timeout hazard is gone by construction**
  - [x] Run the full unit + integration suite (per-subpackage, as in 4.2)
    and confirm every test completes within its configured per-test
    `pytest-timeout` limit — no test run reaches the timeout
  - [x] This is a by-construction check, not an attribution check: since
    `pymongo` is fully removed from the dependency tree (verified in 3.2),
    no test can spawn its server-RTT background thread, so no test can hit
    a timeout caused by it
  - [x] Success: no test in either tier hits its configured timeout

- [x] **4.5 Confirm CLI is unaffected**
  - [x] Run `mt --help > /tmp/mt-help-after.txt`
  - [x] Run `diff /tmp/mt-help-before.txt /tmp/mt-help-after.txt` — expect no
    output (no command referenced the news subsystem, so nothing should
    change)
  - [x] Success: `diff` reports no differences between the 1.1 baseline and
    the post-removal output

Phase 4 is verification-only and produces no file changes to commit —
proceed directly to Phase 5 without a commit checkpoint here. If any 4.x
step surfaces an actual defect requiring a code fix, that fix gets its own
descriptively-named commit before continuing.

### Phase 5: Documentation and Slice Closeout

- [x] **5.1 Update the slice design's Verification Walkthrough**
  - [x] In `user/slices/914-slice.remove-the-alphavantage-era-news-subsystem.md`,
    replace the walkthrough's expected outputs with actual command output
    captured during Phase 4, including the real pre/post integration
    failure counts
  - [x] Note any deviation from the design (e.g., if baseline counts
    differed from what 913's wrap-up recorded)
  - [x] Success: walkthrough is independently reproducible by another agent
    or the Project Manager

- [x] **5.2 Update CHANGELOG.md**
  - [x] Add an entry under the appropriate section for slice 914: removal of
    the AlphaVantage-era news subsystem, dependency drop (`pymongo`,
    `motor`), and the integration-tier noise reduction
  - [x] Success: `CHANGELOG.md` entry follows the existing format used for
    prior 900-band slices (see slice 913's entry)

- [x] **5.3 Finalize slice design frontmatter**
  - [x] Set `status: complete` and `dateUpdated` to the completion date in
    `914-slice.remove-the-alphavantage-era-news-subsystem.md`
  - [x] Success: frontmatter reflects completion accurately

**Commit**: `docs: close out slice 914 — news subsystem removal`
