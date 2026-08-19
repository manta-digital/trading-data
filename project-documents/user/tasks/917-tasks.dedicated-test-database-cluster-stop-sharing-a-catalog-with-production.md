---
docType: tasks
slice: dedicated-test-database-cluster-stop-sharing-a-catalog-with-production
project: trading-data
lldReference: user/slices/917-slice.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: []
interfaces: [905, 907, 913, 915]
projectState: >
  One PostgreSQL cluster on this host (17/main, port 5432) holds both production
  and every ephemeral test database. MT_TIMESCALE_TEST_URL points at it. One
  integration test per run dies on a catalog race. No test cluster exists yet.
status: not_started
dateCreated: 20260819
dateUpdated: 20260819
---

# Tasks: Dedicated test database cluster

## Context summary

The test suite creates its throwaway `mt_test_<uuid>` databases inside the
**production** PostgreSQL cluster, because `MT_TIMESCALE_TEST_URL` resolves to
`192.168.1.144:5432` — production's own maintenance database. This slice builds
a second cluster on the same host, moves the test databases onto it, and makes
misconfiguration fail loudly instead of skipping.

Success is measured, not asserted: five consecutive integration runs with zero
`tuple concurrently updated/deleted` errors, against today's rate of roughly one
per run, with production provably untouched throughout.

Read the design before starting. The decisions that shape these tasks are the
sizing budget, localhost-only listening, the runbook-not-script choice for
cluster creation, and the rule that the TimescaleDB checks are a **gate before
any test run** rather than a verification step afterwards.

## Execution notes

- **Root boundary.** There is no passwordless sudo on this host. Every task
  marked **[PM]** must be executed by the Project Manager. Tasks marked
  **[agent]** need no elevation. Do not attempt to work around a **[PM]** task.
- **Never touch `17/main`.** Every `pg_ctlcluster`, config edit, and
  `pg_createcluster` invocation names the test cluster explicitly. A command
  that would restart production is a stop-and-ask, not a judgement call.
- **The port is a fact, not a constant.** `pg_createcluster` assigns the port.
  Record what it assigns and use that value everywhere downstream. 5433 is the
  expected value, not a requirement to enforce.
- **Never `source .env`** — a `$` in the password mangles it. Grep the value out.
- **zsh does not word-split unquoted `$VAR`.** Use `xargs -a` or `$(cat file)`
  when passing file lists to a command.
- Integration tier runs take roughly 9.5 minutes each; group A and group F each
  spend real time on the runner.

---

## Group A — Capture baselines before anything changes

Effort: 1/5. Nothing in this group modifies the host. It must complete first:
two of the slice's success criteria are before/after comparisons, and the
"before" half cannot be recovered once the work starts.

- [ ] **A.1 [agent] Capture production's role set**
  - [ ] Run against production (port 5432) and save to a tracked scratch path:
        `SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication FROM pg_roles ORDER BY rolname`
  - [ ] Use `psql -At` so the output is stable and diffable
  - [ ] Success: a file exists containing one line per role, and it includes
        `trading_app`, `trading_migrate`, and `postgres`

- [ ] **A.2 [agent] Capture production's postmaster start time**
  - [ ] `SELECT pg_postmaster_start_time();` against port 5432, saved alongside A.1
  - [ ] Success: a single timestamp is recorded. This value proves at the end of
        the slice that production was never restarted

- [ ] **A.3 [agent] Record the current flake rate contemporaneously**
  - [ ] Run the integration tier once against the current (shared) cluster:
        `uv run --no-sync python scripts/run_tests.py integration`, tee to a log
  - [ ] Grep the log for `tuple concurrently updated` and record the count and
        which test it hit
  - [ ] Also record the pass/fail totals — this is the baseline group F compares
        against
  - [ ] Success: a log file and a recorded summary. Expected shape is 171 passed
        with 2 `test_cli_lists.py` failures, plus zero or one catalog race. **A
        zero here is not a problem** — the flake is intermittent; note it and
        continue, because slice 169 already recorded the rate

- [ ] **A.4 [agent] Commit the captured baselines**
  - [ ] Commit the baseline files and a short note recording what A.1–A.3 found
  - [ ] Success: `git log --oneline -1` shows the commit; the baselines are now
        recoverable by anyone reading the branch

---

## Group B — Create and configure the test cluster

Effort: 2/5. Every task here is **[PM]** except the verification tasks.

- [ ] **B.1 [PM] Create the cluster**
  - [ ] `pg_createcluster 17 test`
  - [ ] Do not pass a data directory — the default
        `/var/lib/postgresql/17/test` is the deliberate choice (it keeps the test
        cluster off `/data`, where the WAL archive and base backups live)
  - [ ] Success: the command exits zero and `pg_lsclusters` lists `17 test`
  - [ ] If it fails for insufficient privilege, stop — do not attempt a workaround

- [ ] **B.2 [agent] Record the assigned port**
  - [ ] Read the port from `pg_lsclusters`, not from an assumption
  - [ ] Write it into the task notes; every later task uses this value
  - [ ] Success: the port is recorded. If it is not 5433, that is fine — carry
        the real value forward rather than trying to force 5433

- [ ] **B.3 [PM] Configure the test cluster**
  - [ ] Edit `/etc/postgresql/17/test/postgresql.conf` only. Confirm the path
        contains `/test/` before saving
  - [ ] `shared_preload_libraries = 'timescaledb'`
  - [ ] `listen_addresses = 'localhost'` — the test cluster has no reason to be
        LAN-reachable
  - [ ] Apply the sizing budget from the design: `shared_buffers` 2GB,
        `work_mem` 32MB, `maintenance_work_mem` 256MB, `max_connections` 50,
        `max_worker_processes` 16, `timescaledb.max_background_workers` 8
  - [ ] Success: the file contains all six settings plus the preload and listen
        lines, and `/etc/postgresql/17/main/postgresql.conf` is unmodified
        (`git`-untracked, so verify by timestamp or checksum captured in A)

- [ ] **B.4 [PM] Start the test cluster**
  - [ ] `pg_ctlcluster 17 test start`
  - [ ] Success: `pg_lsclusters` shows `17 test` as `online`
  - [ ] If it starts and immediately exits, read the cluster's own log for the
        cause, fix the config, and restart **the test cluster only**

- [ ] **B.5 [agent] GATE — verify TimescaleDB is actually loaded**
  - [ ] This runs **before any test run**, not after. A cluster missing the
        extension or its background workers produces hangs that read as test
        bugs rather than configuration problems
  - [ ] `SHOW shared_preload_libraries;` on the test cluster → contains `timescaledb`
  - [ ] `SHOW timescaledb.max_background_workers;` → greater than zero
  - [ ] `CREATE EXTENSION timescaledb;` in a scratch database on the test cluster
        succeeds, then drop that database
  - [ ] Success: all three pass. **On failure, stop the slice here** — correct the
        config, restart the test cluster, and re-run this gate before proceeding

- [ ] **B.6 [agent] Verify version parity with production**
  - [ ] `SELECT version();` and
        `SELECT extversion FROM pg_extension WHERE extname='timescaledb';` on the
        test cluster
  - [ ] Success: PostgreSQL 17.11 and TimescaleDB 2.29.1, matching production. A
        mismatch means the cluster was built from different binaries and the
        migration-fidelity assumption is void

- [ ] **B.7 [agent] Verify the test cluster is not LAN-reachable**
  - [ ] Attempt `psql -h 192.168.1.144 -p <port> -d postgres -c "SELECT 1"`
  - [ ] Success: the connection is **refused**. A successful connection means
        `listen_addresses` did not take effect — return to B.3

---

## Group C — Provision roles on the test cluster

Effort: 1/5.

- [ ] **C.1 [PM] Run the existing role provisioning script against the test cluster**
  - [ ] `psql "<test-cluster-superuser-url>" -v ON_ERROR_STOP=1 -v with_test_admin=1 -f scripts/provision_roles.sql`
  - [ ] Use the same artifact production uses — do not write a variant. The
        script is idempotent and parameterized for exactly this
  - [ ] Success: the script exits zero
  - [ ] Note: `with_replication` is **not** passed. The constraint that made it
        opt-in no longer applies on a separate cluster, but changing that is
        slice 915's business, not this slice's

- [ ] **C.2 [PM] Set the test admin password out-of-band**
  - [ ] `ALTER ROLE <test_admin_role> WITH PASSWORD '...'` executed directly,
        never committed to any file in the repository
  - [ ] Success: the role can authenticate from localhost

- [ ] **C.3 [agent] Verify the roles exist on the test cluster**
  - [ ] Query `pg_roles` on the test cluster
  - [ ] Success: the test admin role exists with `CREATEDB` and `CREATEROLE`,
        which is what the fixture needs to create and drop databases

- [ ] **C.4 [agent] Verify production's roles are unchanged**
  - [ ] Re-run the A.1 query against production and diff against the A.1 snapshot
  - [ ] Success: the diff is **empty**. A non-empty diff means the provisioning
        ran against the wrong cluster — stop and investigate before continuing

- [ ] **C.5 [agent] Commit the recorded host evidence**
  - [ ] Groups B and C change *host* state, not repository state, so without this
        the slice runs several groups with nothing reviewable committed
  - [ ] Commit the recorded port from B.2, the gate output from B.5, the version
        parity from B.6, and the role verification from C.3–C.4
  - [ ] Success: the branch now contains enough evidence for someone else to tell
        what the cluster looks like without logging into the host

---

## Group D — Repoint the test suite at the new cluster

Effort: 1/5.

- [ ] **D.1 [agent] Update `MT_TIMESCALE_TEST_URL` in `.env`**
  - [ ] Change the host and port from `192.168.1.144:5432` to `127.0.0.1:<port>`
  - [ ] Perform this edit with **no test run in flight** — a run that reads the
        old value mid-swap lands on production
  - [ ] Do not `source .env`; edit it directly
  - [ ] Success: grepping the variable shows the new host and port. `.env` remains
        gitignored and uncommitted

- [ ] **D.2 [agent] Smoke-test the fixture path end to end**
  - [ ] Run one small DB-backed integration test, e.g. a single migration test
  - [ ] Success: it passes, and the database it created was on the test cluster.
        Confirm by listing databases on **production** during or after the run
        and seeing no new `mt_test_*` entries

---

## Group E — Make misconfiguration fail loudly

Effort: 2/5. This is the only code change in the slice.

- [ ] **E.1 [agent] Replace the silent skip with an error**
  - [ ] In `test/conftest.py`, the fixture currently calls
        `pytest.skip("MT_TIMESCALE_TEST_URL not set")` when the variable is
        absent — a green run that tested nothing
  - [ ] Make the absent-variable case fail with a message naming the variable and
        what to set it to
  - [ ] Both database fixtures need this, not just the first one
  - [ ] Success: the fixtures error rather than skip when the variable is unset

- [ ] **E.2 [agent] Add a guard rejecting a production-cluster test URL**
  - [ ] Refuse to proceed when `MT_TIMESCALE_TEST_URL` resolves to the production
        cluster. Match on the semantic content — host and port — not on an exact
        string, so a URL with different credentials or query parameters is still
        caught
  - [ ] The failure message must say plainly that the test URL points at
        production and must name the expected test-cluster port
  - [ ] Success: setting the variable to the production URL makes the DB-backed
        tiers fail immediately, before any database is created

- [ ] **E.3 [agent] Test both guards**
  - [ ] A test proving the unset case errors and does not skip
  - [ ] A test proving a production-pointing URL is refused, covering at least
        one variation (different credentials or a trailing parameter) so the
        guard is not matching a literal string
  - [ ] Success: both tests pass, and each fails if its guard is reverted —
        verify by temporarily reverting, not by assuming

- [ ] **E.4 [agent] Commit the guard work**
  - [ ] Commit `test/conftest.py` and the new tests together
  - [ ] Success: the commit message says what the guards prevent, not just that
        guards were added

---

## Group F — Prove the slice delivered

Effort: 2/5. This group is the evidence, so record actual output rather than
summarising it.

- [ ] **F.1 [agent] Five consecutive integration runs, asserting zero catalog races**
  - [ ] Run the integration tier five times, teeing each to its own log
  - [ ] Assert across all five logs — the check must **exit non-zero on any hit**,
        not merely print counts. A `grep -c` per file that prints `0` five times
        and exits zero regardless is not an assertion
  - [ ] Success: zero occurrences of `tuple concurrently updated` across all five
        runs. Any single occurrence fails the slice's central criterion
  - [ ] **Void-run rule:** a run that dies on a connection refusal or is
        interrupted is **discarded, not counted** — neither as a pass nor as a
        baseline deviation. Only five *complete* runs satisfy the criterion. Note
        each discarded run and why, so the count cannot be quietly padded

- [ ] **F.2 [agent] Compare the pass/fail set to the A.3 baseline**
  - [ ] Success: the same tests pass and the same two `test_cli_lists.py` tests
        fail. **No test newly fails, and no test newly skips** — a new skip is the
        failure mode this slice is specifically guarding against

- [ ] **F.3 [agent] Confirm the background scheduler genuinely runs**
  - [ ] Run `test_policy_advances_head.py` on the test cluster
  - [ ] Success: 9/9 pass. This test waits on the real TimescaleDB scheduler
        rather than calling `run_job()`, so passing proves the test cluster's
        background workers are live — not merely configured

- [ ] **F.4 [agent] Measure production impact during a run**
  - [ ] While an integration run is in progress, time the coverage freshness
        probe against production
  - [ ] Success: within its 10-second budget. Slice 169 measured 1.33s on this
        host, so a result near the budget is a real regression, not noise

- [ ] **F.5 [agent] Confirm the host never swapped**
  - [ ] Check swap usage during and after the F.1 runs
  - [ ] Success: zero. Swapping means the sizing in B.3 is wrong by an order of
        magnitude — stop and re-size rather than tuning around it

- [ ] **F.6 [agent] Confirm production was never restarted or altered**
  - [ ] `SELECT pg_postmaster_start_time();` on production, compared to A.2
  - [ ] Production role diff against A.1, compared again at the end
  - [ ] Success: the timestamp is identical and the role diff is empty

---

## Group G — Document and close

Effort: 1/5.

- [ ] **G.1 [agent] Write the test-cluster runbook**
  - [ ] Create a runbook covering: that two clusters exist and which is which,
        the test cluster's port and data directory, the full recreation procedure
        (B.1 through C.2), and the gate checks from B.5
  - [ ] State why the data directory is not on `/data` — the WAL archive and base
        backups live there, and a full filesystem breaks archiving and PITR
  - [ ] Success: someone who has never seen this slice can recreate the cluster
        from the runbook alone

- [ ] **G.2 [agent] Fold the measured values back into the slice design**
  - [ ] Replace the design's draft verification walkthrough placeholders with the
        actual port, the actual measured freshness-probe time, and the F.1 result
  - [ ] Success: the walkthrough is a record of what was run, not a plan for what
        might be

- [ ] **G.3 [agent] Record the follow-up this slice deliberately did not do**
  - [ ] The suite passes throwaway role names to `provision_roles.sql` because
        `pg_authid` is cluster-wide. On a dedicated cluster that is no longer
        necessary, and using the real role names would make the test exercise the
        exact invocation production runs
  - [ ] Write it as an entry in the **Future Work** section of
        `user/architecture/900-slices.foundation-cleanup.md`, which is where this
        class of small deferred item already lives
  - [ ] Success: the entry names the file, why the workaround existed, and why it
        is no longer needed — enough that a reader who never saw this slice can
        act on it

- [ ] **G.4 [agent] Final commit**
  - [ ] Commit the runbook, the design update, and the recorded evidence
  - [ ] Success: the working tree is clean and `cf check` reports no new warnings

---

## Definition of done

All ten of the slice's success criteria are met, with the two that matter most
demonstrated rather than argued:

- Five consecutive integration runs, zero catalog races, asserted by a command
  that fails on a hit.
- Production's postmaster start time and role set identical to the values
  captured in group A, before any work began.
