---
docType: slice-design
slice: daemon-cycle-correctness
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: []
interfaces: [145, 154]
dateCreated: 20260803
dateUpdated: 20260803
status: complete
---

# Slice Design: Daemon Cycle Correctness — Data-Driven Daily Work Determination

## Overview

The daily acquisition cycle decides whether it has work from
`RunnerState.last_daily_cycle_start_utc`. That field has two properties that
combine badly.

**It records a start, not a completion.**
[runner.py:355-357](../../../src/manta_trading/data/acquisition/daemon/runner.py)
stamps it *before* invoking the cycle:

```python
self._state.last_daily_cycle_start_utc = now
try:
    self._run_daily_cycle(...)
```

A pass that dies partway — SIGTERM between symbols, an exception, an
interrupted session — has already marked the UTC day as done. `daily_cycle_due`
then returns `False` for the remainder of that day. The daemon does not retry,
and reports nothing wrong.

**It is in-memory only.** `RunnerState` is documented as "cleared at process
start"
([runner.py:112](../../../src/manta_trading/data/acquisition/daemon/runner.py)),
so the stamp does not survive a restart — and that second failure *masks* the
first. Every restart re-runs a full pass unconditionally, so in practice
operators only ever observe the recovery path and the cycle appears to work. A
long-lived daemon that is not restarted is the case where an interrupted pass
silently goes unretried until the next UTC day. Observed on prod 2026-08-03: a
daily pass was interrupted partway through the alphabet, and the operator's
recovery was to stop and restart the process, not anything the daemon did.

The minute cycle already solves this. `minute_cycle_due` gates only on cadence —
one minute since the previous cycle's **end** — and its docstring states the
division of responsibility explicitly: whether any scope member has actionable
gaps is determined inside `run_minute_cycle` itself, `data_gaps`-driven, and the
runner just gates on cadence so it does not busy-loop. Interruption, restart,
and host sleep are all self-healing there, because completion is not tracked —
remaining work is derived.

This slice converges daily on that model, and fixes the second defect in the
same forty lines (#6): the `--stop-when-done` exit path cannot tell "no cycle
due yet" from "scope drained," so a scoped run launched inside the post-midnight
grace window exits instantly claiming completion, having fetched nothing.

Closes [#7](https://github.com/manta-digital/trading-data/issues/7) and
[#6](https://github.com/manta-digital/trading-data/issues/6).

## Value

The daemon is the only thing keeping daily data current across ~5,900
instruments. Today its work-determination is a timer that can be wrong in one
direction only — claiming done when it is not — and nothing in the logs, the
exit code, or `mt data status` distinguishes a clean pass from one that died at
`C`. Every silent partial day becomes a hole that only shows up later as a gap
someone has to notice.

The cost of the current recovery path is low — one bulk `/eod-bulk-last-day`
call plus upserts — so this is a correctness and observability problem, not a
quota one. That also means the fix is cheap to get right: the durable per-symbol
truth already exists and is already written on every path.

## Dependencies

### Prerequisites

None. Both defects are self-contained in the daemon runner and the daily cycle.

### Interfaces required

- **145** — `run_daily_cycle`'s `data_gaps`-driven contract, the pool-per-cycle
  ownership rule, and `update_data_gaps` as the single writer of
  `acquisition_state`.
- **154** — the `mt data daemon run` CLI surface, including the
  `--stop-when-done` / `terminate_when_drained` default
  ([data.py:1181-1185](../../../src/manta_trading/cli/commands/data.py)).
Slice 168's `assert_cagg_fresh` is discussed in D1 as the guard the rejected
coverage-based alternative would have required. It is not an interface this
slice consumes and is deliberately absent from the list above.

## Technical decisions

### D1 — The resume set derives from `acquisition_state.last_attempt_ts`, not from coverage

Issue #7 proposes deriving the work list from "symbols whose daily coverage is
behind `_last_completed_session` for their calendar," and separately notes that
"the durable per-symbol truth already exists (`acquisition_state.updated_at` /
`last_attempt_outcome`, and `data_gaps`)." This slice takes the second of those
two sanctioned signals. The reasoning matters, because the first looks more
principled and is worse in practice:

- **It is already written on both daily paths.** `update_data_gaps` synchronizes
  `data_gaps` *and* upserts `acquisition_state` with `last_attempt_ts` and
  `last_attempt_outcome`
  ([update_data_gaps.py:328-338](../../../src/manta_trading/data/gaps/update_data_gaps.py)),
  and both `_run_steady_state_cycle`
  ([daily.py:282](../../../src/manta_trading/data/acquisition/daemon/daily.py))
  and `_do_daily_symbol`
  ([daily.py:414](../../../src/manta_trading/data/acquisition/daemon/daily.py))
  call it per symbol. An interrupted pass therefore leaves exactly the unreached
  symbols un-stamped for the current pass. No new bookkeeping is introduced —
  the resume set is read out of writes that already happen.

- **It is cheap.** `acquisition_state` is one small row per (symbol,
  granularity, provider). The coverage alternative requires reading
  `daily_coverage`, which is a continuous aggregate, which per slice 168 must be
  guarded by `assert_cagg_fresh` before it may be trusted — dragging a ~1 s
  freshness probe and a whole failure mode into the hot gate.

- **It terminates.** A symbol that legitimately has no bar for the session
  (halted, thin, newly delisted) is stamped `empty` and drops out of the work
  list. Under the coverage comparison it would remain permanently "behind" its
  calendar's last completed session, so the derived work list would never empty
  — and because the list drives the cycle, that means re-issuing the billable
  bulk `/eod-bulk-last-day` call on **every cadence tick**, forever. This is the
  single largest risk the slice has to avoid, and D1 avoids it structurally
  rather than by adding a suppression rule.

The coverage-versus-session comparison is not discarded; it is already where it
belongs. `_select_daily_mode`
([daily.py:162-199](../../../src/manta_trading/data/acquisition/daemon/daily.py))
consults gap state and bar presence to choose `STEADY_STATE` (one bulk call) vs
`BACKFILL` (per-symbol `/eod`), and any genuine hole left by an `empty` outcome
is recorded in `data_gaps` by the same `update_data_gaps` call, which is what
drives the next cycle into `BACKFILL`. The safety net exists; it does not need
to be duplicated in the gate.

**Pass boundary.** "Attempted in the current pass" means
`acquisition_state.last_attempt_ts >= today's 00:00 UTC + DAILY_CYCLE_START_OFFSET`
(D3). A symbol stamped at 00:35 today is done for the day; one stamped
yesterday at 04:00 is pending. This is a date-grain question deliberately
resolved against an explicit boundary rather than `::date`, so the boundary and
the cycle's start gate are the same expression.

**Transient failures deliberately do not stamp.** In `_run_steady_state_cycle`
the `update_data_gaps` call sits inside the `try`, so an advisory-lock timeout
or an unexpected exception skips it. The symbol stays un-stamped and is retried
on the next tick. That is the behavior we want, and it falls out of the existing
structure rather than needing to be added.

### D2 — The day-timer becomes a busy-loop cadence guard

`RunnerState.last_daily_cycle_start_utc` is renamed `last_daily_cycle_end_utc`
and stamped **after** `_run_daily_cycle` returns, exactly mirroring
`last_minute_cycle_end_utc`
([runner.py:379](../../../src/manta_trading/data/acquisition/daemon/runner.py)).
`daily_cycle_due` becomes a pure cadence predicate:

```
past today's DAILY_CYCLE_START_OFFSET boundary
AND (no cycle has ended yet OR now - last_end >= DAILY_CYCLE_RETRY_INTERVAL)
```

Whether there is anything to do moves inside `run_daily_cycle`, which is where
it is for minute. `DAILY_CYCLE_RETRY_INTERVAL` is a new constant in
`constants.py` with a docstring stating its role — it exists so an interrupted
pass retries within the same UTC day without the loop spinning, not to express
any policy about how often daily data changes.

The retry interval must be long enough that a work-list query per tick is
negligible and short enough that an interruption is recovered promptly. Shipped
at 15 minutes and **raised to 30 after the code review** (F002): each retry
during a provider outage re-issues the bulk EOD call, and observed catch-up once
the provider recovers is under two hours, so a faster poll bought no recovery
speed the fetch did not already bound. A fully-drained scope now costs ~47 no-op
ticks per day, each one a small-table read with no provider call.

It is also no longer only a constant: `MT_DAILY_CYCLE_RETRY_MINUTES` and
`--daily-retry-minutes` override the default, because the right value is
empirical and belongs to whoever is watching the daemon, not to this document.

The field rename is not cosmetic: keeping the name `..._start_utc` while
stamping at the end is exactly the kind of drift that produced this bug.
`sleep_until_next_due_event` is updated in step, since it computes the next
daily start from the same boundary
([runner.py:200-211](../../../src/manta_trading/data/acquisition/daemon/runner.py)).

### D3 — Split `LATE_BAR_GRACE_PERIOD` into `DAILY_CYCLE_START_OFFSET`

`LATE_BAR_GRACE_PERIOD` is documented as "Grace period after `session_close_utc`
before a day is considered completed"
([constants.py:148](../../../src/manta_trading/constants.py)), and that is how
the `data_status` view and migration 043 use it. `runner.py` applies the same
value as an offset from **UTC midnight** in three places (lines 135, 170, 202),
which is not a session close. Two different concepts are sharing one value by
coincidence, and they will drift the moment either is tuned.

Add `DAILY_CYCLE_START_OFFSET: timedelta = timedelta(minutes=30)` with its own
docstring naming its actual role — how long after UTC midnight the daemon waits
for the provider's late bars before starting a daily pass — and move all three
`runner.py` uses (`daily_cycle_due`, `ca_update_due`,
`sleep_until_next_due_event`) onto it. `LATE_BAR_GRACE_PERIOD` keeps its
session-close meaning and its migration/`data_status` uses, untouched. The
values are equal today; nothing may rely on that. `test_constants.py` asserts
each independently.

### D4 — Idle reasons are an enum, and the exit message states which one

`_loop`'s `did_anything` boolean conflates two states
([runner.py:385-390](../../../src/manta_trading/data/acquisition/daemon/runner.py)):
a cadence gate that has not opened yet, and a scope with genuinely nothing left
to do. Only the second deserves "scope drained."

Introduce a `RunnerIdleReason` `StrEnum` — `NOTHING_DUE`, `NO_ACTIONABLE_WORK` —
and have the loop track the reason rather than a bare bool. Per the project's
no-magic-strings rule this is an enum, not a log-message variant. The
`terminate_when_drained` branch reports the reason, and for `NOTHING_DUE` names
the next due time:

```
runner: no cycle due yet (daily due at 00:30 UTC) — exiting because --stop-when-done
runner: no actionable work in scope — exiting because --stop-when-done
```

`run_daily_cycle` already returns a report; it gains the fact that it found
nothing actionable so the runner can classify without re-deriving.

**Minute never reports drained, and that is deliberate** (corrected during
implementation — see below). `run_minute_cycle` iterates every scope symbol
every cycle and returns `EMPTY` for a symbol with no actionable gap
([minute.py:160-187](../../../src/manta_trading/data/acquisition/daemon/minute.py)),
which is indistinguishable from "fetched and got no data." There is no drained
signal to read, and adding one would mean changing the minute path this slice
exists to copy, not to modify.

Therefore `nothing_actionable` is always `False` on a minute report, and
`NO_ACTIONABLE_WORK` is reachable **only for daily-only scopes**. For any
minute-inclusive scope the runner reports `NOTHING_DUE` or nothing at all,
exactly as today: `did_anything` is currently set whenever a cycle *ran* rather
than when it did work, and the minute gate opens every minute, so
`terminate_when_drained` never fires for those scopes in the first place. This
is why the reported #6 incident was a `--daily` run. Preserving that is a
non-regression, not a compromise; giving minute a real drained signal is
follow-on work for the initiative that owns it.

### D5 — A `--stop-when-done` run satisfies a cadence gate by sleeping, not by exiting

This is the one operator-visible behavior change, and it is the substance of
issue #6's second suggestion.

`terminate_when_drained` defaults to true for scoped runs
([data.py:1181-1185](../../../src/manta_trading/cli/commands/data.py)), so
`mt data daemon run --symbols AAPL` launched at 00:13 UTC exits immediately
having fetched nothing — the reported incident. With D4 the message would at
least be honest, but the run still does no work when the user plainly asked for
data.

Under D5, when the reason is `NOTHING_DUE`, the loop sleeps until the gate opens
(via the existing `sleep_until_next_due_event`, whose `cap_seconds` already
bounds SIGTERM latency) rather than exiting — **but only when some configured
granularity has not yet run a cycle in this process**, i.e. its
`last_*_cycle_end_utc` is still `None`. Otherwise it exits as it does today.

That qualifier is load-bearing, not a refinement (corrected during
implementation — see Corrections). Without it, `mt data daemon run --minute
--list <name>` never terminates. Today that run does exactly one pass and
exits: iteration 1 runs the cycle because `last_minute_cycle_end_utc is None`,
iteration 2 finds the one-minute gate closed and takes the terminate branch.
An unqualified D5 would sleep at iteration 2, re-run the identical scope, sleep
again — forever, because minute publishes no drained signal (D4) and so can
never reach `NO_ACTIONABLE_WORK`.

The qualifier expresses the actual principle: sleeping is worth it only when
waiting makes a *new* kind of progress possible. A closed daily start-offset
gate at 00:13 means the day's first pass has not happened and the data is not
published yet — worth 17 minutes. A closed minute cadence gate means a pass over
that exact scope just finished — waiting only repeats it. Because a granularity's
end-stamp is `None` only before its first cycle, this permits at most one wait
per granularity per process, which is what bounds the whole behavior. `--stop-when-done` then means what it says: exit when
there is no work, not when there is no *cycle*.

The wait is bounded and narrow. It occurs **only** in the 00:00–00:30 UTC
window: outside it a fresh process has `last_daily_cycle_end_utc = None`, so
under D2 the cycle is due immediately and there is no wait at all. It occurs
**only** when `daily` is in the granularity set, since a minute gate is never
closed before minute's first pass. And unscoped runs are unaffected — they
already default to `terminate_when_drained = False` and already sleep. Worst
case is therefore a 30-minute block, for a scoped run including `--daily`
launched at 00:00 UTC.

Concretely, against the flag combinations in use:

| Invocation | `terminate_when_drained` | Change |
| --- | --- | --- |
| `--minute` (no scope) | `False` | None — D5 branch unreachable |
| `--minute --list X` / `--symbols` | `True` | None — minute's end-stamp is set after its first pass, so the qualifier blocks the wait and it exits after one pass as today |
| `--daily --list X` at 00:13 UTC | `True` | Waits to 00:30, runs, then exits — the #6 fix |
| `--daily --list X` at 14:00 UTC | `True` | None — daily is due immediately |
| `--daily --minute --list X` at 00:13 | `True` | Minute runs first; waits to 00:30 for daily's first pass, then exits |

Two consequences follow, and both are requirements, not caveats:

- **The wait must announce itself.** On entering it, log at INFO naming the
  reason and the due time — e.g. `runner: no cycle due until 00:30 UTC —
  waiting (27m) because --stop-when-done`. A silent multi-minute wait is
  indistinguishable from a hang, and replacing a misleading exit message with a
  silent stall would trade one observability defect for another.
- **Ctrl-C latency during the wait is up to `cap_seconds` (60 s), not
  instant.** The signal handler sets `_should_exit` and returns without raising,
  so under PEP 475 `time.sleep` resumes for its remaining time; the loop only
  observes the flag at the top of the next iteration. This is exactly what
  `sleep_until_next_due_event`'s `cap_seconds` exists to bound, per its own
  docstring, and it is pre-existing behavior — but D5 is what makes an operator
  likely to encounter it interactively, so it must be stated in the operator
  documentation for `--stop-when-done` rather than discovered.

### D6 — Scope members with no resolvable calendar are counted, not dropped

`_last_completed_session` returns `None` when an instrument has no matching
`trading_calendar_id`
([daily.py:491-509](../../../src/manta_trading/data/acquisition/daemon/daily.py)),
and `_run_steady_state_cycle` logs a warning and `continue`s **without** calling
`update_data_gaps`
([daily.py:261-267](../../../src/manta_trading/data/acquisition/daemon/daily.py)).
No stamp is written — which under D1 means those symbols are never "attempted"
and would sit in the derived work list forever, re-triggering the cycle on every
tick. That is precisely the non-termination D1 was chosen to avoid, arriving by
another door.

Issue [#4](https://github.com/manta-digital/trading-data/issues/4) reports 906
instruments in this state. Assigning them calendars is #4's job and explicitly
not this slice's. What *is* this slice's job is refusing to let a derived work
list be dishonest about them: the work-list query classifies scope members with
no resolvable calendar as **un-actionable**, excludes them from the pending set
so the cycle can terminate, and carries the count on the cycle report. The
runner logs it once per cycle at WARNING with a count and a pointer, never
per-symbol — 906 warnings per pass is how the current behavior became invisible.

A scope whose only remaining members are un-actionable reports
`NO_ACTIONABLE_WORK` with a non-zero un-actionable count, which is a materially
different statement from "scope drained" and must read that way in the log.

## Scope

**In scope**

- `daily_cycle_due`, `RunnerState`, `sleep_until_next_due_event`, and `_loop` in
  `data/acquisition/daemon/runner.py`.
- A work-list derivation for daily, and the "nothing actionable" outcome on the
  cycle report, in `data/acquisition/daemon/daily.py`.
- `DAILY_CYCLE_START_OFFSET` and `DAILY_CYCLE_RETRY_INTERVAL` in `constants.py`;
  moving `runner.py`'s three `LATE_BAR_GRACE_PERIOD` uses onto the former.
- `RunnerIdleReason` and the `terminate_when_drained` exit path.
- Unit tests in `test/unit/data/acquisition/daemon/test_runner.py` and the daily
  cycle's tests; `test/unit/test_constants.py` for the constant split.

**Out of scope**

- Assigning trading calendars to the 906 instruments (#4). This slice reports
  them; it does not fix them.
- Any change to `run_minute_cycle` or the minute gate. The minute path is the
  model being copied, not the thing being changed.
- `data/acquisition/daily/freshness.py`, which is AlphaVantage-era
  (`OutputSize`, `outputsize`) and dead since AV was removed. Noted for a
  future cleanup; deleting it here would mix an unrelated removal into a
  correctness fix.
- Reading `daily_coverage` or adding an `assert_cagg_fresh` call (D1).
- Changing quota accounting, provider behavior, or the bulk-vs-per-symbol mode
  selection.

## Success criteria

### Functional

1. A daily pass interrupted after N of M symbols, followed by another tick in
   the same UTC day, processes exactly the M−N unreached symbols — not zero, not
   all M.
2. A daily pass that completes does not re-run within the same UTC day, however
   many ticks occur.
3. With no actionable work in scope, the loop does not busy-poll: at most one
   work-list query per `DAILY_CYCLE_RETRY_INTERVAL`, and zero provider calls.
4. `--stop-when-done` with a cadence gate closed sleeps until the gate opens and
   then runs, rather than exiting (D5), and logs at INFO on entering the wait
   naming the reason and the due time.
5. `--stop-when-done` with a genuinely drained scope exits reporting
   `NO_ACTIONABLE_WORK`, and the message distinguishes it from `NOTHING_DUE`,
   which names the next due time.
6. Scope members with no resolvable calendar are excluded from the pending set,
   counted on the report, and logged once per cycle with that count.

### Technical

7. `RunnerState` carries `last_daily_cycle_end_utc`, stamped after the cycle
   returns; no field records a start (D2).
8. `DAILY_CYCLE_START_OFFSET` and `LATE_BAR_GRACE_PERIOD` are separate
   constants with separate docstrings and separate uses; `runner.py` imports
   only the former.
9. Idle reasons are an enum; no log-message string is used as logical structure.
10. `uv run --extra dev ruff check` and the daemon subpackage test suite pass;
    no new mypy errors in the touched files.

### Verification

11. Unit tests cover criteria 1–6 with an injected clock and mocked cycle
    functions, using the existing `test_runner.py` seams (`clock`, `sleep`,
    `run_daily_cycle`, `conn_factory`).
12. A prod verification on .144 after merge: stop the daemon mid-pass, restart
    it within the same UTC day, and confirm from `acquisition_state` that it
    resumes at the unreached symbols rather than re-running the alphabet.
    Recorded in `project-documents/user/notes/`.

## Slice review disposition (20260803)

Reviewed by `minimax/minimax-m3`; verdict CONCERNS, four PASS findings and two
concerns, both about placement rather than technical content
([912-review.slice.daemon-cycle-correctness.md](../reviews/912-review.slice.daemon-cycle-correctness.md)).

- **F005 — slice scope exceeds the parent architecture's stated scope.**
  Accepted and resolved in the architecture, not in the slice. The reviewer's
  point was exact: the "maintenance band" justification existed only inside this
  slice and was invisible in the document that defines the band, so nothing
  distinguished a sanctioned maintenance slice from scope creep. The PM's
  2026-08-03 decision that 900-999 is the maintenance band is now recorded in
  [900-arch.foundation-cleanup.md](../architecture/900-arch.foundation-cleanup.md)
  §Overview, with two constraints — corrective rather than additive, and
  honoring rather than rewriting the originating initiative's contracts — that
  this slice satisfies: it fixes specified-and-wrong behavior, and it consumes
  slice 145's `update_data_gaps` contract without redefining it.
- **F006 — dependency direction requires acquisition-layer interfaces.**
  Same root cause, same resolution: the architecture's "900 precedes 100-180"
  statement is now scoped to the foundation slices, since a maintenance slice by
  construction comes after the work it corrects. The finding's secondary point
  was actionable and taken — slice 168 is consulted only as the precedent for
  D1's *rejected* alternative and has been dropped from the interfaces list.
- **F001-F004 — PASS** on the enum-over-boolean idle reason (D4), the failure
  modes and bounded Ctrl-C latency of the new wait path (D5), the constant split
  (D3), and structured logging.

No technical finding was raised against D1, which was the decision most at risk
of being read as non-compliance with issue #7's literal proposal.

## Task review disposition (20260803)

Reviewed by `minimax/minimax-m3`; verdict CONCERNS, four PASS findings, three
concerns and a note
([912-review.tasks.daemon-cycle-correctness.md](../reviews/912-review.tasks.daemon-cycle-correctness.md)).
All four addressed in the task breakdown.

- **F005 — tests batched at the end, violating test-with-implementation.**
  Accepted; the finding was correct and the diagnosis exact — seven subtasks
  ended with a *description* of an assertion rather than the assertion. Tests
  now live in the subtask that implements the behavior they cover. Task 5 was
  reduced to the four genuinely cross-cutting cases that span multiple tasks and
  cannot be written earlier.
- **F006 — Task 1.3's success criterion self-contradictory.** Accepted as a
  wording defect; rejected as a contradiction. The reviewer's premise — that the
  migration and `data_status` uses live under `src/manta_trading/data/` — is
  false: they are in `market/schema/migrations/minute.py`, so both clauses can
  hold simultaneously. The criterion nonetheless invited that reading, and is
  now two explicitly-stated greps with the reason they are consistent.
- **F007 — commit cadence unspecified given production reach.** The ask is
  accepted; its premise came from an error in this slice's own task metadata,
  now corrected. `projectState` claimed every change here reaches production on
  the next restart. It does not: .144 runs from a checkout tracking `main`, and
  912 merges to `trading-data-maintenance`, so nothing reaches prod until the PM
  promotes that branch. A commit-cadence section and per-task checkpoints were
  added anyway — for bisectability, which is the real benefit — and Task 6.2 is
  now explicitly blocked on promotion so it cannot be checked off from local
  testing, with Task 6.5 requiring the issue-closure comments to say the same.
- **F008 — branch note is information, not a tracked task.** Accepted. Merge
  target is now confirmed (PM, 2026-08-03: `trading-data-maintenance`), and
  integration is a tracked Task 6.4 that re-confirms the target immediately
  before merging rather than relying on a prose note read days earlier.
- **F001-F004 — PASS** on criteria coverage, task sequencing, correctly leaving
  #4 out of closure, and no spurious load-test requirement.

## Work-list SQL verified against production data (20260803)

Run by the PM in DataGrip during Task 2, before any of this reached a daemon.
The unit tests drive a mock cursor, so this is the only evidence the statement
itself is correct.

- **Unknown symbols surface, not vanish.** A scope containing a bogus symbol
  returned it with `has_calendar = false` rather than dropping it. Dropping is
  the failure D6 exists to prevent.
- **906 unactionable across the active universe** — matching GitHub issue #4's
  count exactly, arrived at independently. The calendar join is right; had it
  been over- or under-matching, this number would have moved.
- **Zero pending, 11,976 done** — consistent with a full daily pass having
  completed after 00:30 UTC that day. This is precisely the drained state D4
  reports as `NO_ACTIONABLE_WORK`.
- **12,882 instrument rows, 12,882 distinct symbols.** The scope is the registry
  size; there is no duplication today. See the fan-out correction below for why
  the query does not rely on that remaining true.

## Corrections discovered during implementation

- **The work-list join could fan out on `instruments.symbol` (found 20260803
  while reconciling the verification row counts).** `instruments` has primary
  key `instrument_id` and its UNIQUE constraint on `canonical_id`; `symbol`
  carries only a non-unique index. Joining scope straight to `instruments`
  would emit one row per instrument row per symbol, which would fetch a symbol
  repeatedly within a single pass and — where one row resolves a calendar and
  another does not — place the same symbol in *both* buckets, contradicting the
  disjointness `DailyWorkList` promises. Corrected to aggregate with
  `GROUP BY i.symbol` and `bool_or`, matching `_last_completed_session`'s
  equally permissive `MAX` over the same join; the two must agree or the work
  list would hand the cycle a symbol it then declines to fetch. Verification
  showed no duplicates today, so this was latent rather than active — but the
  schema permits them, and a symbol that delists and re-lists produces exactly
  that shape.

- **D5 would have made `mt data daemon run --minute --list <name>` never
  terminate (found 20260803, PM question during Task 1).** D5 originally slept
  through any `NOTHING_DUE`. For a minute-only scoped run — the PM's routine
  minute-fetch invocation, where `--list` implies `--stop-when-done` — the
  closed gate at iteration 2 is a one-minute cadence limiter, not a
  wait-for-data gate. Sleeping through it re-runs the identical scope forever,
  since D4 establishes minute can never report `NO_ACTIONABLE_WORK`. D5 now
  sleeps only while some configured granularity has never run a cycle in this
  process (`last_*_cycle_end_utc is None`), which permits at most one wait per
  granularity per process and leaves every minute-only invocation behaving
  exactly as it does today. This is the defect the slice came closest to
  shipping: it would have converted a routine operator command into a hang.

- **D4's cross-granularity drain rule was unevaluable (found during Task 1,
  20260803).** As originally written, D4 said `NO_ACTIONABLE_WORK` wins "only if
  every configured granularity is drained." The minute path publishes no such
  signal, so the runner could not evaluate the condition for any
  minute-inclusive scope. D4 now states the resolution explicitly: minute never
  reports drained, `NO_ACTIONABLE_WORK` is reachable only for daily-only scopes,
  and minute-inclusive behavior is unchanged from today. No minute code is
  touched, which was the point of the constraint the original wording violated.

## Code review disposition (20260803)

Verdict CONCERNS: six concerns, three notes, two passes. Every concern was
confirmed against the code before being acted on; nothing was accepted on the
reviewer's description alone. F010 and F011 are PASS findings requiring no
action.

| ID | Verdict | Disposition |
|----|---------|-------------|
| F001 | Confirmed | Fixed — `_run_steady_state_cycle` populates the caller's report |
| F002 | Confirmed, split | (a) fixed; (b) PM decision — retry semantics kept, cadence made configurable |
| F003 | Confirmed | Fixed — production reads the contract; the mocks were the defect |
| F004 | Confirmed | Fixed — new `daemon/cadence.py`, five copies collapsed to one |
| F005 | Confirmed | Fixed — write sites and config type converted to the enum |
| F006 | Confirmed | Fixed — and it exposed a hole in the 6.1 gate method |
| F007 | Accepted | Fixed — aggregate bounded to scope |
| F008 | Accepted (PM) | Fixed — unknown symbols get their own bucket and message |
| F009 | Rejected | Load-test rule applied out of context; see below |

### F001 — the STEADY_STATE path discarded the un-actionable counts

Confirmed by reading: `run_daily_cycle` set the counts, then rebound `report` to
`_run_steady_state_cycle`'s return value, which constructed a fresh
`CycleReport`. BACKFILL mutated in place and kept them, so the two modes
disagreed and the mode that lost the data is the production one.
`_run_steady_state_cycle` now takes the caller's report and populates it, which
removes the divergence rather than patching one side of it.

### F002 — an unstampable symbol makes the work list non-terminating

The finding is two problems, and they resolve differently.

**(a) A precision mismatch in the work-list SQL — fixed.**
`calendars_with_sessions` had no time bound while `_last_completed_session`
requires `session_open_utc < NOW()`. A calendar holding only future sessions
therefore satisfied the work list and failed the cycle: pending, skipped, never
stamped, pending again next tick. The SQL docstring asserted the two agreed,
which was simply wrong. Both now apply the same bound. This was the only
*permanent* unstampable condition, and it was a defect in this slice's own code.

**(b) Repeated transient failure — semantics kept, cadence made configurable.**
Every remaining no-stamp path is transient: the bulk call raising (whole scope),
advisory-lock timeout, unexpected exception, or any raise before
`update_data_gaps` on the BACKFILL path. Stamping those would have been the
wrong fix, and was rejected on analysis: D1 declines to stamp transient failures
precisely so the daemon self-heals within the day, and stamping them would mean
a five-minute provider blip at 00:30 defers the whole scope to tomorrow — a
worse regression than the credit spend.

The real exposure is rate, not retry. Corrected arithmetic, since the first pass
at it undercounted: `bucket.consume` sits *inside* `eodhd_get`'s retry loop, so
a timeout-style outage costs up to `MAX_RETRY_COUNT + 1` × 100 = 600 credits per
tick, not 100. At 94 ticks/day that is ~56,400 credits against a 100,000 daily
quota — enough that the QuotaBucket would throttle and starve the minute
pipeline. Not every mode loops: a 5xx returns a response, the bulk body fails to
parse, and every symbol is stamped EMPTY instead, which is a different problem
this slice does not introduce but does make terminal.

Set against that, the old code lost the entire daily pass when an outage
straddled 00:30, because the gate had closed until the next day. The extra
credits buy same-day recovery; that is the trade the slice exists to make.

PM decision: keep D1's retry semantics, and make the cadence operator-tunable
rather than guessing a number now. `DAILY_CYCLE_RETRY_INTERVAL` remains the
single definition site and the shipped default; `MT_DAILY_CYCLE_RETRY_MINUTES`
and `--daily-retry-minutes` override it, validated to `1..1440`. The two runner
predicates take it as a defaulted keyword argument, so they stay pure.

The PM then set the default to **30 minutes**, halving every figure above:
~47 ticks/day, and a full-day timeout-style outage bounded at ~28,200 credits
rather than ~56,400. The reasoning is the empirical one — catch-up after a
provider recovers is observed under two hours, so the fetch bounds recovery, not
the poll. Anything faster spends credits to arrive at the same time.

This puts `DAILY_CYCLE_RETRY_INTERVAL`, `DAILY_CYCLE_START_OFFSET`, and
`LATE_BAR_GRACE_PERIOD` all at 30 minutes — which is exactly the state in which
collapsing them into one constant starts to look like a tidy-up. It would not
be: they are a retry cadence, a start gate, and a session-close offset, on three
different clocks. `test_constants.py` asserts each independently *and* asserts
they are distinct objects, so aliasing one to another fails a test rather than
silently coupling two unrelated knobs.

### F003 — production code shaped around a test double

Accepted in full. `getattr(..., "nothing_actionable", False) is True` existed
only to survive under-specified mocks, at the cost of degrading a rename into a
silently absent drained signal. Production now reads
`daily_report.nothing_actionable` straight off the contract, and the mocks that
motivated the guard return real `CycleReport`s.

### F004 — five copies of "today's UTC midnight + offset"

Accepted. The docstring promised the gate and the work list could never
disagree, but promised it about a copy. `daemon/cadence.py` now holds
`utc_day_start` and `daily_pass_boundary`; `runner.py`'s four sites and
`daily.py` call it. A test asserts the gate and the boundary agree at the
instant itself, which is the property the copies could not guarantee.

### F005 — the enum governed reads but not writes

Accepted. The protection is only real if the token the query reads is the token
the cycle wrote. `daily.py` gains `_DAILY_GRANULARITY`, derived from the enum
once and used at every `update_data_gaps`, `advisory_lock`,
`iter_active_instruments`, and SQL site; the `_select_daily_mode` literal is now
a parameter; `CA_UPDATE_SENTINEL_GRANULARITY` derives from the enum;
`RunnerConfig.granularities` is typed `frozenset[CycleGranularity]` and defaults
to `frozenset(CycleGranularity)`; the CLI builds the set from enum members.

### F006 — a lint regression, and a hole in the gate that missed it

Confirmed, and it invalidates part of the Task 6.1 result as originally
recorded. The repo-wide ruff **total** was 1812 at both HEAD and the branch
point — but runner.py had *lost* an E501 and *gained* this I001, so the totals
matched by coincidence. Comparing totals cannot detect a swap. The gate is now
run per-file and per-rule; on that basis daily.py and cli/commands/data.py are
each one E501 better than the branch point, runner.py is one better, and the new
`cadence.py` is clean.

### F007 / F008 — accepted as described

The aggregate is bounded to the requested scope, and symbols absent from
`instruments` are reported in their own bucket with their own warning, so a
mistyped `--symbols` is no longer attributed to issue #4.

### F009 — no load-tier coverage for the new cadence

**Rejected** (revised 20260803 — initially deferred, which was wrong on both
counts).

The load-test rule exists for the minute pipeline: real concurrency, real
throughput, billions of rows. The daily cadence is one small work-list query and
one HTTP call per cycle. The finding pattern-matches the rule onto code it was
not written for, and each candidate assertion fails on its own terms:

- **Credit ceiling** — `QuotaBucket` already enforces the real limit. A test
  asserting a credits/day figure encodes today's arithmetic and breaks whenever
  the interval is tuned. A change-detector, not a bug-catcher.
- **Tick ceiling** — already covered by
  `test_no_busy_poll_when_nothing_is_actionable`.
- **Query latency at scale** — the only real question, but `trading_test` is
  documented as unrepresentative, so it would skip locally and be meaningful
  only against production. Running a load test against production to learn what
  production already shows is backwards. The 6.2 verification exercised the
  query at full scope (12,882 symbols) with no latency issue.

There is also a scope point that outranks all of the above: with
`--stop-when-done`, the daemon exits after one pass, so nothing runs between
invocations and the per-cycle costs this finding wants bounded are incurred once
per manual invocation. The cadence only becomes a rate at all under forever
mode, which production does not run today — see GitHub issue #9. If that
changes, revisit the query-latency assertion and nothing else.

Recorded as rejected rather than deferred so it does not resurface later as
untracked debt.

## Notes

- The plan entry designates 912 as the maintenance band's home for daemon-cycle
  correctness. Similar issues found in the same scope may be folded in rather
  than spawning near-duplicate slices; anything touching the minute path,
  provider behavior, or calendar assignment is not "the same scope."
- Prod query discipline applies to every verification query run against .144:
  `statement_timeout` always set, and never an expression aggregate over a
  compressed hypertable.
