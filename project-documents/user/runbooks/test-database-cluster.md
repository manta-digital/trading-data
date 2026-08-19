---
docType: runbook
project: trading-data
scope: project-wide
host: hammerhead (192.168.1.143)
dateCreated: 20260819
dateUpdated: 20260819
status: current
---

# Test Database Cluster — hammerhead

**Every command here runs on hammerhead**, as the login user, using `sudo` where
marked. Nothing in this runbook touches the production host.

The repository is already cloned at `~/source/repos/manta/trading-data`, so the
configuration files this runbook installs come from the checkout rather than from
pasted text. Run `git pull` first so you have the current versions.

Each command is a **single line** and can be pasted directly. There are no
heredocs — multi-line pastes into an interactive shell are unreliable.

## Why a separate machine

Test databases were previously created inside the production cluster on
manta9000. That cost a rotating catalog-race flake, left throwaway roles in
production's shared `pg_authid`, and made the test suite compete for production's
memory and disk. Slice 917 moved them here. The reasoning is in
`user/slices/917-slice.dedicated-test-database-cluster-stop-sharing-a-catalog-with-production.md`.

## Version parity is the standing obligation

This cluster must run the **same upstream PostgreSQL and TimescaleDB as
production**. That parity is free when both live on one machine and is a
maintained property once they do not. It is held by two mechanisms: `apt-mark
hold` on the packages, and an assertion the test suite itself runs.

| | Production (26.04) | Hammerhead (24.04) |
|---|---|---|
| PostgreSQL | `17.11-1.pgdg26.04+2` | `17.11-1.pgdg24.04+2` |
| TimescaleDB | `2.29.1~ubuntu26.04-1710` | `2.29.1~ubuntu24.04-1710` |

The differing suffix is packaging metadata for a different Ubuntu release, not
different code. **Hammerhead does not need upgrading to 26.04.**

---

## Step 1 — Add the package repositories

PostgreSQL's own installer script picks the right suite for this release:

```bash
sudo apt install -y postgresql-common
```

```bash
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
```

TimescaleDB's repository and signing key:

```bash
echo "deb https://packagecloud.io/timescale/timescaledb/ubuntu/ $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/timescaledb.list
```

```bash
curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/timescaledb.gpg
```

```bash
sudo apt update
```

**Verify before continuing** — both exact versions must be offered:

```bash
apt-cache policy postgresql-17 timescaledb-2-postgresql-17 | grep -E "^[a-z]|17\.11-1\.pgdg24\.04|2\.29\.1~ubuntu24\.04"
```

## Step 2 — Install the pinned versions

Newer builds exist in both repositories — TimescaleDB 2.29.2 among them — so the
versions are named explicitly. An unversioned install lands ahead of production
on day one and silently forfeits parity.

```bash
sudo apt install -y postgresql-17=17.11-1.pgdg24.04+2 timescaledb-2-postgresql-17=2.29.1~ubuntu24.04-1710 timescaledb-2-loader-postgresql-17=2.29.1~ubuntu24.04-1710
```

**If apt cannot resolve one of these versions, stop.** Do not relax the pin to
whatever resolves — that is the failure this step exists to prevent.

## Step 3 — Hold the packages

Without this, an unattended `apt upgrade` on a machine you use interactively
drifts the cluster away from production without anyone noticing.

```bash
sudo apt-mark hold postgresql-17 timescaledb-2-postgresql-17 timescaledb-2-loader-postgresql-17
```

```bash
apt-mark showhold
```

Expect all three listed.

## Step 4 — Create the cluster

Installing `postgresql-17` normally creates a `main` cluster automatically. Check
what exists before creating anything:

```bash
pg_lsclusters
```

If a `17/main` cluster is already present and unused, use it. Otherwise create
one — the default data directory on the 1.7 TB root filesystem is correct, so
pass no location:

```bash
sudo pg_createcluster 17 main
```

**Record the port** it reports. It is expected to be 5432 on this machine since
nothing else uses it, but take the value the tool assigns rather than assuming.

## Step 5 — Install the cluster configuration

From the checkout, so the file is version-controlled rather than retyped:

```bash
sudo install -m 644 -o root -g root ~/source/repos/manta/trading-data/deploy/test-cluster/917-test-cluster.conf /etc/postgresql/17/main/conf.d/917-test-cluster.conf
```

`conf.d` is included by the stock `postgresql.conf` (`include_dir = 'conf.d'`),
so no existing file is edited. This mirrors how slice 915 installed
`915-archiving.conf` on production.

## Step 6 — Admit only manta9000

Append one line to `/etc/postgresql/17/main/pg_hba.conf`:

```bash
echo "host    all    trading_test_admin    192.168.1.144/32    scram-sha-256" | sudo tee -a /etc/postgresql/17/main/pg_hba.conf
```

**Never widen this to `0.0.0.0/0`.** The single source address is the access
control that makes binding all interfaces acceptable. Only the test admin role
needs to connect — the privilege suite authenticates as that credential and then
uses `SET ROLE`, so it never opens a connection as a throwaway role.

## Step 7 — Start the cluster

```bash
sudo pg_ctlcluster 17 main start
```

```bash
pg_lsclusters
```

Expect status `online`. If it starts and immediately exits, read
`/var/log/postgresql/postgresql-17-main.log` for the cause, correct the config,
and start it again.

## Step 8 — Gate checks, before anything else connects

**Run these before any test run.** A cluster missing the extension or its
background workers produces hangs that read as test failures rather than as a
configuration problem.

```bash
sudo -u postgres psql -At -c "SHOW shared_preload_libraries;"
```

Must contain `timescaledb`.

```bash
sudo -u postgres psql -At -c "SHOW timescaledb.max_background_workers;"
```

Must be greater than zero.

```bash
sudo -u postgres psql -At -c "SELECT version();"
```

Must report PostgreSQL 17.11.

```bash
sudo -u postgres psql -d postgres -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
```

Then confirm the extension version:

```bash
sudo -u postgres psql -At -c "SELECT extversion FROM pg_extension WHERE extname='timescaledb';"
```

Must report 2.29.1. **If any of these fail, stop and fix before continuing** —
everything downstream produces confusing symptoms rather than clear errors.

## Step 9 — Provision the roles

Applied as a superuser, using the same artifact production uses. The script's own
header explains why: creating roles and granting `postgres` requires rights the
maintenance role does not hold on itself.

```bash
sudo -u postgres psql -v ON_ERROR_STOP=1 -v with_test_admin=1 -f ~/source/repos/manta/trading-data/scripts/provision_roles.sql
```

`with_replication` is deliberately **not** passed. The constraint that made it
opt-in was production and tests sharing one cluster, which no longer applies —
but changing that flag belongs to slice 915, not here.

## Step 10 — Set the test admin password

Choose a password and set it directly. **Never commit it**, and never write it
into any file in the repository.

```bash
sudo -u postgres psql -c "ALTER ROLE trading_test_admin WITH PASSWORD 'REPLACE-ME';"
```

The matching `MT_TIMESCALE_TEST_URL` on manta9000's `.env` carries the same
password. That file is gitignored.

---

## Verification

From **manta9000**, not from here — this proves the path the test suite actually
uses:

```bash
psql "$MT_TIMESCALE_TEST_URL" -At -c "SELECT version();"
```

From any **other** machine on the LAN, the same connection must be refused by
`pg_hba.conf`. A success there means Step 6 was too broad.

## When production upgrades PostgreSQL or TimescaleDB

Parity is a property, not a one-time act. When production moves:

1. Lift the holds here: `sudo apt-mark unhold postgresql-17 timescaledb-2-postgresql-17 timescaledb-2-loader-postgresql-17`
2. Install the new versions explicitly, named as in Step 2.
3. Re-apply the holds as in Step 3.
4. Re-run the Step 8 gate checks.

The test suite asserts the versions it finds, so a drifted cluster announces
itself — but the assertion reports the problem, it does not fix it.

## Rebuilding from scratch

Steps 1 through 10 in order. Nothing here holds data worth preserving: test
databases are UUID-named, created per fixture, and dropped on teardown. Dropping
the cluster and rebuilding costs only the time to run this runbook.
