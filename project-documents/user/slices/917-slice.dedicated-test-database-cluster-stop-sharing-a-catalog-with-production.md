---
docType: slice-design
slice: dedicated-test-database-cluster-stop-sharing-a-catalog-with-production
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: []
interfaces: [905, 907, 913, 915]
dateCreated: 20260819
dateUpdated: 20260819
status: not_started
review: user/reviews/917-review.slice.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md
---

# Slice Design: Dedicated test database cluster

## Overview

Every throwaway database the test suite creates is created **inside the
production PostgreSQL cluster**. There is one cluster on this host, and it holds
the 4.4-billion-row production hypertable, production's roles, and every
ephemeral `mt_test_*` database the suite has ever made.

Nothing is at risk of *data* loss — test databases are UUID-named and dropped on
teardown — but three costs follow from sharing a single system catalog, and all
three are already recorded elsewhere in the project rather than hypothesised
here. This slice removes the sharing.

## Current state (verified 2026-08-19)

| | |
|---|---|
| Clusters on this host | exactly one — `17/main`, port 5432, `/var/lib/postgresql/17/main` |
| Listening | `0.0.0.0:5432` and `[::]:5432` — LAN-reachable |
| `MT_TIMESCALE_TEST_URL` | `postgresql://…@192.168.1.144:5432/postgres` — **the production cluster** |
| Test database lifecycle | `mt_test_<uuid12>`, `CREATE DATABASE` / `DROP DATABASE` per fixture ([conftest.py:88](../../../test/conftest.py), [:147](../../../test/conftest.py)) |
| Production preload | `shared_preload_libraries = 'timescaledb'`, `timescaledb.max_background_workers = 16` |
| Production sizing | `shared_buffers` 32179MB, `effective_cache_size` 96538MB, `work_mem` 512MB, `maintenance_work_mem` 2047MB, `max_worker_processes` 51, `max_connections` 100 |
| Host | 32 cores, 125 GB RAM (33 GB used), `/` 994 GB free, `/data` 489 GB free, load 0.14 |

### The three costs

1. **A rotating flake.** One test per full integration run errors with
   `psycopg.errors.InternalError_: tuple concurrently updated/deleted` during
   DDL — a *different* test each run (observed in `test_data_status_equivalence`,
   `test_migration_050`, `test_migration_051_052`), all passing in isolation. It
   is a catalog race between test-database DDL and the TimescaleDB background
   workers serving production.
2. **Roles are cluster-wide.** Slice 915 could not grant REPLICATION to the test
   admin, because the role would also exist for production and let a test
   credential stream production WAL — undoing slice 913. It shipped opt-in behind
   `-v with_replication=1`, which works around the colocation rather than removing
   it, and the same constraint returns on every future privilege decision.
3. **Blast radius.** The 2026-08-04 incident destroyed six production tables.
   CLAUDE.md's production-database protection rule, [conftest.py](../../../test/conftest.py)'s
   environment scrubbing, and [test/_prod_url_guard.py](../../../test/_prod_url_guard.py)
   are all guards against a hazard that a separate cluster removes structurally
   rather than by vigilance.

## Technical decisions

**D1 — A second cluster on this host, not a container and not another machine.**
Production admits replication from localhost only, so backup and restore work
must run here (slice 915); moving the tests elsewhere does not change that, and a
container introduces TimescaleDB version drift against production's 2.29.1. What
the tests actually need is catalog isolation, and a second cluster delivers it
completely.

**D2 — `pg_createcluster 17 test`, port 5433.** Same binaries as production, so
the test cluster is PostgreSQL 17.11 with TimescaleDB 2.29.1 by construction —
no version-drift surface, and migrations behave identically. 5433 is free.

**D3 — Data directory at the `pg_createcluster` default, `/var/lib/postgresql/17/test` on `/`.**
Explicitly **not** `/data`: that filesystem holds the WAL archive and the base
backups, and a runaway test cluster filling it would break archiving and PITR.
That failure is materially worse than I/O contention with a production cluster
that currently sits at load 0.14 with a manually-invoked daemon. Record the
choice with this reasoning so it is not "optimised" later.

**D4 — TimescaleDB preloaded, with background workers enabled.** Non-negotiable,
not a nicety: migrations create hypertables and continuous aggregates, and
`test_policy_advances_head.py` deliberately waits on the real background
scheduler instead of calling `run_job()`. A cluster without background workers
converts those 9 passing tests into hangs or failures.

**D5 — Sized against production's configured maxima, not its current usage.**
Production reserves `shared_buffers` at startup and can expand to
`max_connections` × `work_mem`; the test cluster's totals must fit in the
headroom left after those maxima, not after today's 33 GB. The budget:

| Setting | Production | Test cluster |
|---|---|---|
| `shared_buffers` | 32179MB | **2GB** |
| `work_mem` | 512MB | **32MB** |
| `maintenance_work_mem` | 2047MB | **256MB** |
| `max_connections` | 100 | **50** |
| `max_worker_processes` | 51 | **16** |
| `timescaledb.max_background_workers` | 16 | **8** |

Worst-case resident total for the test cluster is `2GB + (50 × 32MB) + 256MB` ≈
**4 GB**, which is the number to hold the implementation to. Two measurable
production-impact targets, both falsifiable during the verification runs:

1. **The coverage freshness probe stays within its 10-second budget** while the
   integration tier runs. Slice 169 measured it at 1.33s on this host, so the
   headroom is large and a regression is unambiguous.
2. **Swap usage stays at zero.** The host has 125 GB and production's reserved
   32 GB; if a 4 GB test cluster induces swapping, the sizing is wrong by an
   order of magnitude and the run should stop rather than be tuned around.

**D6 — Listen on localhost only.** Production listens on `0.0.0.0:5432` because
it serves the LAN. The test cluster has no such need, so `listen_addresses =
'localhost'`, and `MT_TIMESCALE_TEST_URL` moves from `192.168.1.144:5432` to
`127.0.0.1:5433`. Narrower by default, and it makes "am I pointed at production?"
answerable from the URL alone.

**D7 — Roles come from the existing `scripts/provision_roles.sql`,** run against
the new cluster with `-v with_test_admin=1`. No second provisioning mechanism.
Because the test cluster shares no roles with production, the constraint that
forced slice 915's `with_replication` opt-in does not apply there — this slice
**records** that and changes nothing, so 915's flag and 913's guarantees stay
exactly as they are.

**D8 — Absent or wrong configuration must fail, not skip.** Today
[conftest.py:85](../../../test/conftest.py) does
`pytest.skip("MT_TIMESCALE_TEST_URL not set")` — a green run that tested nothing,
the precise "passes because the database was absent" outcome this project
rejects. Two changes: make the missing-variable case an error, and add a guard
that refuses a test URL resolving to the production cluster, so a stale `.env`
cannot silently reintroduce the sharing this slice exists to remove.
`scripts/run_tests.py` already lists `MT_TIMESCALE_TEST_URL` as required for the
integration and load tiers, so the runner path is close; the bare-`pytest` path
is the hole.

**D9 — No data migrates.** Test databases are created and dropped per fixture;
there is nothing persistent to move. The whole "migration" is a URL change plus
role provisioning on the new cluster.

**D10 — The read-only production path is untouched.** Tests that intentionally
read production still go through `MT_TIMESCALE_DB_URL` gated on
`MT_ALLOW_PROD_READS=1`. This slice moves only the create-and-drop path.

## Migration plan

| | Source | Destination |
|---|---|---|
| Cluster | `17/main`, port 5432, LAN-listening | `17/test`, port 5433, localhost-only |
| Test admin role | provisioned in production's catalog | provisioned in the test cluster's catalog |
| Consumer | `MT_TIMESCALE_TEST_URL` in `.env` | same variable, repointed |
| Data | none — ephemeral, UUID-named | none |

**Consumers to update:** `.env` (the variable's value), and the two guard points
in `test/conftest.py` from D8. `scripts/run_tests.py` needs no change — it
already treats the variable as required for the DB-backed tiers.

**Behavior verification:** the integration tier passes on the new cluster with
the same result set as the recorded baseline (171 passed, 2 pre-existing
`test_cli_lists.py` failures for a `priority1` symbol list absent from
`config/symbol-lists.yaml`), and the rotating flake stops appearing. Production's
role set, port, listen addresses, and postmaster start time are unchanged across
the whole slice.

## Cross-slice interfaces

- **905 (lint/type debt)** — should run *after* this. Its verification is "the
  suite still passes"; with a flake firing once per integration run it cannot
  distinguish a real regression from catalog noise.
- **907 (CI)** — gains a third option for DB-dependent tests beyond "service
  container or skip", and no longer has to quarantine a flake whose affected test
  is not stable enough to name.
- **913 (least-privilege roles)** — its 30-test privilege suite runs against the
  test cluster, where cluster-wide role reasoning is finally scoped to test roles.
- **915 (backup/restore)** — unchanged. Its `with_replication` opt-in stays.

## Success criteria

1. `pg_lsclusters` reports two clusters: production `17/main` on 5432, and
   `17/test` on 5433, both online.
2. The test cluster reports PostgreSQL 17.11 and TimescaleDB 2.29.1, matching
   production.
3. `CREATE EXTENSION timescaledb` succeeds on a database in the test cluster, and
   `timescaledb.max_background_workers` is greater than zero.
4. The test roles exist on the test cluster and gain no new privilege on
   production. Production's `pg_roles` set is byte-identical **across this
   slice's execution window** — captured before the first task and compared after
   the last. This is a "did 917 change anything" check, not a standing invariant:
   slice 913 and its successors remain free to change production roles for their
   own reasons afterwards.
5. `MT_TIMESCALE_TEST_URL` resolves to `127.0.0.1:5433`, and the test cluster
   accepts no connection from another host on the LAN.
6. **Five consecutive full integration runs complete with zero**
   `tuple concurrently updated/deleted` **errors**, against today's rate of one
   per run.
7. The integration tier's pass/fail set matches the recorded baseline apart from
   the removed flake — no test newly fails, and none newly skips.
8. `test_policy_advances_head.py` still passes 9/9 on the test cluster, proving
   the background scheduler is genuinely running there.
9. Running the DB-backed tiers with `MT_TIMESCALE_TEST_URL` unset **errors**
   rather than skipping; setting it to the production cluster is **refused**.
10. Production is untouched: `pg_postmaster_start_time()` on 5432 is the same
    value at the end of the slice as at the start.

## Verification walkthrough

Draft — to be refined at implementation close.

```bash
# 1. Both clusters exist, production still on 5432
pg_lsclusters

# 2. Versions match production
psql -h 127.0.0.1 -p 5433 -d postgres -c "SELECT version();"
psql -h 127.0.0.1 -p 5433 -d postgres \
  -c "SELECT extversion FROM pg_extension WHERE extname='timescaledb';"

# 3. GATE — run before any test run, not after. A cluster that fails either
#    check produces hangs and confusing failures rather than an obvious cause.
psql -h 127.0.0.1 -p 5433 -d postgres -c "SHOW shared_preload_libraries;"
psql -h 127.0.0.1 -p 5433 -d postgres -c "SHOW timescaledb.max_background_workers;"

# 4. The test cluster is not reachable from the LAN
psql -h 192.168.1.144 -p 5433 -d postgres -c "SELECT 1"   # must fail to connect

# 5. Production's roles are unchanged (capture before the slice, diff after)
psql -h 127.0.0.1 -p 5432 -d postgres -At \
  -c "SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication
        FROM pg_roles ORDER BY rolname" > /tmp/roles-after.txt
diff /tmp/roles-before.txt /tmp/roles-after.txt   # must be empty

# 6. The flake is gone — five runs, zero catalog races.
#    Asserts and exits non-zero on any hit; does not merely print counts.
for i in 1 2 3 4 5; do
  uv run --no-sync python scripts/run_tests.py integration \
    2>&1 | tee "/tmp/integration-run-$i.log"
done
if grep -l "tuple concurrently updated" /tmp/integration-run-*.log; then
  echo "FAIL: catalog race still present in the files listed above" >&2
  exit 1
fi
echo "PASS: 5 runs, zero catalog races" 

# 7. Misconfiguration fails loudly instead of skipping
env -u MT_TIMESCALE_TEST_URL uv run --no-sync python -m pytest test/integration
#   -> error, not "skipped"

# 8. Production never restarted
psql -h 127.0.0.1 -p 5432 -d postgres -c "SELECT pg_postmaster_start_time();"
```

Steps 5 and 8 require values captured *before* any work begins; capturing them is
the first task, not an afterthought.

## Risks

- **Accidentally restarting production.** `pg_createcluster` and any package
  operation must touch `17/test` only. The `shared_preload_libraries` change
  requires a restart *of the new cluster*, which is harmless; the same edit
  applied to `17/main` would be a production outage. Mitigated by criterion 10 and
  by capturing the postmaster start time first.
- **Host memory.** Production reserves 32 GB of `shared_buffers` at startup and
  may expand well past it under load. Sizing the test cluster against the 92 GB
  currently free rather than against production's configured maxima is how this
  slice would end up causing the slowdown it is meant to prevent (D5).
- **Root access.** There is no passwordless sudo on this host, so cluster
  creation, config edits, and service control need Project-Manager-executed steps.
  The task breakdown must separate agent-runnable steps from PM-runnable ones
  rather than discovering the boundary mid-run.

## Failure modes

Each row is a way this slice fails in practice, how it is detected, and what
happens next. The recovery column is deliberately explicit about who acts, since
the root-access boundary (see Risks) splits the work.

| Failure | Detection | Recovery |
|---|---|---|
| `pg_createcluster` refuses — insufficient privilege | Command exits non-zero | PM-executed; the slice does not attempt a workaround |
| Port 5433 already bound by a leftover process | `pg_createcluster` selects a different port, or `ss -ltn` shows it taken | **Take the port the tool assigns rather than forcing 5433**, and propagate the real value into `MT_TIMESCALE_TEST_URL` and the walkthrough. The port is a fact to record, not a constant to defend |
| Cluster starts, then exits immediately | `pg_lsclusters` reports `down` after start; the cluster log names the cause | Fix the config that rejected startup and restart the test cluster. Production is never restarted as part of this recovery |
| `shared_preload_libraries` edit silently ineffective — TimescaleDB not loaded | `SHOW shared_preload_libraries` and `CREATE EXTENSION timescaledb` on the test cluster | **Gate the suite on this check** — it runs before any test run, not as a post-hoc verification step, so a cluster missing the extension never produces a confusing test failure |
| `timescaledb.max_background_workers` reports 0 | Walkthrough step 3, run as a gate | Correct the setting and restart the test cluster. Do **not** run the suite meanwhile: `test_policy_advances_head.py` waits on the real scheduler, so a zero-worker cluster produces a hang, which reads as a test bug rather than a configuration one |
| Cluster refuses connections part-way through a suite run | Tests error with a connection failure rather than an assertion failure | Treat the run as void, not as a baseline deviation. A partial run must never be counted toward criterion 6 |
| `.env` changed while a test run is in flight | A run reads the old URL and lands on the production cluster | Perform the URL swap with no run in flight. D8's guard is the backstop: a run that started before the swap and reaches the production cluster is refused rather than silently proceeding |

## Out of scope

- Moving development off this host — the localhost-only replication admission
  makes it the required home for backup and restore work (slice 915).
- CI runner design and where CI's databases come from — slice 907.
- Changing slice 915's `with_replication` opt-in, or any of slice 913's
  privilege guarantees.
- Fixing the two pre-existing `test_cli_lists.py` failures, or the missing
  `__init__.py` packaging that breaks whole-`test/` collection — both are 907's.

**Effort: 2/5. Risk: Med** — the work never touches the production cluster, but it
runs on the production host, and the failure mode of getting that wrong is an
outage rather than a failed test.
