---
docType: slice-design
slice: fixture-teardown-drop-database-race
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: []
interfaces: [905, 907, 917]
dateCreated: 20260822
dateUpdated: 20260822
status: not_started
review: none
---

# Slice Design: The test suite races itself on database teardown

## Overview

One integration test per run — a *different* test each time — dies with
`psycopg.errors.InternalError_: tuple concurrently updated` or
`tuple concurrently deleted`. Every affected test passes in isolation.

The cause is now known, with a reproduction that takes under four minutes. It
was previously attributed to sharing a PostgreSQL cluster with production. **That
attribution was wrong.** Slice 917 moved the test databases onto a dedicated
machine carrying no other workload, and the flake followed them.

The suite races *itself*: the fixture's `DROP DATABASE` at teardown collides with
the next test creating its own database and applying the migration chain.

## Evidence (measured 2026-08-20 and 2026-08-21, on the dedicated host)

Three experiments, one variable moving. All on hammerhead, no production
workload present, tests running serially with no parallel runner.

| Condition | Runs | Failures | Catalog races |
|---|---|---|---|
| Whole file, teardown drops enabled (current behavior) | 6 | **4** | present in 3 runs |
| Whole file, teardown `DROP DATABASE` suppressed | 6 | **0** | 0 |
| One test per pytest process (no overlap possible) | 10 | **0** | 0 |

The failing statement is identifiable and consistent:

```
src/manta_trading/market/schema/migrations/minute.py:2140
query = 'DROP MATERIALIZED VIEW IF EXISTS minute_coverage'
```

reached through `apply_migrations`, on a freshly created database.

### Why the window exists

That migration runs **without a transaction** — `requires_autocommit` is set, and
its own description explains why: it drops and recreates continuous aggregates,
which cannot happen inside a transaction, so every statement is `IF EXISTS` /
`IF NOT EXISTS` and re-running converges from any point.

That is a sound design for the migration. Its side effect is that no lock is held
across the drop-and-recreate sequence, leaving a window in which another session's
catalog work can collide. `DROP DATABASE` and this DDL both touch catalogs shared
across the whole cluster rather than private to one database.

### What is not the cause

- **Not production.** Reproduces on a dedicated host with no other workload.
- **Not the TimescaleDB job scheduler racing the migration inside one database.**
  If it were, the one-test-per-process case would race too. It did not, across ten
  attempts, with background workers enabled throughout.
- **Not parallel test execution.** No parallel runner is installed; tests are
  serial.

## The attempted fix, and why it is not the answer yet

Recorded so it is not repeated blindly. The patch is preserved at
[918-deferred-disposal-attempt.patch](../notes/attachments/918-deferred-disposal-attempt.patch).

**Approach:** stop dropping databases mid-run. Teardown registers the name; a
session-end finalizer drops them all after the last test; a session-start sweeper
removes any left by an interrupted run. Per-test isolation is unchanged — each
test still gets its own freshly created, empty database.

**It removes the race completely.** Zero races across six runs of the reproduction
case, and zero across a full 540-second integration tier.

**It breaks `test_policy_advances_head.py`.** Those tests wait on the real
TimescaleDB background scheduler rather than calling `run_job()`. With disposal
deferred they fail with `the policy did not advance the head unaided within
0:03:00`, and `last_successful_finish=None` — the job never ran at all.

Attribution is certain, and against the patch:

| Same two test files, one session | Result |
|---|---|
| Without the patch | policy tests **pass**, 19 passed |
| With the patch | policy tests **fail**, 2 failed |

The suspected mechanism is that retired-but-not-dropped databases keep their
continuous-aggregate policies, and TimescaleDB allocates a background worker per
such database. Measured peak during a run: **13 live databases against
`timescaledb.max_background_workers = 8`**.

A refinement that deletes each database's jobs at teardown — dropping its worker
demand to zero while the shell awaits disposal — **did not** rescue the policy
tests. That is where the work stopped.

## The first thing to check when this is picked up

`_release_background_workers` in the patch swallows every `psycopg.Error` so that
teardown cannot fail a test. **It may therefore never be executing at all**, in
which case the worker-starvation theory was never actually tested and is still
open. Instrument that helper before concluding anything from the failed
refinement — roughly ten minutes of work that determines whether the leading
theory is alive or already dead.

## Directions worth evaluating

Listed without preference; the measurement above should decide between them.

1. **Deferred disposal plus a genuine fix for worker demand** — if the jobs are
   not in fact being deleted, fixing that may be the whole answer.
2. **Raise the background-worker budget** on the test host. Cheap to try, but the
   number of live databases scales with the suite, so it may only move the ceiling.
3. **Remove the asynchrony rather than the drop.** The collision exists because
   something about `DROP DATABASE` continues after the statement returns.
   Identifying and waiting on that would let per-test drops stay.
4. **Reduce database churn** by moving more tests to the existing session-scoped
   fixture. Cheapest to write, but it trades away the per-test isolation those
   tests were deliberately given.

## Scope

- Identify why deferred disposal starves the policy tests, or establish that it
  does not and something else does.
- Land a fix that produces **zero catalog races across five consecutive full
  integration runs** while leaving the pass/fail set otherwise unchanged.
- Keep per-test isolation unless a deliberate, recorded decision trades it away.

## Success criteria

1. Five consecutive full integration runs complete with **zero**
   `tuple concurrently updated` / `tuple concurrently deleted` occurrences.
2. `test_policy_advances_head.py` passes 9/9 **within a full-tier run**, not only
   in isolation.
3. The tier's pass/fail set is otherwise unchanged from the recorded baseline: the
   two `test_cli_lists.py` failures for the missing `priority1` symbol list remain
   (they belong to slice 907), and no test newly fails or newly **skips**.
4. An interrupted run leaves no accumulating throwaway databases — whatever
   disposal strategy is chosen is self-healing on the next run.
5. Per-test isolation is preserved, or its loss is a recorded decision with
   reasoning.

## Verification walkthrough

Draft. The short reproduction is the working tool; the full tier is the proof.

```bash
# Reproduction — under four minutes, fails today, must pass after the fix
uv run --no-sync python scripts/run_tests.py integration -- \
  test/integration/test_migrations_046_047.py \
  test/integration/test_policy_advances_head.py

# Proof — five consecutive full runs, asserted rather than counted
for i in 1 2 3 4 5; do
  uv run --no-sync python scripts/run_tests.py integration 2>&1 | tee "/tmp/tier-$i.log"
done
if grep -l "tuple concurrently" /tmp/tier-*.log; then
  echo "FAIL: races still present in the files listed above" >&2; exit 1
fi
```

A run that dies on a connection failure or is interrupted is **discarded, not
counted** — as neither a pass nor a deviation. Only five complete runs satisfy
criterion 1.

## Out of scope

- The two `test_cli_lists.py` failures (missing `priority1` list) — slice 907.
- The missing `__init__.py` files that break whole-`test/` collection — slice 907.
- Anything about where the test cluster lives. Slice 917 settled that, and this
  defect is independent of it: it predates the move and survived it.

**Effort: 2/5. Risk: Low** — test-suite-only; no production code path is touched
unless direction 3 above turns out to require it.
