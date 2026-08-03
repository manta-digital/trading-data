---
docType: tasks
slice: daemon-cycle-correctness
project: trading-data
lldReference: project-documents/user/slices/912-slice.daemon-cycle-correctness.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: []
interfaces: [145, 154]
projectState: >
  Slice 912 design reviewed 20260803 (CONCERNS — F005/F006 placement findings,
  both dispositioned; maintenance-band scope now recorded in
  900-arch.foundation-cleanup.md, slice 168 dropped from interfaces). No
  technical finding against D1. Task breakdown reviewed 20260803 (CONCERNS —
  F005 test-with, F006 criterion wording, F007 commit cadence, F008 integration
  step; all four addressed in this revision). Closes GitHub issues #7 and #6.
  This work does NOT reach production by merging: prod .144 runs from a checkout
  tracking `main`, and 912 merges to `trading-data-maintenance`. It reaches .144
  only when the PM promotes that branch to `main` and .144 pulls and restarts.
dateCreated: 20260803
dateUpdated: 20260803
status: not-started
---

# Tasks: Daemon Cycle Correctness — Data-Driven Daily Work Determination

## Context summary

The daily cycle decides it has work from an in-memory field that records a
*start* and is stamped before the cycle runs, so an interrupted pass marks the
UTC day done and never retries. Converge on the minute cycle's model: derive the
work list from durable per-symbol state, demote the day-timer to a busy-loop
cadence guard. In the same forty lines, stop the `--stop-when-done` exit path
from reporting "scope drained" when it merely hit a closed cadence gate.

All decisions referenced below (D1–D6) are in the LLD.

### Non-negotiables from the design

- The resume set derives from `acquisition_state.last_attempt_ts`, **not** from
  `daily_coverage` (D1). Do not add an `assert_cagg_fresh` call or read a
  coverage cagg anywhere in this slice.
- A derived work list must **terminate**. Any symbol that cannot be acted on —
  no resolvable calendar (D6) — is excluded from the pending set and counted,
  never left pending. A non-terminating work list re-issues the billable bulk
  `/eod-bulk-last-day` call on every cadence tick.
- Nothing records a cycle *start*. `last_daily_cycle_end_utc` is stamped after
  `_run_daily_cycle` returns, mirroring `last_minute_cycle_end_utc` (D2).
- Idle reasons are a `StrEnum`, never a log-message variant (D4).
- `DAILY_CYCLE_START_OFFSET` and `LATE_BAR_GRACE_PERIOD` are equal today.
  Nothing may rely on that (D3).
- The minute path is not touched. It is the model being copied.

### Tests are written with their implementation

Per the project's python rules, each implementation subtask below carries its
own tests and is not complete until they pass. Task 5 holds only the
**cross-cutting** tests that span more than one task's work and therefore cannot
be written until those parts exist. If a subtask's success criterion names an
assertion, that assertion is written in that subtask, not deferred.

### Commit cadence

Commit at every numbered task boundary (end of Task 1, Task 2, …), and within a
task wherever a subtask leaves the tree green. Each commit must have ruff clean
and the daemon subpackage suite passing — a task boundary that cannot meet that
is a signal the task was too large, not a reason to commit red.

This is about bisectability and reviewability, not deployment risk: nothing on
this branch reaches .144. Prod runs from a checkout tracking `main`; 912 merges
to `trading-data-maintenance`, which only reaches `main` by a separate PM
promotion. Do not treat any commit here as a production event.

### Branch

Work on `912-slice.daemon-cycle-correctness`, forked from **and merged back
into** `trading-data-maintenance` — not `main`. Confirmed by the PM 2026-08-03:
the maintenance worktree's branch is the 900-band integration target, and the
Phase 0–5 planning commits for this slice (`1c096d5`, `39998f9`, `2dc2021`) live
there, so a branch forked from `main` would not contain its own design.

Note that `cf config get git.integration_branch` is unset, which nominally makes
the target `main`. Setting it is **not** the fix: the key is project-level, so
pointing it at `trading-data-maintenance` would also redirect the default
worktree's 100-799 slices, which do merge to `main`. Promoting
`trading-data-maintenance` to `main` is a PM-only action outside this slice's
scope.

---

## Task 1 — Constants: split the double-duty grace period (D3)

- [x] **1.1 Add `DAILY_CYCLE_START_OFFSET` to `constants.py`**
  - [x] `DAILY_CYCLE_START_OFFSET: timedelta = timedelta(minutes=30)`
  - [x] Docstring states its actual role: how long after **UTC midnight** the
        daemon waits for the provider's late bars before starting a daily pass.
        Note explicitly that it is not a session-close offset and that its
        equality with `LATE_BAR_GRACE_PERIOD` is coincidental.
  - Success: the constant exists with its own docstring; `LATE_BAR_GRACE_PERIOD`
    keeps its session-close docstring unchanged.
  - Effort: 1

- [x] **1.2 Add `DAILY_CYCLE_RETRY_INTERVAL` to `constants.py`**
  - [x] `DAILY_CYCLE_RETRY_INTERVAL: timedelta = timedelta(minutes=15)`
  - [x] Docstring states it is a busy-loop guard — how soon an interrupted daily
        pass may resume within the same UTC day — and explicitly **not** a
        statement about how often daily data changes. Record the sizing
        rationale: ~94 no-op ticks/day worst case, each a small-table read.
  - Effort: 1

- [x] **1.3 Move `runner.py` onto the new offset**
  - [x] Replace all three `LATE_BAR_GRACE_PERIOD` uses — `daily_cycle_due`
        (:135), `ca_update_due` (:170), `sleep_until_next_due_event` (:202) —
        with `DAILY_CYCLE_START_OFFSET`. The `daily_cycle_due` docstring
        referenced it by name too; updated in step.
  - [x] Remove the `LATE_BAR_GRACE_PERIOD` import from `runner.py`.
  - Success: two greps, both of which must hold.
    1. `grep -rn LATE_BAR_GRACE_PERIOD src/manta_trading/data/` returns
       **nothing** — the daemon no longer references it at all.
    2. `grep -rn LATE_BAR_GRACE_PERIOD src/` returns **only**
       `constants.py` (the definition) and
       `market/schema/migrations/minute.py` (the `data_status` / migration-043
       uses), and those uses are unchanged by this slice.
    These are consistent because the migration and `data_status` paths live
    under `src/manta_trading/market/`, not `src/manta_trading/data/`.
  - Effort: 1

- [x] **1.4 Assert both constants independently in `test_constants.py`**
  - [x] Existing `LATE_BAR_GRACE_PERIOD == timedelta(minutes=30)` assertion
        stays; add the same for both new constants.
  - [x] Add a comment at the assertions noting the values are independent, so a
        future tuning of one does not get "fixed" by copying the other.
  - Success: `uv run --extra dev pytest test/unit/test_constants.py` passes.
  - Effort: 1

**Commit checkpoint** — constants split, no behavior change yet.

---

## Task 2 — Derive the daily work list (D1, D6)

- [x] **2.1 Add `pending_daily_symbols()` to `daily.py`, with its tests**
  - [x] Signature: `(conn, symbol_list: list[str], pass_boundary: datetime) -> DailyWorkList`
        where `DailyWorkList` is a small frozen dataclass carrying
        `pending: list[str]` and `unactionable_no_calendar: list[str]`.
  - [x] One parameterized SQL statement over `instruments` LEFT JOIN
        `acquisition_state` (`granularity = 'daily'`, `provider = 'eodhd'`),
        classifying each scope member:
        - **un-actionable** if no `trading_sessions` row exists for the
          instrument's `trading_calendar_id` — the same join
          `_last_completed_session` uses, evaluated once for the whole scope
          rather than per symbol;
        - **done** if `last_attempt_ts >= pass_boundary`;
        - **pending** otherwise (including `last_attempt_ts IS NULL` and
          missing `acquisition_state` rows — never call `.date()` on `None`).
  - [x] Preserve the caller's ordering for `pending` (`iter_active_instruments`
        already orders `most_stale_first`); do not re-sort.
  - [x] **Tests (written here):** against a mock connection, one case per
        branch — attempted-after-boundary, attempted-before-boundary, NULL
        `last_attempt_ts`, absent `acquisition_state` row, no calendar — plus an
        ordering-preservation case.
  - Success: those tests pass.
  - Effort: 3

- [x] **2.2 Extend `CycleReport` with the un-actionable count and a drained flag**
  - [x] `unactionable_no_calendar: int = 0`
  - [x] `nothing_actionable: bool = False` — set when the derived pending set is
        empty, so the runner can classify the idle reason without re-deriving it
        (D4). Default `False` keeps `run_minute_cycle`'s use of `CycleReport`
        unchanged.
  - Success: `run_minute_cycle` compiles and its existing tests pass untouched.
  - Effort: 1

- [x] **2.3 Wire the work list into `run_daily_cycle`, with its tests**
  - [x] After `symbol_list` is resolved (`daily.py:103-112`), compute the pass
        boundary as today's UTC midnight + `DAILY_CYCLE_START_OFFSET` and call
        `pending_daily_symbols`.
  - [x] If `pending` is empty: set `nothing_actionable = True`, log at INFO with
        the scope size and the un-actionable count, and **return before any
        provider call** — including before `_select_daily_mode`.
  - [x] Otherwise pass `pending` (not `symbol_list`) to `_select_daily_mode` and
        to both the STEADY_STATE and BACKFILL loops.
  - [x] **Tests (written here):** (a) every symbol stamped at/after the boundary
        → zero HTTP requests, asserted with a mock `httpx.Client` whose every
        method fails the test if called, and `nothing_actionable is True`;
        (b) a partially-stamped scope → only the unstamped symbols reach
        `_select_daily_mode`.
  - Success: those tests pass.
  - Effort: 3

- [x] **2.4 Report un-actionable symbols once per cycle, never per symbol**
  - [x] Log at WARNING with the count and a pointer to issue #4 — not 906
        individual lines. Include up to a handful of example symbols for
        diagnosis.
  - [x] Leave `_run_steady_state_cycle`'s existing per-symbol
        `target_end is None` branch (`daily.py:261-267`) in place as a
        belt-and-braces guard; it should now be unreachable for scope members,
        since 2.1 excludes them upstream.
  - [x] **Test (written here):** a scope of N calendar-less symbols produces
        exactly one WARNING record (caplog), not N.
  - Effort: 1

- [x] **2.5 Check downstream consumers of `report.total`**
  - [x] `report.total` now counts pending symbols, not full scope. Audit the
        `-v` progress output and any `mt data daemon` rendering that derives a
        denominator from it, and correct anything that would now display
        "processed 3 of 3" for a 5,900-symbol universe.
  - Success: named explicitly in the commit message either as "no consumer
    affected, verified" or with the fix and its test.
  - Effort: 1

**Commit checkpoint** — work-list derivation complete and tested; runner not yet
changed, so the daemon still gates on the old timer.

---

## Task 3 — Demote the day-timer to a cadence guard (D2)

- [ ] **3.1 Rename the `RunnerState` field**
  - [ ] `last_daily_cycle_start_utc` → `last_daily_cycle_end_utc`. Update the
        dataclass docstring; the rename is load-bearing, not cosmetic.
  - Effort: 1

- [ ] **3.2 Rewrite `daily_cycle_due` as a pure cadence predicate, with its tests**
  - [ ] Returns `False` before today's `midnight + DAILY_CYCLE_START_OFFSET`.
  - [ ] Returns `True` when `last_daily_cycle_end_utc is None`.
  - [ ] Otherwise `True` iff `now - last_daily_cycle_end_utc >= DAILY_CYCLE_RETRY_INTERVAL`.
  - [ ] Rewrite the docstring to state the division of responsibility in the
        same terms `minute_cycle_due` uses: whether any scope member has
        actionable daily work is determined inside `run_daily_cycle` itself; the
        runner gates on cadence so it does not busy-loop.
  - [ ] **Tests (written here):** rewrite
        `test_daily_cycle_due_false_when_last_cycle_was_today` and
        `test_daily_cycle_due_true_after_utc_day_rollover`, which encode the old
        once-per-day semantics, to the cadence semantics; add a
        within-retry-interval case and a past-retry-interval case. Rewriting
        these two is expected; silently deleting either is not.
  - Success: no UTC-day comparison remains in the predicate, and the rewritten
    tests pass.
  - Effort: 2

- [ ] **3.3 Stamp completion, not start, in `_loop`**
  - [ ] Delete the pre-`try` assignment at `runner.py:355`.
  - [ ] Assign `self._state.last_daily_cycle_end_utc = self._clock()` after the
        `try/except` around `_run_daily_cycle`, matching the minute branch at
        `runner.py:379`. It is stamped on the exception path too — a cycle that
        raised still consumed its cadence slot, and retrying it instantly would
        busy-loop against a persistent failure.
  - [ ] **Test (written here):** a `run_daily_cycle` that raises still advances
        `last_daily_cycle_end_utc`, and the loop does not immediately re-enter
        the daily branch.
  - Effort: 1

- [ ] **3.4 Update `sleep_until_next_due_event`, with its tests**
  - [ ] The next daily wake is now `last_daily_cycle_end_utc +
        DAILY_CYCLE_RETRY_INTERVAL` when a cycle has ended today and the day's
        offset has passed, else the day's start boundary (tomorrow's if today's
        has passed and no retry is pending).
  - [ ] Keep `cap_seconds=60` and its SIGTERM-latency rationale untouched.
  - [ ] **Tests (written here):** `test_sleep_caps_at_60s` still passes
        unmodified; add a case asserting the retry-interval wake is chosen over
        the next-day boundary when a cycle ended earlier today.
  - Effort: 2

**Commit checkpoint** — daily gating is now cadence-based and data-driven end to
end. This is the commit that closes issue #7's substance.

---

## Task 4 — Honest idle reporting (D4, D5)

- [ ] **4.1 Add `RunnerIdleReason` `StrEnum` to `runner.py`**
  - [ ] Members: `NOTHING_DUE`, `NO_ACTIONABLE_WORK`.
  - Effort: 1

- [ ] **4.2 Replace `did_anything` with a tracked reason in `_loop`, with its tests**
  - [ ] An iteration that runs any cycle doing work clears the reason.
  - [ ] An iteration where a cycle ran and reported `nothing_actionable` sets
        `NO_ACTIONABLE_WORK`.
  - [ ] An iteration where no gate opened sets `NOTHING_DUE`.
  - [ ] **Minute never reports drained** (D4, corrected 20260803).
        `run_minute_cycle` returns `EMPTY` for a symbol with no actionable gap,
        which is indistinguishable from "fetched, got nothing", so
        `nothing_actionable` stays `False` on every minute report. Do **not**
        add a drained signal to the minute path. Consequence:
        `NO_ACTIONABLE_WORK` is reachable only for daily-only scopes, and
        minute-inclusive scopes behave exactly as they do today.
  - [ ] **Tests (written here):** one case per reason, plus a minute-inclusive
        case asserting the reason never resolves to `NO_ACTIONABLE_WORK` even
        when daily is drained — this pins the non-regression and will fail
        loudly if someone later gives minute a drained signal without revisiting
        D4.
  - Effort: 2

- [ ] **4.3 Report the reason on the `terminate_when_drained` path, with its tests**
  - [ ] `NO_ACTIONABLE_WORK` → `runner: no actionable work in scope — exiting
        because --stop-when-done`, including the un-actionable count when
        non-zero, which is a materially different statement from "drained".
  - [ ] Message text derives from the enum member; do not branch on strings.
  - [ ] **Tests (written here):** assert on the enum member reaching the exit
        path, plus a caplog assertion for the substring carrying the
        distinction. Do not assert full message strings.
  - Effort: 1

- [ ] **4.4 Sleep through a closed cadence gate instead of exiting (D5), with its tests**
  - [ ] When `terminate_when_drained` and the reason is `NOTHING_DUE`, call
        `sleep_until_next_due_event` and continue rather than returning —
        **but only while some configured granularity has never run a cycle in
        this process** (`last_daily_cycle_end_utc is None` or
        `last_minute_cycle_end_utc is None`, for granularities in scope).
        Otherwise exit as today.
  - [ ] This qualifier is mandatory, not an optimization. Without it
        `mt data daemon run --minute --list <name>` — where `--list` implies
        `--stop-when-done` — never terminates: minute can never report
        `NO_ACTIONABLE_WORK` (D4), so the loop would sleep and re-run the same
        scope forever. Verify against the invocation table in D5 before
        checking this off.
  - [ ] On entering the wait, log once at INFO naming the reason and the due
        time — e.g. `runner: no cycle due until 00:30 UTC — waiting (27m)
        because --stop-when-done`. Log on entry only, not once per 60 s tick.
  - [ ] **Verification of the condition — exhaustive, not sampled.**
        `_awaiting_first_cycle()` is a pure function of three inputs: the
        configured granularity set and the two nullable end-stamps. The input
        space is finite, so enumerate all eight reachable combinations with
        `@pytest.mark.parametrize` and assert the expected verdict for each:

        | granularities | daily stamp | minute stamp | expected |
        | --- | --- | --- | --- |
        | `{daily}` | `None` | — | `True` |
        | `{daily}` | set | — | `False` |
        | `{minute}` | — | `None` | `True` |
        | `{minute}` | — | set | `False` |
        | `{daily, minute}` | `None` | `None` | `True` |
        | `{daily, minute}` | `None` | set | `True` |
        | `{daily, minute}` | set | `None` | `True` |
        | `{daily, minute}` | set | set | `False` |

        Note the `(set, None)` row is defensive rather than reachable in
        practice — minute is due immediately when its stamp is `None`, and the
        stamp is written right after the try/except at `runner.py:379`, so
        minute cannot stay unstamped once its branch has run. Assert it anyway;
        the predicate must be correct on its own terms, not by relying on the
        loop's ordering.
  - [ ] **Wiring tests (written here):** an exhaustive predicate test cannot
        catch a correct predicate called in the wrong place, so also assert at
        the loop level that the wait is entered rather than exited; that the
        INFO message names the due time; and that the message is emitted once
        across several sleep ticks, not per tick.
  - [ ] **Regression guard:** the `--minute --list X` case gets an explicit test
        timeout so that if the qualifier is ever removed, the suite reports a
        failure rather than hanging. A hang in CI reads as infrastructure
        flakiness; a timeout failure names the cause.
  - Effort: 2

- [ ] **4.5 Document the Ctrl-C latency on `--stop-when-done`**
  - [ ] The flag's CLI help text notes the command may wait for the next due
        cycle, and that interrupt latency during that wait is up to 60 s
        (PEP 475: the handler sets a flag without raising, so `time.sleep`
        resumes; `cap_seconds` bounds it).
  - Success: `mt data daemon run --help` states both facts.
  - Effort: 1

**Commit checkpoint** — issue #6's substance is closed.

---

## Task 5 — Cross-cutting behavior tests

These span more than one task's work and could not be written earlier. Every
single-unit assertion already lives with its implementation above.

- [ ] **5.1 Interrupted pass resumes at the unreached symbols, through the runner**
  - [ ] Drive the full `Runner` loop with an injected clock and a
        `run_daily_cycle` that processes N of M symbols and then reports
        `should_continue() is False`; on the next tick within the same UTC day,
        assert the cycle receives exactly the M−N unreached symbols, in order.
  - [ ] This is success criterion 1 and the reason the slice exists — it must
        fail against `main`'s behavior. Verify that it does before moving on.
  - Effort: 2

- [ ] **5.2 No busy-poll when nothing is actionable**
  - [ ] With a frozen clock advanced in steps across several hours, assert at
        most one work-list derivation per `DAILY_CYCLE_RETRY_INTERVAL` and zero
        provider calls.
  - Effort: 2

- [ ] **5.3 `--stop-when-done` sleeps through a closed gate, then runs, then exits**
  - [ ] Clock at 00:13 UTC, daily-only scope, `terminate_when_drained=True`:
        assert the runner sleeps rather than returning, that the cycle runs once
        the injected clock passes 00:30, and that the loop then exits reporting
        `NO_ACTIONABLE_WORK` — the full sequence, not just the wait.
  - Effort: 2

- [ ] **5.4 Every invocation in D5's table behaves as tabulated**
  - [ ] Parameterize over the five rows of the D5 invocation table, asserting
        for each whether the runner waits and whether it exits.
  - [ ] The row that matters most: `--minute --list X` must run exactly one
        minute cycle and then **exit**. Give this case a real timeout so a
        regression fails as a test failure rather than a hung suite — it is the
        PM's routine minute-fetch invocation and the one D5 nearly broke.
  - [ ] `--minute` with no scope must not enter the D5 branch at all
        (`terminate_when_drained` is `False`).
  - Effort: 2

- [ ] **5.5 A scope of only calendar-less symbols terminates**
  - [ ] Reports `NO_ACTIONABLE_WORK` with a non-zero un-actionable count, exits
        under `--stop-when-done`, and does not loop or issue provider calls.
  - Effort: 1

---

## Task 6 — Gates, verification, integration, and closure

- [ ] **6.1 Local gates**
  - [ ] `uv run --extra dev ruff check src/ test/` — no new violations.
  - [ ] `uv run --extra dev mypy` — no new errors in touched files (the
        repo-wide baseline is slice 905's to clear, not this slice's).
  - [ ] `uv run --extra dev pytest test/unit/data/acquisition/daemon/ test/unit/test_constants.py`
        passes.
  - Effort: 1

- [ ] **6.2 Prod verification on .144 (success criterion 12)**
  - [ ] Requires the PM to have promoted the change to `main` and .144 to have
        pulled and restarted — this task is **blocked** until then and must not
        be checked off on the basis of local testing.
  - [ ] Stop the daemon mid-pass; restart within the same UTC day; confirm from
        `acquisition_state` that it resumes at the unreached symbols rather than
        re-running the alphabet.
  - [ ] Every verification query sets `statement_timeout` and avoids expression
        aggregates over compressed hypertables.
  - [ ] Record in `project-documents/user/notes/912-prod-verify-<date>.md`.
  - Effort: 2

- [ ] **6.3 Code review**
  - [ ] `sq review code 912` — PM selects the model. Disposition findings in the
        slice document before integration.
  - Effort: 1

- [ ] **6.4 Integrate**
  - [ ] Merge `912-slice.daemon-cycle-correctness` into
        `trading-data-maintenance`. Confirm the target immediately before
        merging — merging a 9xx slice to `main` is wrong (see Branch above).
  - [ ] Do not delete the branch.
  - [ ] Update the slice plan entry and slice/task frontmatter to `complete`.
  - Effort: 1

- [ ] **6.5 Close the issues**
  - [ ] Close #7 and #6 with a comment naming the slice and the commits, and
        stating what changed for an operator: the daemon now retries an
        interrupted daily pass within the same UTC day, and `--stop-when-done`
        no longer claims completion when it merely hit a closed gate.
  - [ ] Note in both comments that the fix is on `trading-data-maintenance` and
        reaches .144 only on the next promotion to `main` — closing an issue
        must not imply prod is fixed when it is not.
  - [ ] Do **not** close #4 — 912 reports calendar-less instruments, it does not
        fix them. Add a comment on #4 noting that 912 now surfaces the count.
  - Effort: 1
