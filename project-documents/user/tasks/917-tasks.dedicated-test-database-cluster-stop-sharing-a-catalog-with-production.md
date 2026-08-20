---
docType: tasks
slice: dedicated-test-database-cluster-stop-sharing-a-catalog-with-production
project: trading-data
lldReference: user/slices/917-slice.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: []
interfaces: [187, 905, 907, 913, 915]
projectState: >
  Test databases are created inside the production PostgreSQL cluster on
  manta9000, because MT_TIMESCALE_TEST_URL resolves to 192.168.1.144:5432. One
  integration test per run dies on a catalog race. Hammerhead (192.168.1.143,
  Ubuntu 24.04, 20 cores, 62 GiB, 1.7 TB free) has no PostgreSQL installed and is
  reachable by SSH key as `manta`.
status: in_progress
dateCreated: 20260819
dateUpdated: 20260819
---

# Tasks: Dedicated test database cluster on hammerhead

## Context summary

The test suite creates its throwaway `mt_test_<uuid>` databases inside the
**production** cluster. This slice stands up a dedicated cluster on **hammerhead**
(192.168.1.143), points the suite at it, and makes misconfiguration fail loudly
instead of skipping.

Success is measured, not asserted: five consecutive integration runs with zero
`tuple concurrently updated/deleted` errors, against today's rate of roughly one
per run, with production provably untouched.

Read the design first. The decisions that shape these tasks are the **pinned and
held** package versions, the LAN exposure limited to one named host, the
runbook-not-script choice for cluster creation, and the rule that the TimescaleDB
checks are a **gate before any test run** rather than a verification afterwards.

## Execution notes

- **Root boundary.** Neither machine has passwordless sudo. Every task marked
  **[PM]** must be executed by the Project Manager. **[agent]** tasks need no
  elevation. Do not attempt to work around a **[PM]** task.
- **Nothing on manta9000's PostgreSQL is touched.** No cluster is created there,
  no configuration edited, no restart. A command that would do any of those is a
  stop-and-ask, not a judgement call. The only change on .144 is `.env` and
  `test/conftest.py`.
- **The port is a fact, not a constant.** Record what the cluster is actually
  assigned and carry that value forward.
- **Never `source .env`** — a `$` in the password mangles it. Grep the value out.
- **zsh does not word-split unquoted `$VAR`.** Use `xargs -a` or `$(cat file)`.
- SSH reaches the test host as `ssh hammerhead` (configured in `~/.ssh/config`).
- **Every `[PM]` step in groups B, C, and D is written up as a runbook**:
  [test-database-cluster.md](../runbooks/test-database-cluster.md). It is committed,
  and the repository is already cloned on hammerhead at
  `~/source/repos/manta/trading-data`, so the Project Manager follows it there
  directly rather than having steps relayed. Configuration comes from
  `deploy/test-cluster/` in the checkout, so nothing multi-line is ever pasted.
  Keep the runbook and these tasks in step: a correction found while running one
  belongs in both.
- Integration tier runs take roughly 9.5 minutes each; groups A and F each spend
  real time on the runner.

---

## Group A — Capture baselines before anything changes

Effort: 1/5. Nothing here modifies either host.

- [x] **A.1 [agent] Record the integration tier's current wall-clock time and result set**
  - [x] Run the tier once against the current (shared) cluster, timed, tee'd to a log
  - [x] Record: elapsed time, pass/fail totals, and the count of
        `tuple concurrently updated` occurrences with the test each hit
  - [x] The elapsed time is the **before** half of the network-cost comparison in F.5
  - [x] Success: a log and a recorded summary. Expected shape is 171 passed with 2
        `test_cli_lists.py` failures, plus zero or one catalog race. **A zero is
        not a problem** — the flake is intermittent; note it and continue

- [x] **A.2 [agent] Capture production's role set, after A.1**
  - [x] Order matters: A.1 creates and drops its own `t913_*` roles, so a snapshot
        taken before it would not match at F.6 for reasons unrelated to this slice
  - [x] `SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication FROM
        pg_roles WHERE rolname NOT LIKE 't913\_%' ORDER BY rolname` via `psql -At`
  - [x] Filtering `t913_*` as transient follows the existing convention in
        `test/integration/data/test_test_admin_role.py`
  - [x] Success: a diffable file including `trading_app`, `trading_migrate`,
        `trading_test_admin`, and `postgres`

- [x] **A.3 [agent] Capture production's postmaster start time**
  - [x] `SELECT pg_postmaster_start_time();` against 5432
  - [x] Success: a timestamp recorded. Proves at F.6 that production never restarted

- [x] **A.4 [agent] Commit the captured baselines**
  - [x] Commit the baseline files and a short note of what A.1–A.3 found
  - [x] Success: the baselines are recoverable by anyone reading the branch

---

## Group B — Install PostgreSQL and TimescaleDB on hammerhead

Effort: 2/5. Every install and configuration task is **[PM]**.

- [x] **B.1 [PM] Add the PGDG and TimescaleDB apt repositories**
  - [x] PGDG for `noble`, and the TimescaleDB packagecloud repository for `noble`
  - [x] Success: `apt-cache policy postgresql-17` lists `17.11-1.pgdg24.04+2`, and
        `apt-cache policy timescaledb-2-postgresql-17` lists
        `2.29.1~ubuntu24.04-1710`

- [x] **B.2 [PM] Install the pinned versions**
  - [x] Install `postgresql-17=17.11-1.pgdg24.04+2`,
        `timescaledb-2-postgresql-17=2.29.1~ubuntu24.04-1710`, and
        `timescaledb-2-loader-postgresql-17=2.29.1~ubuntu24.04-1710`
  - [x] **Do not** install unversioned. Newer builds exist — TimescaleDB 2.29.2 is
        published — and taking them forfeits the parity this slice depends on
  - [x] Success: all three install at exactly the named versions
  - [x] If apt cannot resolve a pinned version, **stop**. Do not relax the pin

- [x] **B.3 [PM] Hold the three packages against upgrade**
  - [x] `apt-mark hold` each of the three
  - [x] Success: `apt-mark showhold` lists all three, so an unattended
        `apt upgrade` on an interactively-used machine cannot drift them

- [x] **B.4 [agent] Verify version parity with production**
  - [x] `SELECT version();` and the `timescaledb` row from `pg_extension` on
        hammerhead, compared against production's `17.11` / `2.29.1`
  - [x] Success: upstream versions match. The distro build suffix differs
        (`pgdg24.04` vs `pgdg26.04`) and that is expected — it is packaging
        metadata, not code
  - [x] **Verified 2026-08-20:** hammerhead `postgresql-17 17.11-1.pgdg24.04+2`,
        `timescaledb-2-postgresql-17 2.29.1~ubuntu24.04-1710`, loader likewise —
        matching production's 17.11 / 2.29.1
  - [x] **Known harmless difference:** `timescaledb-toolkit` is 1.25.0 here and
        1.24.0 on production. It is **not pinned and does not need to be** — the
        extension is never created (production's `pg_extension` holds only
        `plpgsql` and `timescaledb`) and no code references toolkit functions. If
        the project ever adopts the toolkit, it joins the pinned set

---

## Group C — Create and configure the cluster

Effort: 2/5.

- [x] **C.1 [PM] Create the cluster on hammerhead**
  - [x] Create a cluster for PostgreSQL 17 on the default data directory. The 1.7 TB
        NVMe root filesystem has ample room; no separate location is needed
  - [x] Success: the cluster is listed by `pg_lsclusters` on hammerhead

- [x] **C.2 [agent] Record the assigned port**
  - [x] Read it from `pg_lsclusters`, not from an assumption
  - [x] Success: the port is recorded and every later task uses that value
  - [x] **Observed 2026-08-20:** port **5432**, data directory
        `/var/lib/postgresql/17/main`, cluster `17/main`, status online

- [x] **C.3 [PM] Configure the cluster**
  - [x] `shared_preload_libraries = 'timescaledb'`
  - [x] `listen_addresses` includes the LAN interface — the suite runs on .144
  - [x] Sizing from the design: `shared_buffers` 8GB, `work_mem` 64MB,
        `maintenance_work_mem` 512MB, `max_connections` 100,
        `max_worker_processes` 16, `timescaledb.max_background_workers` 8
  - [x] Success: all settings present in hammerhead's `postgresql.conf`

- [x] **C.4 [PM] Admit only manta9000 in `pg_hba.conf`**
  - [x] Admit the test admin role from `192.168.1.144` specifically
  - [x] **Never `0.0.0.0`.** Name the one host
  - [x] Success: the entry names a single source address

- [x] **C.5 [PM] Restart the cluster**
  - [x] **`restart`, not `start`.** Installing `postgresql-17` creates and starts
        `17/main` automatically, so `start` fails with "already running". More
        importantly C.3 changed `shared_preload_libraries`, which only takes
        effect on a genuine restart
  - [x] Success: `pg_lsclusters` reports it online
  - [ ] If it starts then exits, read the cluster's own log, fix the config, and
        restart the hammerhead cluster

- [ ] **C.6 [agent] GATE — verify TimescaleDB is actually loaded**
  - [ ] Runs **before any test run**, not after. A cluster missing the extension or
        its background workers produces hangs that read as test bugs rather than
        configuration problems
  - [ ] `SHOW shared_preload_libraries;` contains `timescaledb`
  - [ ] `SHOW timescaledb.max_background_workers;` is greater than zero
  - [ ] `CREATE EXTENSION timescaledb;` succeeds in a scratch database, then drop it
  - [ ] Success: all three pass. **On failure, stop here** — correct, restart the
        cluster, re-run this gate

- [ ] **C.7 [agent] Verify reachability is exactly as intended**
  - [ ] Connecting from .144 succeeds
  - [ ] Connecting from a different LAN address is refused by `pg_hba.conf`
  - [ ] Success: both hold. A successful connection from elsewhere means C.4 was
        too broad — return to it

---

## Group D — Provision roles

Effort: 1/5.

- [ ] **D.1 [PM] Run the existing role provisioning script against hammerhead**
  - [ ] Pipe the script in; do **not** pass `-f <path>`. `sudo -u postgres` drops
        to the `postgres` user, which cannot traverse a mode-750 home directory,
        so a path argument fails with `Permission denied`. Redirecting means your
        own shell opens the file and `postgres` receives only stdin. `-f -`
        preserves `\if` and `\gexec` handling exactly
  - [ ] Do not relax home-directory permissions and do not stage the script in
        `/tmp` — configuration comes from the version-controlled checkout
  - [ ] Must be applied **as a superuser** — the script's own header explains why
        creating roles and granting `postgres` requires rights the maintenance role
        does not hold
  - [ ] Use the same artifact production uses; do not write a variant
  - [ ] `with_replication` is **not** passed. The constraint that made it opt-in no
        longer applies on a separate cluster, but changing that is slice 915's business
  - [ ] Success: the script exits zero

- [ ] **D.2 [PM] Set the test admin password out-of-band**
  - [ ] Executed directly, never committed to any file in the repository
  - [ ] Success: the role authenticates from .144

- [ ] **D.3 [agent] Verify the roles on hammerhead**
  - [ ] Success: the test admin role exists with `CREATEDB` and `CREATEROLE`, which
        is what the fixture needs to create and drop databases

- [ ] **D.4 [agent] Verify production's roles are unchanged**
  - [ ] Re-run the A.2 query against production and diff against the A.2 snapshot
  - [ ] Success: the diff is **empty**. A non-empty diff means provisioning ran
        against the wrong host — stop and investigate

- [ ] **D.5 [agent] Commit the recorded host evidence**
  - [ ] Groups B–D change *host* state, not repository state, so without this the
        slice runs several groups with nothing reviewable committed
  - [ ] Commit the port from C.2, the gate output from C.6, the version parity from
        B.4, the reachability result from C.7, and the role checks from D.3–D.4
  - [ ] Success: someone can tell what the cluster looks like without logging in

---

## Group E — Point the suite at hammerhead and make misconfiguration fail

Effort: 2/5. The only code change in the slice.

- [ ] **E.1 [agent] Update `MT_TIMESCALE_TEST_URL` in `.env` on manta9000**
  - [ ] Repoint from `192.168.1.144:5432` to hammerhead and the recorded port
  - [ ] Perform the edit with **no test run in flight** — a run reading the old
        value mid-swap lands on production
  - [ ] Success: the variable resolves to hammerhead. `.env` stays gitignored

- [ ] **E.2 [agent] Smoke-test the fixture path**
  - [ ] Run one small DB-backed integration test
  - [ ] Success: it passes, and **no new `mt_test_*` database appears on
        production** — confirm by listing databases on .144

- [ ] **E.3 [agent] Replace the silent skip with an error**
  - [ ] `test/conftest.py` currently calls
        `pytest.skip("MT_TIMESCALE_TEST_URL not set")` — a green run that tested
        nothing. Make the absent-variable case fail with a message naming the
        variable and what to set it to
  - [ ] Both database fixtures need this, not just the first
  - [ ] Success: the fixtures error rather than skip when the variable is unset

- [ ] **E.4 [agent] Add a guard refusing a production-pointing test URL**
  - [ ] Refuse when `MT_TIMESCALE_TEST_URL` resolves to the production host. Match
        on host and port, not an exact string, so a URL with different credentials
        or a trailing parameter is still caught
  - [ ] The message must say plainly that the test URL points at production
  - [ ] Success: setting the variable to the production URL fails immediately,
        before any database is created

- [ ] **E.5 [agent] Add the version-parity assertion**
  - [ ] The suite asserts the test cluster reports PostgreSQL 17.11 and TimescaleDB
        2.29.1, so a drifted cluster announces itself rather than producing quietly
        different results
  - [ ] Compare upstream versions, not distro build suffixes — those legitimately
        differ between 24.04 and 26.04
  - [ ] Success: the assertion passes now, and fails if given a different version

- [ ] **E.6 [agent] Test the three guards**
  - [ ] A test proving the unset case errors and does not skip
  - [ ] A test proving a production-pointing URL is refused, covering at least one
        variation so the guard is not matching a literal string
  - [ ] A test proving the parity assertion fails on a mismatched version
  - [ ] Success: all pass, and each fails if its guard is reverted — verify by
        temporarily reverting, not by assuming

- [ ] **E.7 [agent] Commit the guard work**
  - [ ] Commit `test/conftest.py` and the new tests together
  - [ ] Success: the message says what the guards prevent, not that guards were added

---

## Group F — Prove the slice delivered

Effort: 2/5. Record actual output rather than summarising it.

- [ ] **F.1 [agent] Five consecutive integration runs, asserting zero catalog races**
  - [ ] Run the tier five times, teeing each to its own log
  - [ ] Assert across all five — the check must **exit non-zero on any hit**, not
        merely print counts
  - [ ] **Void-run rule:** a run that dies on a connection failure or is interrupted
        is **discarded, not counted**, as neither pass nor baseline deviation. Only
        five *complete* runs satisfy the criterion. Note each discarded run and why,
        so the count cannot be quietly padded
  - [ ] Success: zero occurrences across all five

- [ ] **F.2 [agent] Compare the pass/fail set to the A.1 baseline**
  - [ ] Success: the same tests pass and the same two `test_cli_lists.py` tests
        fail. **No test newly fails, and none newly skips** — a new skip is the
        failure mode this slice guards against

- [ ] **F.3 [agent] Confirm the background scheduler genuinely runs**
  - [ ] Run `test_policy_advances_head.py` against hammerhead
  - [ ] Success: 9/9. This test waits on the real TimescaleDB scheduler rather than
        calling `run_job()`, so passing proves the workers are live, not merely
        configured

- [ ] **F.4 [agent] Label the load tier's thresholds**
  - [ ] Record in `test/load/` that the latency thresholds were established on
        manta9000 (32 cores, 125 GiB), so a failure on other hardware is read as a
        possible hardware difference rather than an automatic regression
  - [ ] Do **not** re-derive the thresholds — declined by the Project Manager
  - [ ] Success: the provenance is stated where someone reading a failure will see it

- [ ] **F.5 [agent] Measure the network cost**
  - [ ] Compare the tier's wall-clock time against the A.1 baseline
  - [ ] Success: the delta is a recorded number. A large regression is a finding to
        report, not a cost to absorb silently

- [ ] **F.6 [agent] Confirm production was never touched**
  - [ ] `pg_postmaster_start_time()` on 5432 identical to A.3
  - [ ] Production role diff against A.2 empty
  - [ ] No cluster created and no configuration edited on manta9000
  - [ ] Success: all three hold

---

## Group G — Document and close

Effort: 1/5.

- [ ] **G.1 [agent] Write the test-cluster runbook**
  - [ ] Cover: that the test database lives on hammerhead and why, the repository
        setup, the **pinned versions and the holds**, cluster creation, the
        `pg_hba.conf` restriction to .144, role provisioning, and the gate checks
  - [ ] State what to do when production upgrades PostgreSQL or TimescaleDB —
        hammerhead is upgraded to match as part of that work, and the holds must be
        lifted and re-applied deliberately
  - [ ] Success: someone who never saw this slice can rebuild the cluster from it

- [ ] **G.2 [agent] Fold measured values into the slice design**
  - [ ] Replace the draft verification walkthrough with the actual port, the
        measured wall-clock delta, and the F.1 result
  - [ ] Success: the walkthrough records what was run, not what might be

- [ ] **G.3 [agent] Record the two follow-ups this slice deliberately did not do**
  - [ ] Tests pass throwaway role names to `provision_roles.sql` because
        `pg_authid` is cluster-wide; on a separate cluster the real role names could
        be used, a fidelity gain
  - [ ] A single-machine path for other users of this repository — most people
        cloning it will not have two database-capable machines, and the same-host
        second-cluster approach is the answer when someone asks
  - [ ] Write both into the **Future Work** section of
        `user/architecture/900-slices.foundation-cleanup.md`
  - [ ] Success: each entry says enough that a reader who never saw this slice can act

- [ ] **G.4 [agent] Final commit**
  - [ ] Commit the runbook, the design update, and the recorded evidence
  - [ ] Success: the working tree is clean and `cf check` reports no new warnings

---

## Definition of done

All eleven of the slice's success criteria are met, with the three that matter
most demonstrated rather than argued:

- Five consecutive integration runs, zero catalog races, asserted by a command
  that fails on a hit.
- Production untouched — postmaster start time and role set identical to the
  group A captures, no cluster created, no configuration edited.
- Version parity held by `apt-mark hold` and asserted by the suite, so drift is
  visible rather than silent.
