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
  technical finding against D1. Closes GitHub issues #7 and #6. Daemon runs
  continuously on prod .144 from a git checkout, so every change here reaches
  production on the next restart.
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

### Branch

Work on `912-slice.daemon-cycle-correctness`, created from
`trading-data-maintenance` — **not** from `main`. The Phase 0–5 planning commits
for this slice (`1c096d5`, `39998f9`, `2dc2021`) live on the worktree branch, so
a branch forked from `main` would not contain its own design. Confirm the
eventual merge target with the PM before integrating.

---

## Task 1 — Constants: split the double-duty grace period (D3)

- [ ] **1.1 Add `DAILY_CYCLE_START_OFFSET` to `constants.py`**
  - [ ] `DAILY_CYCLE_START_OFFSET: timedelta = timedelta(minutes=30)`
  - [ ] Docstring states its actual role: how long after **UTC midnight** the
        daemon waits for the provider's late bars before starting a daily pass.
        Note explicitly that it is not a session-close offset and that its
        equality with `LATE_BAR_GRACE_PERIOD` is coincidental.
  - Success: the constant exists with its own docstring; `LATE_BAR_GRACE_PERIOD`
    keeps its session-close docstring unchanged.
  - Effort: 1

- [ ] **1.2 Add `DAILY_CYCLE_RETRY_INTERVAL` to `constants.py`**
  - [ ] `DAILY_CYCLE_RETRY_INTERVAL: timedelta = timedelta(minutes=15)`
  - [ ] Docstring states it is a busy-loop guard — how soon an interrupted daily
        pass may resume within the same UTC day — and explicitly **not** a
        statement about how often daily data changes. Record the sizing
        rationale: ~94 no-op ticks/day worst case, each a small-table read.
  - Effort: 1

- [ ] **1.3 Move `runner.py` onto the new offset**
  - [ ] Replace all three `LATE_BAR_GRACE_PERIOD` uses — `daily_cycle_due`
        (:135), `ca_update_due` (:170), `sleep_until_next_due_event` (:202) —
        with `DAILY_CYCLE_START_OFFSET`.
  - [ ] Remove the `LATE_BAR_GRACE_PERIOD` import from `runner.py`.
  - Success: `grep -n LATE_BAR_GRACE_PERIOD src/manta_trading/data/` returns
    nothing; the constant's remaining uses are the migration and `data_status`
    paths only.
  - Effort: 1

- [ ] **1.4 Assert both constants independently in `test_constants.py`**
  - [ ] Existing `LATE_BAR_GRACE_PERIOD == timedelta(minutes=30)` assertion
        stays; add the same for both new constants.
  - [ ] Add a comment at the assertions noting the values are independent, so a
        future tuning of one does not get "fixed" by copying the other.
  - Success: `uv run --extra dev pytest test/unit/test_constants.py` passes.
  - Effort: 1

---

## Task 2 — Derive the daily work list (D1, D6)

- [ ] **2.1 Add `pending_daily_symbols()` to `daily.py`**
  - [ ] Signature: `(conn, symbol_list: list[str], pass_boundary: datetime) -> DailyWorkList`
        where `DailyWorkList` is a small frozen dataclass carrying
        `pending: list[str]` and `unactionable_no_calendar: list[str]`.
  - [ ] One parameterized SQL statement over `instruments` LEFT JOIN
        `acquisition_state` (`granularity = 'daily'`, `provider = 'eodhd'`),
        classifying each scope member:
        - **un-actionable** if no `trading_sessions` row exists for the
          instrument's `trading_calendar_id` — the same join
          `_last_completed_session` uses, evaluated once for the whole scope
          rather than per symbol;
        - **done** if `last_attempt_ts >= pass_boundary`;
        - **pending** otherwise (including `last_attempt_ts IS NULL` and
          missing `acquisition_state` rows — never call `.date()` on `None`).
  - [ ] Preserve the caller's ordering for `pending` (`iter_active_instruments`
        already orders `most_stale_first`); do not re-sort.
  - Success: unit-tested against a mock connection for each branch, including a
    symbol with no `acquisition_state` row and one with a NULL `last_attempt_ts`.
  - Effort: 2

- [ ] **2.2 Extend `CycleReport` with the un-actionable count and a drained flag**
  - [ ] `unactionable_no_calendar: int = 0`
  - [ ] `nothing_actionable: bool = False` — set when the derived pending set is
        empty, so the runner can classify the idle reason without re-deriving it
        (D4). Default `False` keeps `run_minute_cycle`'s use of `CycleReport`
        unchanged.
  - Success: `run_minute_cycle` compiles and its tests pass untouched.
  - Effort: 1

- [ ] **2.3 Wire the work list into `run_daily_cycle`**
  - [ ] After `symbol_list` is resolved (`daily.py:103-112`), compute the pass
        boundary as today's UTC midnight + `DAILY_CYCLE_START_OFFSET` and call
        `pending_daily_symbols`.
  - [ ] If `pending` is empty: set `nothing_actionable = True`, log at INFO with
        the scope size and the un-actionable count, and **return before any
        provider call** — including before `_select_daily_mode`.
  - [ ] Otherwise pass `pending` (not `symbol_list`) to `_select_daily_mode` and
        to both the STEADY_STATE and BACKFILL loops.
  - Success: with every scope symbol stamped at/after the boundary, the cycle
    issues zero HTTP requests. Assert this with a mock `httpx.Client` that fails
    the test if called.
  - Effort: 2

- [ ] **2.4 Report un-actionable symbols once per cycle, never per symbol**
  - [ ] Log at WARNING with the count and a pointer to issue #4 — not 906
        individual lines. Include up to a handful of example symbols for
        diagnosis.
  - [ ] Leave `_run_steady_state_cycle`'s existing per-symbol
        `target_end is None` branch (`daily.py:261-267`) in place as a
        belt-and-braces guard; it should now be unreachable for scope members,
        since 2.1 excludes them upstream.
  - Success: a scope of N calendar-less symbols produces exactly one WARNING.
  - Effort: 1

- [ ] **2.5 Check downstream consumers of `report.total`**
  - [ ] `report.total` now counts pending symbols, not full scope. Audit the
        `-v` progress output and any `mt data daemon` rendering that derives a
        denominator from it, and correct anything that would now display
        "processed 3 of 3" for a 5,900-symbol universe.
  - Success: named explicitly in the commit message either as "no consumer
    affected, verified" or with the fix.
  - Effort: 1

---

## Task 3 — Demote the day-timer to a cadence guard (D2)

- [ ] **3.1 Rename the `RunnerState` field**
  - [ ] `last_daily_cycle_start_utc` → `last_daily_cycle_end_utc`. Update the
        dataclass docstring; the rename is load-bearing, not cosmetic.
  - Effort: 1

- [ ] **3.2 Rewrite `daily_cycle_due` as a pure cadence predicate**
  - [ ] Returns `False` before today's `midnight + DAILY_CYCLE_START_OFFSET`.
  - [ ] Returns `True` when `last_daily_cycle_end_utc is None`.
  - [ ] Otherwise `True` iff `now - last_daily_cycle_end_utc >= DAILY_CYCLE_RETRY_INTERVAL`.
  - [ ] Rewrite the docstring to state the division of responsibility in the
        same terms `minute_cycle_due` uses: whether any scope member has
        actionable daily work is determined inside `run_daily_cycle` itself; the
        runner gates on cadence so it does not busy-loop.
  - Success: no UTC-day comparison remains in the predicate.
  - Effort: 1

- [ ] **3.3 Stamp completion, not start, in `_loop`**
  - [ ] Delete the pre-`try` assignment at `runner.py:355`.
  - [ ] Assign `self._state.last_daily_cycle_end_utc = self._clock()` after the
        `try/except` around `_run_daily_cycle`, matching the minute branch at
        `runner.py:379`. It is stamped on the exception path too — a cycle that
        raised still consumed its cadence slot, and retrying it instantly would
        busy-loop against a persistent failure.
  - Effort: 1

- [ ] **3.4 Update `sleep_until_next_due_event`**
  - [ ] The next daily wake is now `last_daily_cycle_end_utc +
        DAILY_CYCLE_RETRY_INTERVAL` when a cycle has ended today and the day's
        offset has passed, else the day's start boundary (tomorrow's if today's
        has passed and no retry is pending).
  - [ ] Keep `cap_seconds=60` and its SIGTERM-latency rationale untouched.
  - Success: `test_sleep_caps_at_60s` still passes.
  - Effort: 2

---

## Task 4 — Honest idle reporting (D4, D5)

- [ ] **4.1 Add `RunnerIdleReason` `StrEnum` to `runner.py`**
  - [ ] Members: `NOTHING_DUE`, `NO_ACTIONABLE_WORK`.
  - Effort: 1

- [ ] **4.2 Replace `did_anything` with a tracked reason in `_loop`**
  - [ ] An iteration that runs any cycle doing work clears the reason.
  - [ ] An iteration where a cycle ran and reported `nothing_actionable` sets
        `NO_ACTIONABLE_WORK`.
  - [ ] An iteration where no gate opened sets `NOTHING_DUE`.
  - [ ] Where both apply across granularities, `NO_ACTIONABLE_WORK` wins only if
        every configured granularity is drained; otherwise `NOTHING_DUE`.
  - Effort: 2

- [ ] **4.3 Report the reason on the `terminate_when_drained` path**
  - [ ] `NO_ACTIONABLE_WORK` → `runner: no actionable work in scope — exiting
        because --stop-when-done`, including the un-actionable count when
        non-zero, which is a materially different statement from "drained".
  - [ ] Message text derives from the enum member; do not branch on strings.
  - Effort: 1

- [ ] **4.4 Sleep through a closed cadence gate instead of exiting (D5)**
  - [ ] When `terminate_when_drained` and the reason is `NOTHING_DUE`, call
        `sleep_until_next_due_event` and continue rather than returning.
  - [ ] On entering the wait, log once at INFO naming the reason and the due
        time — e.g. `runner: no cycle due until 00:30 UTC — waiting (27m)
        because --stop-when-done`. Log on entry only, not once per 60 s tick.
  - [ ] Verify termination: the wait must be bounded by the daily start offset
        (or one minute, when minute is in scope), and the loop must exit once
        the gate opens and the cycle reports `NO_ACTIONABLE_WORK`.
  - Effort: 2

- [ ] **4.5 Document the Ctrl-C latency on `--stop-when-done`**
  - [ ] The flag's CLI help text notes the command may wait for the next due
        cycle, and that interrupt latency during that wait is up to 60 s
        (PEP 475: the handler sets a flag without raising, so `time.sleep`
        resumes; `cap_seconds` bounds it).
  - Success: `mt data daemon run --help` states both facts.
  - Effort: 1

---

## Task 5 — Tests (success criteria 1–6)

- [ ] **5.1 Interrupted pass resumes at the unreached symbols**
  - [ ] Stamp M−N symbols as attempted after the pass boundary; assert
        `pending_daily_symbols` returns exactly the N unreached ones, in order.
  - Effort: 2

- [ ] **5.2 A completed pass does not re-run within the same UTC day**
  - [ ] All symbols stamped after the boundary → `pending` empty,
        `nothing_actionable` True, zero HTTP calls.
  - Effort: 1

- [ ] **5.3 No busy-poll when nothing is actionable**
  - [ ] With a frozen clock advanced in steps, assert at most one work-list
        derivation per `DAILY_CYCLE_RETRY_INTERVAL` and zero provider calls.
  - Effort: 2

- [ ] **5.4 `--stop-when-done` sleeps through a closed gate, then runs**
  - [ ] Clock at 00:13 UTC, daily-only scope, `terminate_when_drained=True`:
        assert the runner sleeps rather than returning, that the INFO wait
        message names the 00:30 due time, and that the cycle runs once the
        injected clock passes it.
  - Effort: 2

- [ ] **5.5 Exit messages distinguish the two idle reasons**
  - [ ] Assert on the enum member reaching the exit path — plus a caplog
        assertion that `NOTHING_DUE` names the next due time. Do not assert
        exact message strings beyond the substring that carries the distinction.
  - Effort: 1

- [ ] **5.6 Calendar-less symbols are excluded and counted**
  - [ ] A scope mixing actionable and calendar-less symbols yields the
        actionable ones in `pending`, the rest in `unactionable_no_calendar`,
        and exactly one WARNING.
  - [ ] A scope of **only** calendar-less symbols reports
        `NO_ACTIONABLE_WORK` with a non-zero un-actionable count and does not
        loop.
  - Effort: 2

- [ ] **5.7 Existing runner tests updated, not deleted**
  - [ ] `test_daily_cycle_due_false_when_last_cycle_was_today` and
        `test_daily_cycle_due_true_after_utc_day_rollover` encode the old
        once-per-day semantics and must be rewritten to the cadence semantics.
        Rewriting them is expected; silently dropping either is not.
  - Effort: 1

---

## Task 6 — Gates, verification, and closure

- [ ] **6.1 Local gates**
  - [ ] `uv run --extra dev ruff check src/ test/` — no new violations.
  - [ ] `uv run --extra dev mypy` — no new errors in touched files (the
        repo-wide baseline is slice 905's to clear, not this slice's).
  - [ ] `uv run --extra dev pytest test/unit/data/acquisition/daemon/ test/unit/test_constants.py`
        passes.
  - Effort: 1

- [ ] **6.2 Prod verification on .144 (success criterion 12)**
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

- [ ] **6.4 Close the issues**
  - [ ] Close #7 and #6 with a comment naming the slice and the commits, and
        stating what changed for an operator: the daemon now retries an
        interrupted daily pass within the same UTC day, and `--stop-when-done`
        no longer claims completion when it merely hit a closed gate.
  - [ ] Do **not** close #4 — 912 reports calendar-less instruments, it does not
        fix them. Add a comment on #4 noting that 912 now surfaces the count.
  - Effort: 1
