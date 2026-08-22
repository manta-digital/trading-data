---
docType: slice-design
slice: dedicated-test-database-cluster-stop-sharing-a-catalog-with-production
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: []
interfaces: [187, 905, 907, 913, 915]
dateCreated: 20260819
dateUpdated: 20260819
status: complete
review: none
---

# Slice Design: Dedicated test database cluster on a separate host

## Overview

Every throwaway database the test suite creates is created **inside the
production PostgreSQL cluster**. One cluster on one host holds the
4.4-billion-row production hypertable, production's roles, and every ephemeral
`mt_test_*` database the suite has ever made.

This slice moves the test databases onto **hammerhead** (192.168.1.143), a
separate machine, and makes misconfiguration fail loudly instead of skipping.

**This design was revised on 2026-08-19 after measurement invalidated its first
version.** The original built a second cluster on the production host and sized
it against roughly 92 GiB of apparently free RAM. That number was wrong in kind:
the host runs strict memory overcommit, and the binding constraint was 1.47 GiB
of *commit headroom*, not free RAM. A baseline test run exhausted it and produced
a server-side out-of-memory failure. The overcommit policy has since been
corrected (see the 20260819 entry in `../notes/000-process-journal.md`), but the
episode established the real point: co-locating the suite with production leaves
it competing for memory and I/O even after the catalog is separated. A separate
host removes all of it at once.

## Current state (verified 2026-08-19)

### Production — manta9000, 192.168.1.144

| | |
|---|---|
| Clusters | exactly one — `17/main`, port 5432, `/var/lib/postgresql/17/main` |
| Listening | `0.0.0.0:5432` — LAN-reachable |
| `MT_TIMESCALE_TEST_URL` | `postgresql://…@192.168.1.144:5432/postgres` — **the production cluster** |
| Test database lifecycle | `mt_test_<uuid12>`, `CREATE DATABASE` / `DROP DATABASE` per fixture (`test/conftest.py`) |
| Versions | PostgreSQL `17.11-1.pgdg26.04+2`, TimescaleDB `2.29.1~ubuntu26.04-1710` |
| Database size | 142 GB total, 83 GB in hypertables |
| Role residue | `t913_app_29b4ce5b85` / `t913_mig_29b4ce5b85` — leftovers from an interrupted run, sitting in production's shared catalog |

### Candidate test host — hammerhead, 192.168.1.143

| | |
|---|---|
| OS | Ubuntu 24.04.4 LTS, kernel 6.17 |
| Capacity | 20 cores, 62 GiB RAM (60 GiB free), 1.7 TB free on `/` (NVMe) |
| Overcommit | `vm.overcommit_memory=0` (heuristic) — none of manta9000's trap |
| PostgreSQL | **not installed** — clean slate |
| Latency from .144 | 0.48 ms, wired, 0% loss |
| Availability | used interactively for agent experiments, so normally powered on |

### The three costs of sharing

1. **A rotating flake.** One test per full integration run errors with
   `psycopg.errors.InternalError_: tuple concurrently updated/deleted` during
   DDL — a *different* test each run (observed in `test_data_status_equivalence`,
   `test_migration_050`, `test_migration_051_052`), all passing in isolation. A
   catalog race between test-database DDL and the TimescaleDB background workers
   serving production.
2. **Roles are cluster-wide.** Slice 915 could not grant REPLICATION to the test
   admin without letting a test credential stream production WAL, undoing slice
   913. It shipped opt-in behind `-v with_replication=1` — a workaround for the
   colocation, not a fix. The two stale `t913_*` roles above are the same problem
   leaving visible residue.
3. **Blast radius, now demonstrated to include memory.** The 2026-08-04 incident
   destroyed six production tables. On 2026-08-19 a routine test run helped
   exhaust the host's memory commitment. Both are hazards a separate machine
   removes structurally rather than by vigilance.

## Technical decisions

**D1 — A separate machine, not a second cluster on the production host.**
A second local cluster separates the catalog and nothing else: the suite would
still contend for the same RAM, the same NVMe, and the same machine. Hammerhead
separates catalog, roles, memory, I/O, and blast radius simultaneously, and
removes a consumer from a host that is explicitly not dedicated to the database.
The objection that previously favoured co-location — automatic version parity
from shared binaries — is answered by D2. Latency, the other objection, measures
0.48 ms and is far below per-query overhead.

**D2 — Install PostgreSQL 17.11 and TimescaleDB 2.29.1 explicitly, and pin them.**
Both are published for Ubuntu 24.04 at production's exact upstream versions:

| | Production (26.04) | Hammerhead (24.04) |
|---|---|---|
| PostgreSQL | `17.11-1.pgdg26.04+2` | `17.11-1.pgdg24.04+2` |
| TimescaleDB | `2.29.1~ubuntu26.04-1710` | `2.29.1~ubuntu24.04-1710` |

Same upstream releases; the suffix is packaging metadata, not code. **Hammerhead
therefore does not need upgrading to 26.04** — doing so would add risk for no gain.

The install must **pin and hold** these versions. Both repositories already
publish newer builds (TimescaleDB 2.29.2 exists), so a bare
`apt install timescaledb-2-postgresql-17` lands one release ahead of production on
day one, and an unattended `apt upgrade` on a machine used interactively would
drift later. `apt-mark hold` on the three packages makes drift require a
deliberate act.

**D3 — Version parity is a standing obligation, not a one-time check.** This is
the real ongoing cost of D1 and the thing most likely to be forgotten. Two
mechanisms: the hold from D2, and an assertion the suite itself runs, so a drifted
test cluster announces itself rather than producing quietly different results.
When production upgrades, hammerhead is upgraded to match as part of that work.

**D4 — TimescaleDB preloaded, with background workers enabled.** Non-negotiable:
migrations create hypertables and continuous aggregates, and
`test_policy_advances_head.py` deliberately waits on the real background scheduler
instead of calling `run_job()`. A cluster without background workers converts
those 9 passing tests into hangs.

**D5 — Sizing.** With a dedicated 62 GiB host the pressure that produced the
original budget is gone, but the values still matter because hammerhead is used
interactively:

| Setting | Value | Reason |
|---|---|---|
| `shared_buffers` | 8GB | Test databases are small; matches production's new value |
| `work_mem` | 64MB | Ample for test-scale data |
| `maintenance_work_mem` | 512MB | Migrations build indexes and hypertables |
| `max_connections` | 100 | Parity with production, free here |
| `max_worker_processes` | 16 | |
| `timescaledb.max_background_workers` | 8 | Must exceed zero — see D4 |

Worst case roughly 15 GiB of 62 GiB, leaving the interactive workload
comfortable. **Deliberately not parity:** production's `work_mem` is 512MB, a 50×
override of what `timescaledb-tune` recommended and separately suspect. Copying a
value we believe is wrong is not parity, it is propagation. Plan-shape parity is
not a goal of this slice — the tests assert behavior, not plans.

**D6 — The test cluster listens on the LAN; the guard carries the safety.**
It must be reachable from .144, so `listen_addresses` includes the LAN interface
and `pg_hba.conf` admits the test admin role from `192.168.1.144` only. This
reverses the localhost-only narrowing the first version of this design chose, and
it is a deliberate trade: a box holding nothing but throwaway data is a low-value
target, and the protection that actually matters is D8's refusal to point tests at
production. Do not admit `0.0.0.0`; name the one host.

**D7 — Roles come from the existing `scripts/provision_roles.sql`,** run against
hammerhead with `-v with_test_admin=1`. No second provisioning mechanism. The
constraint that forced slice 915's `with_replication` opt-in does not apply on a
separate cluster — this slice **records** that and changes nothing, so 915's flag
and 913's guarantees stay exactly as they are.

**D8 — Absent or wrong configuration must fail, not skip.** Today
`test/conftest.py` calls `pytest.skip("MT_TIMESCALE_TEST_URL not set")` when the
variable is missing — a green run that tested nothing. Two changes: make the
missing-variable case an error, and add a guard refusing a test URL that resolves
to the production host, matching on host and port rather than an exact string so a
URL with different credentials is still caught. `scripts/run_tests.py` already
treats the variable as required for the DB-backed tiers; the bare-`pytest` path is
the hole. This guard matters *more* under D6, not less.

**D9 — No data migrates.** Test databases are created and dropped per fixture.
The migration is an install, role provisioning, and a URL change.

**D10 — The read-only production path is untouched.** Tests that intentionally
read production still go through `MT_TIMESCALE_DB_URL` gated on
`MT_ALLOW_PROD_READS=1`, from .144. This slice moves only the create-and-drop path.

**D11 — Cluster creation is a runbook procedure, not a committed script.**
Creation is a one-time operation requiring root; a committed script taking a host
and cluster name under `sudo` is an executable that can be pointed at the wrong
target, and this project has already lost six production tables once to a tool
that received the wrong target. A runbook the Project Manager executes has no such
failure mode. Role provisioning stays scripted, because `provision_roles.sql` is
idempotent, parameterized, and already the artifact production uses.

**D12 — The load tier's latency thresholds are labelled with the machine they
describe; they are not re-derived here.** Slice 187 added `test/load/` with
request-latency assertions measured on manta9000. `run_tests.py load` also
consumes `MT_TIMESCALE_TEST_URL`, so those assertions move to a 20-core / 62 GiB
machine as a side effect of this slice.

Re-baselining was considered and **declined** (Project Manager, 2026-08-19). The
thresholds stay as they are, and this slice's obligation is narrower: record in
`test/load/` that the numbers were established on manta9000 (32 cores, 125 GiB),
so that **a failure there is read as a possible hardware difference rather than
automatically as a regression**. Numbers that silently start describing different
hardware are the failure mode being avoided; a label is sufficient to avoid it,
and re-deriving them is work this slice does not need to do.

The tier is manually gated on `MT_RUN_LOAD_TESTS=1` today, so nothing runs it
unattended. If slice 907 wires it into CI, that slice inherits the question of
which machine CI's numbers describe.

## Known differences from production

Recorded rather than discovered later:

- **Ubuntu 24.04 versus 26.04** — different libc and OpenSSL builds. Well below
  the threshold that affects database semantics, but not zero.
- **Hardware** — 20 cores / 62 GiB versus 32 cores / 125 GiB. Matters only for
  the load tier (D12).
- **`work_mem`** — 64MB versus production's 512MB, deliberately (D5).
- **Network** — the suite reaches its database over the LAN rather than a local
  socket. 0.48 ms per round trip, against fixtures that create and drop databases
  frequently. Measure the tier's wall-clock time before and after; a large
  regression is a finding, not an acceptable cost.

## Migration plan

| | Source | Destination |
|---|---|---|
| Host | manta9000 (192.168.1.144), production | hammerhead (192.168.1.143), dedicated |
| Cluster | `17/main`, port 5432, shared with production | new cluster on hammerhead |
| Test admin role | production's catalog | hammerhead's catalog |
| Consumer | `MT_TIMESCALE_TEST_URL` in `.env` | same variable, repointed |
| Data | none — ephemeral, UUID-named | none |

**Consumers to update:** `.env` on .144, and the two guard points in
`test/conftest.py` from D8. `scripts/run_tests.py` needs no change.

**Production changes: none.** No cluster is created there, no configuration is
edited, and PostgreSQL is not restarted. That production is untouched becomes
nearly free to demonstrate — which is a reason to keep demonstrating it, not to
stop.

## Failure modes

| Failure | Detection | Recovery |
|---|---|---|
| apt cannot resolve the pinned versions | install exits non-zero naming the version | Stop. Do not relax the pin to "whatever resolves" — that silently forfeits parity |
| Cluster starts, then exits | `pg_lsclusters` reports `down`; the cluster log names the cause | Fix the config, restart the hammerhead cluster |
| `shared_preload_libraries` ineffective — TimescaleDB not loaded | `SHOW shared_preload_libraries` and `CREATE EXTENSION timescaledb` | **Gate the suite on this**, before any test run |
| `timescaledb.max_background_workers` reports 0 | Same gate | Correct and restart. Do not run the suite meanwhile: `test_policy_advances_head.py` waits on the real scheduler, so a zero-worker cluster hangs, which reads as a test bug |
| `pg_hba.conf` rejects .144 | Connection refused with an authentication error | Widen to the named host only. Never `0.0.0.0` |
| Hammerhead unreachable or powered off | Connection timeout at the first fixture | The suite fails loudly, which is correct. Note it as an environment failure, not a test failure |
| Connection lost part-way through a suite run | Tests error with connection failures rather than assertion failures | Treat the run as **void**. A partial run never counts toward the five-run criterion |
| Version drift after an `apt upgrade` | The parity assertion from D3 | Reinstall the pinned versions and re-hold |
| `.env` changed while a run is in flight | A run reads the old URL and lands on production | Swap with no run in flight. D8's guard is the backstop |

## Cross-slice interfaces

- **905 (lint/type debt)** — runs *after* this. Its verification is "the suite
  still passes"; with a flake firing once per run it cannot distinguish a real
  regression from catalog noise.
- **907 (CI)** — gains a real test database beyond "service container or skip",
  and no longer has to quarantine a flake whose affected test is not stable enough
  to name.
- **913 (least-privilege roles)** — its 30-test privilege suite runs on hammerhead,
  where throwaway role names stop touching production's catalog.
- **915 (backup/restore)** — unchanged. Its `with_replication` opt-in stays.
- **187 (load tier)** — its latency thresholds are what D12 addresses.

## Success criteria

1. Hammerhead runs PostgreSQL **17.11** and TimescaleDB **2.29.1**, matching
   production's upstream versions, with all three packages held against upgrade.
2. `CREATE EXTENSION timescaledb` succeeds on a database there, and
   `timescaledb.max_background_workers` is greater than zero.
3. The test roles exist on hammerhead. Production's `pg_roles` set is unchanged
   **across this slice's execution window** — captured after the baseline run and
   compared at the end, excluding the transient `t913_*` names the privilege suite
   creates and drops per run.
4. `MT_TIMESCALE_TEST_URL` resolves to hammerhead, and hammerhead's cluster admits
   `192.168.1.144` but not an arbitrary LAN host.
5. ~~Five consecutive full integration runs with zero catalog races.~~
   **Moved to slice 918 (2026-08-22).** Implementation proved this slice can
   neither cause nor cure that flake: the races reproduce on the dedicated host
   with no production workload present, because the test suite races its own
   `DROP DATABASE` teardown. Isolation was never the remedy. What this slice does
   deliver is criterion 11 — production is provably untouched, and no test
   database or role is created in its catalog.
6. The integration tier's pass/fail set matches the recorded baseline apart from
   the removed flake — no test newly fails, and none newly **skips**.
7. `test_policy_advances_head.py` passes 9/9 on hammerhead, proving the background
   scheduler genuinely runs there.
8. Running the DB-backed tiers with `MT_TIMESCALE_TEST_URL` unset **errors**
   rather than skipping; pointing it at production is **refused**.
9. The integration tier's wall-clock time is recorded before and after the move,
   so the network cost is a measured number rather than an assumption.
10. `test/load/` records that its latency thresholds were established on
    manta9000 (32 cores, 125 GiB), so a failure on other hardware is not read as
    an automatic regression (D12).
11. Production is untouched: no cluster created, no configuration edited, and
    `pg_postmaster_start_time()` on 5432 unchanged from the baseline capture.

## Verification walkthrough

**Executed 2026-08-20 to 2026-08-22.** Measured values below; the commands are
what was actually run.

### What was measured

| | Result |
|---|---|
| Test host | hammerhead, 192.168.1.143, cluster `17/main`, **port 5432** |
| PostgreSQL | `17.11-1.pgdg24.04+2` — matches production's 17.11 |
| TimescaleDB | `2.29.1~ubuntu24.04-1710` — matches production's 2.29.1 |
| Packages held | all three, verified with `apt-mark showhold` |
| Reachability | succeeds from 192.168.1.144; refused elsewhere by `pg_hba.conf` |
| Firewall | `ufw` open on 5432 to 192.168.1.144 only |
| Background workers | 8, greater than zero |
| Integration tier, before | 556 s on production's cluster |
| Integration tier, after | 482–540 s on hammerhead — **no network penalty**, within noise and if anything faster |
| Production postmaster | `2026-08-19 11:25:01.540727-06`, identical to the group A capture |
| Production roles | byte-identical to the group A capture |
| Databases created on production | none — the 3 present predate this slice |
| Clusters on the production host | 1, unchanged; none created |

**The one thing that did not improve: the catalog-race flake got worse.** A full
tier on production's cluster produced 3 races; on hammerhead it produced 10. This
slice neither caused nor could cure it — see slice 918, which owns it — but the
rate is higher on the dedicated host, plausibly because a less-loaded machine runs
tests closer together and widens the collision window. Recorded so 918 starts from
the real number.

One `test_policy_advances_head.py` test failed in that run. It passes 9/9 in
isolation and passed in a smaller multi-file run, so the flake is the likely
cause — but that attribution is **not** established, and 918 should confirm it
rather than assume it.

### Commands

```bash
# 1. Versions and holds on hammerhead
ssh hammerhead 'pg_lsclusters; \
  psql -At -c "SELECT version();"; \
  psql -At -c "SELECT extversion FROM pg_extension WHERE extname=%s;" ; \
  apt-mark showhold'

# 2. GATE — before any test run. Failing either produces hangs, not clear errors.
ssh hammerhead 'psql -At -c "SHOW shared_preload_libraries;"'
ssh hammerhead 'psql -At -c "SHOW timescaledb.max_background_workers;"'

# 3. Reachable from .144, and only from there
psql "$MT_TIMESCALE_TEST_URL" -At -c "SELECT 1"          # succeeds from .144
#   from any other LAN host: must be refused by pg_hba

# 4. Production untouched (baseline captured in group A)
psql "$PROD_URL" -At -c "SELECT pg_postmaster_start_time();"
psql "$PROD_URL" -At -c "SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,\
  rolreplication FROM pg_roles WHERE rolname NOT LIKE 't913\_%' ORDER BY rolname" \
  > /tmp/roles-after.txt
diff /tmp/roles-baseline.txt /tmp/roles-after.txt        # must be empty

# 5. The flake is gone — five runs, asserted, not merely counted
for i in 1 2 3 4 5; do
  uv run --no-sync python scripts/run_tests.py integration 2>&1 \
    | tee "/tmp/integration-run-$i.log"
done
if grep -l "tuple concurrently updated" /tmp/integration-run-*.log; then
  echo "FAIL: catalog race still present in the files listed above" >&2
  exit 1
fi
echo "PASS: 5 runs, zero catalog races"

# 6. Misconfiguration fails loudly rather than skipping
env -u MT_TIMESCALE_TEST_URL uv run --no-sync python -m pytest test/integration
#   -> error, not "skipped"
```

## Risks

- **Version drift** is the standing risk this design accepts in exchange for
  isolation. Mitigated by the hold (D2) and the assertion (D3), neither of which
  is self-enforcing if someone removes them.
- **Root access on hammerhead.** No passwordless sudo there either, so install,
  configuration, and service control are Project-Manager-executed. The task
  breakdown must separate agent-runnable from PM-runnable steps rather than
  discovering the boundary mid-run.
- **Hammerhead availability** becomes a dependency of running integration tests.
  Acceptable — it is normally on — but it is new.

Production restart risk, which dominated the previous version of this design, is
**gone**: nothing on .144's PostgreSQL is touched.

## Out of scope

- Moving development off manta9000. Production admits replication from localhost
  only, so backup and restore drills must run there (slice 915).
- CI runner design and where CI's databases come from — slice 907.
- Changing slice 915's `with_replication` opt-in or slice 913's guarantees.
- Dropping the two stale `t913_*` roles from production. They are production
  catalog objects and removing them is a Project Manager decision.
- Fixing the two pre-existing `test_cli_lists.py` failures, or the missing
  `__init__.py` packaging that breaks whole-`test/` collection — both are 907's.
- **A single-machine path for other users of this repository.** The design
  assumes two database-capable machines, which most people cloning this project
  will not have. Worth adding later, and cheap when the time comes: the *first*
  version of this design — a second PostgreSQL cluster on the same host — is
  precisely what a one-machine user would do, and it is preserved in this file's
  git history rather than lost. Everything downstream of the cluster (role
  provisioning, the `MT_TIMESCALE_TEST_URL` indirection, the guards from D8) is
  identical either way, so a single-machine setup differs only in where the
  cluster is created. Deferred deliberately; raised so it is not rediscovered as a
  surprise when someone opens an issue.
- **Simplifying how tests name roles.** `provision_roles.sql` documents that a
  test run "MUST pass throwaway role names — otherwise it mutates the very roles
  production depends on," because `pg_authid` is cluster-wide. On a separate
  cluster that hazard is gone and the suite could exercise the real role names — a
  fidelity gain. Left alone here; raised as follow-up rather than lost.

**Effort: 3/5. Risk: Low** — more steps than the co-located design, but each is
simpler, and none of them touches the production database.
