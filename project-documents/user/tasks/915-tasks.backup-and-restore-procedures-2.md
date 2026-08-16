---
docType: tasks
slice: backup-and-restore-procedures
project: trading-data
lldReference: project-documents/user/slices/915-slice.backup-and-restore-procedures.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [913]
interfaces: []
projectState: >
  Continuation of `915-tasks.backup-and-restore-procedures-1.md`, which must be
  complete before this file begins. Part 1 delivers the measurement pass, the
  metadata dump tier, the base-backup wrapper, WAL archiving (including the
  PostgreSQL restart), archive-failure monitoring, and the checksum-verified
  offsite copy. Part 2 is where the slice's actual deliverable lands: an
  executed restore drill, a demonstrated point-in-time recovery, scheduling, and
  the runbook. Tooling that has never restored anything is a hypothesis — Part 1
  builds the hypothesis, Part 2 tests it.
dateCreated: 20260816
dateUpdated: 20260816
status: not_started
---

# Tasks: Backup and Restore Procedures — Part 2 (Drill, PITR, Runbook)

## Context summary

Part 1 built and proved the mechanisms. This part proves **recovery**, which is
the only thing the slice is actually for.

Three properties carry over from Part 1 and govern everything here:

- **Verification is by content, never by exit code or catalog presence** (D6).
  Row counts and cagg parity against source — an object created or interrupted
  mid-incident is presumed damaged, per `sql.md` and the 2026-08-04 restore.
- **Never restore over `trading`** (D6). A separate host, or a second cluster on
  a distinct port.
- **Section 7's restore target is resolved**: `nvme1n1` on prod, PM-reported
  ~800 GB free (2026-08-16), against a measured 141 GB `trading`. That is ample
  headroom for the compressed archive plus the extracted cluster. The one thing
  still to confirm is whether `nvme1n1` is the same device as `PGDATA` — if it
  is, the restore drill shares fate with production and that must be recorded
  rather than discovered (Part 1 task 1.2).

Sections 7 and 8 are distinct on purpose: Section 7 proves the base backup
restores, Section 8 proves WAL replay works. Passing the first tells you nothing
about the second.

All decisions referenced (D1–D7) are in the LLD.

---

## Section 7 — The restore drill (the actual deliverable)

Target resolved: **`nvme1n1` on prod, ~800 GB free** against a 141 GB source.
Disk is no longer the blocker it was when the design was written.

- [ ] **7.1 Confirm the restore target**
  - [x] Target chosen: `nvme1n1` on `.144`, PM-reported ~800 GB free
        (2026-08-16). Against a measured 141 GB `trading` plus its compressed
        archive, headroom is ample
  - [ ] Restore as a **second cluster on a distinct port**, since the target is
        the production host rather than a separate machine. Never over `trading`
        (D6)
  - [ ] Record explicitly how the restored cluster is prevented from touching
        the production data directory — a distinct `PGDATA`, a distinct port,
        and a distinct service invocation. This is the step where an operator
        under pressure can do real damage, so it belongs in the runbook verbatim
  - [ ] Confirm whether `nvme1n1` also holds `PGDATA`. If it does, note that the
        drill is consuming the same device production runs on, and watch free
        space during 7.2 rather than assuming 800 GB stays available
  - [ ] Success: target confirmed with measured free space and an explicit
        written separation from production
  - [ ] Effort: 1

- [ ] **7.2 Restore the base backup and bring the cluster up**
  - [ ] Restore from the **offsite copy** if practical, exercising the real
        recovery path rather than the convenient local one
  - [ ] Extract, configure recovery, start the cluster on the distinct port
  - [ ] Record every command as run and the wall-clock duration of each stage —
        this is the material the runbook is built from
  - [ ] Success: a cluster starts from the backup and accepts connections
  - [ ] Effort: 3

- [ ] **7.3 Verify content parity on the priority tables**
  - [ ] Compare `count(*)` for `minute_ohlcv`, `daily_ohlcv`, and
        `acquisition_state` against source (success criterion 6)
  - [ ] Expect the 4.4 B-class figure for `minute_ohlcv` and **45,537-class for
        `acquisition_state`** (measured 2026-08-16, growing daily — 12,191 rows
        were touched in the 24 hours before that reading)
  - [ ] Use exact `count(*)` only. Two estimate sources are known wrong on this
        database: `approximate_row_count` is +68% off on `minute_ohlcv`, and
        `pg_stat_user_tables.n_live_tup` was measured reporting 0 for several
        populated metadata tables. Either would let an empty restore pass
  - [ ] Counts will differ slightly from live source if acquisition ran after
        the backup — compare against the source **as of the backup's LSN/time**,
        or accept a documented delta and explain it rather than hand-waving
  - [ ] Success: counts match or the delta is explained by writes after the
        backup start
  - [ ] Effort: 3

- [ ] **7.4 Verify continuous aggregates by content, not catalog presence**
  - [ ] For each rollup cagg, compare an aggregate over a **closed** historical
        window against the same computation on its source hypertable
  - [ ] Catalog presence proves nothing — this is the direct lesson from the
        2026-08-04 restore, recorded in `sql.md`: an object created or
        interrupted mid-incident is presumed damaged
  - [ ] Include the coverage caggs (`daily_coverage`, `minute_coverage`), which
        slice 169 rebuilt on 2026-08-16 — the restore must carry the repaired
        state, not the pre-rebuild holes
  - [ ] Success: every cagg matches its source over the chosen window; a
        mismatch names the aggregate and the window
  - [ ] Effort: 3

- [ ] **7.5 Tear down the restored cluster**
  - [ ] Stop and remove the restored cluster and reclaim the disk
  - [ ] Confirm production is untouched: `trading` still serving, daemon still
        advancing
  - [ ] Success: no residue; no production impact
  - [ ] Effort: 1

- [ ] **7.6 Retire the 2026-08-10 cloned copy**
  - [ ] **Only after 7.3 and 7.4 pass.** The clone is torn and unverified, but
        until the drill succeeds it is the only copy that exists. Deleting it
        earlier trades a known-imperfect backup for an unproven one
  - [ ] Confirm the drive holds both the clone and the new base backup
        simultaneously through the drill — reclaiming its space is the *result*
        of the drill, not a precondition for it
  - [ ] Once the drill has passed and the offsite copy is checksum-verified
        (6.5), delete the clone and record the reclaimed space
  - [ ] Success: the torn copy is gone, and what replaced it has been restored
        from at least once. This closes out the 2026-08-10 procedure rather than
        leaving two backup regimes in play
  - [ ] Effort: 1

---

## Section 8 — Point-in-time recovery proof

Distinct from Section 7. Section 7 proves the base backup restores; this proves
**WAL replay works**, which is the only thing that turns "segments are being
copied" into "recovery to an arbitrary point exists."

- [ ] **8.1 Plant a timestamped sentinel on prod**
  - [ ] Create a scratch table on prod and insert a sentinel row; note the
        timestamp precisely (before and after)
  - [ ] Keep it trivially reversible and clearly named as scratch — this is a
        deliberate write to production for test purposes
  - [ ] Success: sentinel exists with known before/after timestamps recorded
  - [ ] Effort: 1

- [ ] **8.2 Recover to before the sentinel and confirm absence**
  - [ ] Restore a copy with `recovery_target_time` set **before** the sentinel
        insert
  - [ ] Confirm the scratch row is **absent**
  - [ ] Success: the row is absent — proving replay stopped where instructed
        rather than replaying everything
  - [ ] Effort: 3

- [ ] **8.3 Recover to after the sentinel and confirm presence**
  - [ ] Restore with `recovery_target_time` **after** the insert
  - [ ] Confirm the scratch row is **present**
  - [ ] Both directions are required: absence alone is also what a failed replay
        looks like. Presence in the second run is what distinguishes working
        PITR from broken recovery (success criterion 7)
  - [ ] Success: present in the after-restore, absent in the before-restore
  - [ ] Effort: 2

- [ ] **8.4 Drop the scratch table from prod**
  - [ ] Remove the sentinel scratch table under the maintenance credential
  - [ ] Success: prod schema is back to its pre-drill state
  - [ ] Effort: 1

---

## Section 9 — Scheduling and runbook

- [ ] **9.1 Schedule the two tiers via cron**
  - [ ] Metadata tier nightly; base-backup tier on the infrequent cadence
        settled against 3.4's measured duration and 4.5's retention (D4)
  - [ ] Use `cron`, not a systemd timer — the host has no process manager and no
        units installed (D4, confirmed by the production-deploy runbook). Note
        in a comment that this migrates to a timer if the `/opt` + systemd
        deployment lands
  - [ ] Ensure cron's environment is sufficient: cron does not load the
        operator's shell profile, so absolute paths and explicitly-set variables
        are required. Verify by observing an actual scheduled run, not by
        reading the crontab
  - [ ] Ensure the base-backup schedule does not collide with acquisition peaks
        or whatever re-invokes the acquisition passes (1.6)
  - [ ] Success: both tiers run on schedule unattended, verified by observing a
        real scheduled execution and its output
  - [ ] Effort: 3

- [ ] **9.2 Verify a full unattended cycle**
  - [ ] Let a scheduled metadata dump and a scheduled base backup run without
        intervention; confirm both landed offsite and passed checksum
  - [ ] Confirm the 5.1 monitor stayed green throughout
  - [ ] Success: the procedure works when nobody is watching, which is the only
        condition under which it will ever actually run
  - [ ] Effort: 2

- [ ] **9.3 Write the backup-and-restore runbook**
  - [ ] Create `project-documents/user/runbooks/backup-and-restore.md`, alongside
        `cagg-maintenance-pausing.md` and `coverage-cagg-rebuild.md`
  - [ ] Include the **real commands as executed** and the **observed timings**
        from 3.4, 6.5, 7.2, and 8.2 — not idealized ones (success criterion 9)
  - [ ] Cover all four recovery scenarios from the LLD's recovery-path table:
        metadata-only restore, whole-cluster restore, PITR to a chosen time, and
        the degraded base-backup-only case
  - [ ] Follow the coverage-cagg-rebuild runbook's structure: numbered steps
        with explicit credentials per step. Note which steps need the maintenance
        credential and which need the `postgres` OS user
  - [ ] Include the archive-failure response: what the alarm looks like, and what
        to do when `pg_wal` is growing
  - [ ] Record the credential-extraction idiom used by the existing runbooks
        (grep from `.env`, never `source` — the `$`-in-password trap)
  - [ ] Success: someone following the runbook under pressure, without this
        context, can restore. That is the test — not whether it is complete on
        its own terms
  - [ ] Effort: 3

- [ ] **9.4 Record the drill result and set a repeat expectation**
  - [ ] Write the drill's date, duration, and outcome into the runbook
  - [ ] State when it should next be repeated. The realistic failure mode of
        backup work is that it is verified once and rots (LLD risks)
  - [ ] Success: the runbook says when this was last proven and when it is due
        again
  - [ ] Effort: 1

---

## Section 10 — Completion

- [ ] **10.1 Full validation pass**
  - [ ] Run unit and integration tiers separately via `scripts/run_tests.py`
  - [ ] `ruff check` and `ruff format --check` clean on slice-touched files
  - [ ] Both static prod-URL ratchet guards still pass — Section 2's tests are
        the ones that could regress them
  - [ ] Success: tiers green apart from documented pre-existing failures
  - [ ] Effort: 2

- [ ] **10.2 Verify every success criterion has evidence**
  - [ ] Walk the LLD's nine success criteria and record which task produced the
        evidence for each. Criteria 2, 6, and 7 require **demonstrated**
        behavior — an alarm that fired, a drill that ran, a PITR in both
        directions — not implemented tooling
  - [ ] Success: each of the nine maps to a concrete recorded observation
  - [ ] Effort: 2

- [ ] **10.3 Update slice status and commit**
  - [ ] Set the slice design `status` to `complete` and check off the 915 entry
        in [900-slices.foundation-cleanup.md](../architecture/900-slices.foundation-cleanup.md)
  - [ ] Refine the LLD's Verification Walkthrough with the commands as actually
        run and the measured values (it is marked draft pending Phase 6)
  - [ ] Merge the work branch into **`trading-data-maintenance`**, not `main`
  - [ ] Success: documents reflect delivered state; branch merged to the correct
        target
  - [ ] Effort: 1

---

## Follow-ups (explicitly not this slice)

Recorded here so they are not lost, per D7:

- **Encryption at rest in the object store.** Worth doing; the credential and
  key-management design is its own decision.
- **Cleaning up the 9 leftover test databases** (`mt_test_*`, `mt_diag_*`,
  ~100 MB) and fixing the fixture teardown that orphans them. They will appear
  in the backup; harmless but untidy.
- **Migrating the cron entries to systemd timers** if the `/opt` + systemd
  deployment lands.
- **Replication / hot standby.** A different problem — availability, not
  recoverability. A standby replicates a `DROP TABLE` instantly.
- **`restore_metadata.py` ledger-replay boundary.** Objects dropped while their
  creating migration is still ledgered are invisible to replay. This slice may
  use the tool; redesigning it is separate.
