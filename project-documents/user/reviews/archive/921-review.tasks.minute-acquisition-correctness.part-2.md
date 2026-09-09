---
docType: review
layer: project
reviewType: tasks
slice: minute-acquisition-correctness
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260908
dateUpdated: 20260908
reviewedSha: 7ebbe871afef43aa69bd1fd598e35e25194ee377
findings:
  - id: F001
    severity: concern
    category: commit-checkpoints
    summary: "Section 8's only commit checkpoint lands after the release it should precede"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:267-292"
  - id: F002
    severity: concern
    category: test-coverage
    summary: "The repair's database behavior is only ever exercised against a fake writer"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:211-224"
  - id: F003
    severity: concern
    category: correctness
    summary: "Gap rows straddling `REPAIR_921_WINDOW_START` are neither reset nor excluded"
    location: "src/manta_trading/data/gaps/update_data_gaps.py:295-313"
  - id: F004
    severity: concern
    category: task-sequencing
    summary: "Tasks 8.4 and 8.5 are wait-blocked on wall-clock events"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:267-282"
  - id: F005
    severity: concern
    category: test-coverage
    summary: "No load-tier task for the health check's stated read bound"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:93-105"
  - id: F006
    severity: concern
    category: task-clarity
    summary: "Task 6.7's rendering mechanism is unspecified and its success criterion is untestable as stated"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:144-153"
  - id: F007
    severity: concern
    category: completeness
    summary: "Nothing fetches the candidate sessions, and \"no session qualifies\" has no stated verdict"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:79-117"
  - id: F008
    severity: note
    category: consistency
    summary: "The health floor is stated as both 975,000 and 1,000,000"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:111-117"
  - id: F009
    severity: note
    category: test-with-pattern
    summary: "Section 6 batches its tests at Task 6.6 rather than following each implementation task"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:132-143"
  - id: F010
    severity: pass
    category: criteria-coverage
    summary: "Every success criterion this file owns has a task, and no task is untraceable"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:57-292"
  - id: F011
    severity: pass
    category: correctness
    summary: "Removing the quota check has no dependents outside `health.py`, so Task 6.5's conditional resolves cleanly"
    location: "src/manta_trading/cli/commands/health.py:145-190"
  - id: F012
    severity: pass
    category: task-sequencing
    summary: "Section 7 is correctly gated on the range-end fix landing first"
    location: "project-documents/user/tasks/921-tasks.minute-acquisition-correctness-2.md:29-32"
---

# Review: tasks — slice 921

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Findings

### [CONCERN] Section 8's only commit checkpoint lands after the release it should precede

Sections 6 and 7 each end with an explicit checkpoint (Task 6.8, Task 7.7). Section 8 has one commit, Task 8.7 (`docs: record the 921 cutover measurements and close #19/#20`), placed after Task 8.4 ("version bumped, tagged, released"), Task 8.5 and Task 8.6.

Failure scenario: an implementer follows the order literally. `scripts/cutover_921_minute_sessions.py` (Task 8.1), its tests (8.2), and the CHANGELOG entry (8.3) are still uncommitted when Task 8.4 tags `v0.14.0`. The cutover script's own first step is `install-production.sh --ref v0.14.0`, which installs from a checkout at that ref — a ref that does not contain the script being run, and a release whose CHANGELOG omits the entry Task 8.3 wrote. Add a checkpoint after Task 8.3 (`feat: add the 921 cutover script and changelog entry`) and leave 8.7 for the measurements and closeout only.

### [CONCERN] The repair's database behavior is only ever exercised against a fake writer

Task 7.5 specifies unit tests with "a fake writer; no live database". Every claim SC2 makes that the fake cannot reach is a property of `update_data_gaps` itself: that `force_reset_terminal=True` over `[REPAIR_921_WINDOW_START, now_midnight]` resets the terminal rows and only those; that carry-forward (`_best_prior_count`, keyed on `gap_start`, update_data_gaps.py:316) preserves `attempt_count` so a second `--apply` is genuinely zero net change; that rows before the window survive. The repo has an integration tier (`test/integration/`) and the fixture convention (`MT_TIMESCALE_TEST_URL`) for exactly this.

Failure scenario: `--apply` runs against production, the fake-writer suite green, and the second `--apply` is not idempotent because carry-forward keys on a `gap_start` the recomputed range no longer matches — every repaired row restarts at `attempt_count = 0` (or, worse, re-increments toward `RETRY_EXHAUSTED`), and nothing in Task 7.5 could have caught it. Add an integration-tier task after Task 7.5: seed a prod-shaped `data_gaps` fixture spanning the window boundary, run `--apply` twice, assert row-level equality and the pre-window rows byte-identical.

### [CONCERN] Gap rows straddling `REPAIR_921_WINDOW_START` are neither reset nor excluded

`_delete_intersecting` is named and documented as an intersection (`"Delete all data_gaps rows whose [gap_start, gap_end] intersects [from_ts, to_ts]"`) but its SQL is containment: `gap_start >= %s AND gap_end <= %s`. Task 7.4 passes `from_ts = REPAIR_921_WINDOW_START` and treats "rows before the window are untouched" as the only boundary case; no task considers a row that starts before 2026-07-16 and ends inside the window.

Failure scenario: a symbol carries one coalesced UNKNOWN range from 2026-05 to 2026-08 (the coalescer merges consecutive sessions, and the design records 57,663 UNKNOWN backfill rows plus 1,321 legacy rows in play). The delete skips it because `gap_start < from_ts`. The seed then computes missing sessions from the *cagg coverage index* — which knows nothing about that row — and inserts fresh UNKNOWN rows for the in-window sessions the surviving row already covers. The symbol ends with overlapping gap rows, and those sessions get fetched twice against a quota the slice is explicitly rationing. Task 7.3's `--check` should count boundary-straddling rows, and Task 7.4 should state what happens to them (skip the symbol and report, or widen the window to the row's start).

### [CONCERN] Tasks 8.4 and 8.5 are wait-blocked on wall-clock events

Task 8.4 says "Run the cutover script just after 00:00 UTC"; Task 8.5 says "After two nightly firings, confirm…", and Task 8.6 (close #19/#20) depends on 8.5. The standing rule on this project is that no task may wait on tonight or tomorrow — work is sized by what is measurable now.

Failure scenario: the branch sits at 90% for two calendar days with three tasks unstartable and nothing to check off, and the "two nightly firings" precondition silently becomes "whenever someone remembers to look". `cutover_common` already exposes a `fire` helper and the cutover script runs with root, so the passes can be triggered directly after the quota reset rather than waited for. Restructure 8.5 into a `--verify` mode of `repair_921_minute_sessions.py` that prints every SC3/SC6/SC7 measurement on demand; the act of running it is the task, not the wait.

### [CONCERN] No load-tier task for the health check's stated read bound

The slice restates a read NFR for the new check — "One session is ~41k cagg rows (measured 2026-08-27), a sub-second read" — under `HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT = "30s"` with exit 2 on timeout, citing the §166/§167 raw-table latency cliff. The project's convention for exactly this claim is a load-tier test: `test/load/test_169_coverage_freshness_probe_nfr.py:108` asserts the sibling `CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT` probe "stays well inside its budget" precisely because a timeout degrades to a refusal rather than a pass. Tasks 6.3 and 6.6 cover only a mocked query-text assertion and a simulated timeout.

Failure scenario: the grouped `SUM(minute_count)` read over `[session_open_utc, session_close_utc)` regresses when the cagg's chunk layout changes; on production it exceeds 30 s, the check exits 2, and `mt data health` reports *unavailable* — indistinguishable to an operator from the silence this slice exists to end, with no test to catch it. Add a `test/load/test_921_minute_session_mass_nfr.py` task against `prod_shaped_db`. Note that CI gating is a repo-wide gap (`.github/workflows/ci.yml` runs no test job; tracked as slice 907), so the gate is the documented manual run — the load-test task should say so explicitly rather than leaving it implicit.

### [CONCERN] Task 6.7's rendering mechanism is unspecified and its success criterion is untestable as stated

`deploy/install-production.sh:173` installs each unit verbatim (`install -m 0644 … "${UNIT_SRC_DIR}/${unit}" "${UNIT_DIR}/${unit}"`) from a bash loop. Task 6.7 says to render `OnCalendar` from `MINUTE_PASS_FIRING_TIMES_UTC` but does not say how a bash installer reads a Python constant, what becomes of `deploy/systemd/mt-minute-pass.timer` (a `.in` template? a sed target?), or when in the install sequence the venv is available to evaluate it. Its success criterion — "`test/unit/deploy/test_units.py` asserts the installed unit's `OnCalendar` lines match the constant" — names an artifact a unit-tier test cannot see; that file explicitly runs with "no database, no systemd, no network" and parses the repo's unit files.

Failure scenario: a junior implements the rendering, the repo unit file becomes a template with a placeholder, `test_units.py`'s existing `configparser` parse of that file breaks or silently asserts against the placeholder, and the drift guard the task exists to create never actually guards anything. Either specify the template mechanism and fail-loud read explicitly, or — simpler, and what the existing file already does for the Kalshi pair — keep the unit authoritative and make the guard a unit test asserting the `.timer`'s parsed `OnCalendar` values equal the constant.

### [CONCERN] Nothing fetches the candidate sessions, and "no session qualifies" has no stated verdict

Task 6.2 is explicitly I/O-free ("takes `now` and the candidate sessions as arguments"), Task 6.3 is the mass query only, and Task 6.4 is a pure rule plus "wire it into `gather()`". No task owns the `trading_sessions` read for `HEALTH_MINUTE_SESSION_CALENDAR` that produces those candidates. Nor does any task say what the check returns when the selection yields nothing — an empty `trading_sessions` window, a calendar rename, or a long holiday stretch.

Failure scenario: `trading_sessions` is not populated forward past some date; the judged-session selector returns `None`; `gather()` (health.py:159) either raises and takes the whole `mt data health` command to exit 2 for an unrelated reason, or the rule function defaults to `ok=True` — a silent fallback, and the check that exists to catch silence goes silent. Assign the fetch to Task 6.3 or 6.4 and state the verdict for "no judged session" (per the project's no-silent-fallback rule, an explicit non-OK or exit 2, never a pass).

### [NOTE] The health floor is stated as both 975,000 and 1,000,000

Task 6.4 correctly derives `2,500 × 390 = 975,000`, matching the design's example output line; Task 8.5 uses "≥ 1,000,000" for the production acceptance measurement, as does the design's Verification Walkthrough. The two serve different purposes (computed health floor vs. a stricter one-time cutover bar) but are never distinguished, so a junior reconciling them may hardcode either. Say so in one line in Task 6.4.

### [NOTE] Section 6 batches its tests at Task 6.6 rather than following each implementation task

File 1's Sections 1, 3 and 5 pair each implementation task with a test task (1.2→1.3, 1.4→1.5, 3.2→3.3, 5.2→5.3). Section 6 stacks five implementation tasks (6.1–6.5) before Task 6.6's combined test task, and Section 7 stacks 7.3–7.4 before 7.5. Tasks 6.1 and 6.3 carry inline assertions so the exposure is bounded, but splitting 6.6 into a selector test after 6.2 and a rule/rendering test after 6.4 would make each unit independently verifiable at its own boundary.

### [PASS] Every success criterion this file owns has a task, and no task is untraceable

SC2 → Tasks 7.3/7.4/7.5 (the `--check` predicates, `update_data_gaps` under `advisory_lock`, the fake-writer assertion, the unit-active refusal). SC3 → Task 8.5. SC7 → Tasks 6.1–6.6 for the fixtures, the query capture, the 04:04/04:05 boundary and the removed quota line, with the "healthy run after 23:00 UTC" clause in 8.5. SC8 → Tasks 8.3 and 8.6. SC1/SC4/SC5/SC6 are file 1's and are correctly not duplicated here. In the other direction, every task cites a Scope or Decision item that exists — including Task 6.7, which traces to Scope 4's "the check and the timer cannot disagree". No scope creep found.

### [PASS] Removing the quota check has no dependents outside `health.py`, so Task 6.5's conditional resolves cleanly

Task 6.5 hedges ("`fetch_quota` if it has no other caller", "if removing the quota check leaves `data_health` no longer needing the HTTP client"). Checked against the tree: `fetch_quota`, `check_quota` and `HEALTH_EODHD_QUOTA_HEADROOM_MIN` appear only at health.py:37/108/114/145/188/190 and constants.py:96; `gather()` has exactly one caller (health.py:248); `deploy/mt-run` reads only the service exit code, not check names; and no runbook references the `eodhd quota` line. The only other mention is 919's design record, which is history and should not be edited. The httpx client and `settings.eodhd_api_key` requirement can both be dropped outright.

### [PASS] Section 7 is correctly gated on the range-end fix landing first

The Context Summary states the one ordering constraint that actually matters and gives the reason: "Do not start Section 7 (repair) before Sections 1–5 are merged-ready. The repair reseeds gap rows that the fixed code must then fetch correctly; running it against the old range-end code re-creates the same truncation." Task 7.2's additive parameter on `compute_missing_minute_sessions` also correctly lands after file 1's Section 1 rewrote that function's range end, so the two changes to the same function are ordered rather than colliding.
