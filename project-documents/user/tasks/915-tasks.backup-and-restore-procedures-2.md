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
dateUpdated: 20260817
status: in_progress
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
- **Section 7's restore target is resolved and measured**: `/data`
  (`/dev/nvme1n1p1`), **760 GB free**, against a measured 141 GB `trading`.
  Ample headroom for the compressed archive plus the extracted cluster. The host
  survey confirmed `/data` is a **different physical device** from `PGDATA`
  (`/dev/nvme0n1p2`), so the drill does not share fate with production.

Sections 7 and 8 are distinct on purpose: Section 7 proves the base backup
restores, Section 8 proves WAL replay works. Passing the first tells you nothing
about the second.

All decisions referenced (D1–D7) are in the LLD.

---

## Section 7 — The restore drill (the actual deliverable)

Target resolved: **`nvme1n1` on prod, ~800 GB free** against a 141 GB source.
Disk is no longer the blocker it was when the design was written.

- [x] **7.1 Confirm the restore target**
  - [x] Target chosen and measured: **`/data`** (`/dev/nvme1n1p1`), **760 GB
        free** on the host survey 2026-08-16 — slightly under the PM's ~800 GB
        recollection, so size against 760 GB. Against 141 GB plus its compressed
        archive, headroom is ample
  - [x] `/data` is a **separate physical device** from `PGDATA`
        (`/dev/nvme0n1p2`), so the restored cluster competes with production for
        neither space nor spindle
  - [x] Restore as a **second cluster on a distinct port**, since the target is
        the production host rather than a separate machine. Never over `trading`
        (D6)
  - [x] Record explicitly how the restored cluster is prevented from touching
        the production data directory — a distinct `PGDATA`, a distinct port,
        and a distinct service invocation. This is the step where an operator
        under pressure can do real damage, so it belongs in the runbook verbatim
  - [x] Confirm whether `nvme1n1` also holds `PGDATA`. If it does, note that the
        drill is consuming the same device production runs on, and watch free
        space during 7.2 rather than assuming 800 GB stays available
  - [x] Success: target confirmed with measured free space and an explicit
        written separation from production
  - [x] Effort: 1
  - [x] Done 2026-08-17: /data/restore-test on /dev/nvme1n1p1 (682 GB free, separate device from PGDATA — nvme1n1 does NOT hold PGDATA). Separation recorded verbatim in the runbook: distinct PGDATA owned by manta, listen_addresses='' (no TCP, socket-only in a 0700 dir), pg_ctl as manta (never systemd/postgres user), archive_mode=off in the drill config.

- [x] **7.2 Restore the base backup and bring the cluster up**
  - [x] Restore from the **offsite copy** if practical, exercising the real
        recovery path rather than the convenient local one
  - [x] Extract, configure recovery, start the cluster on the distinct port
  - [x] Record every command as run and the wall-clock duration of each stage —
        this is the material the runbook is built from
  - [x] Success: a cluster starts from the backup and accepts connections
  - [x] Effort: 3
  - [x] Done 2026-08-17: extraction 13m47s (152 GB tree), recovery replay 42s, cluster accepting connections; every command and duration recorded in the runbook Step 6 rewrite. Offsite-copy restore attempted first per the subitem: rclone v1.60.1 HUNG downloading the 84 GB object (twice — multithread and single-stream; zero TCP connections; a 78 MB object downloads fine), so the drill ran from the local copy, which is checksum-identical to the offsite copy (verified at upload). Follow-up: upgrade rclone from the 2022 distro build, then re-exercise the download path.
  - [x] Follow-up resolved 2026-08-18: rclone upgraded to v1.75.0; the full 84.5 GB offsite copy downloaded in 2h05m (~11 MB/s) and checksum-verified (0 differences). The offsite restore path is proven end to end; from-B2 disaster restore is bounded at ~2 h download + ~15 m extraction.

- [x] **7.3 Verify content parity on the priority tables**
  - [x] Compare `count(*)` for `minute_ohlcv`, `daily_ohlcv`, and
        `acquisition_state` against source (success criterion 6)
  - [x] Expect the 4.4 B-class figure for `minute_ohlcv` and **45,537-class for
        `acquisition_state`** (measured 2026-08-16, growing daily — 12,191 rows
        were touched in the 24 hours before that reading)
  - [x] Use exact `count(*)` only. Two estimate sources are known wrong on this
        database: `approximate_row_count` is +68% off on `minute_ohlcv`, and
        `pg_stat_user_tables.n_live_tup` was measured reporting 0 for several
        populated metadata tables. Either would let an empty restore pass
  - [x] Counts will differ slightly from live source if acquisition ran after
        the backup — compare against the source **as of the backup's LSN/time**,
        or accept a documented delta and explain it rather than hand-waving
  - [x] Success: counts match or the delta is explained by writes after the
        backup start
  - [x] Effort: 3
  - [x] Done 2026-08-17: exact count(*) restored vs source — minute_ohlcv 4,464,471,566 = 4,464,471,566; daily_ohlcv 65,735,419; acquisition_state 45,537; instruments 32,075; data_gaps 105,774; universe_members 1,127 — ALL EXACT, no delta to explain (daemon down since before backup start). Counts used columnstore metadata: 12 s for 4.46 B rows.

- [x] **7.4 Verify continuous aggregates by content, not catalog presence**
  - [x] For each rollup cagg, compare an aggregate over a **closed** historical
        window against the same computation on its source hypertable
  - [x] Catalog presence proves nothing — this is the direct lesson from the
        2026-08-04 restore, recorded in `sql.md`: an object created or
        interrupted mid-incident is presumed damaged
  - [x] Include the coverage caggs (`daily_coverage`, `minute_coverage`), which
        slice 169 rebuilt on 2026-08-16 — the restore must carry the repaired
        state, not the pre-rebuild holes
  - [x] Success: every cagg matches its source over the chosen window; a
        mismatch names the aggregate and the window
  - [x] Effort: 3
  - [x] Done 2026-08-17: all NINE caggs (4 minute rollups, 3 daily rollups, both coverage caggs) compared over closed Q2-2026 windows, two ways. (1) Carried state: windowed signatures (count, sum(volume), sum(close)/sum(bars), bucket extrema) restored vs prod — all nine EXACT, coverage caggs carry slice-169's repaired state. (2) Recomputation from source on the restored cluster: 7 of 9 exact; daily_weekly_ohlcv and daily_quarterly_ohlcv each short exactly one day_count — root-caused to SPMA 2026-06-18, a zero-volume flat bar backfilled after those caggs materialized that window (invisible to sum/last/extrema aggregates; only day_count catches it; monthly and coverage caggs materialized later and carry it). NOT a restore defect — prod has the identical staleness and the restore carried it faithfully. Follow-up filed with the PM: refresh those two cagg windows on prod.

- [x] **7.5 Tear down the restored cluster**
  - [x] Stop and remove the restored cluster and reclaim the disk
  - [x] Confirm production is untouched: `trading` still serving, daemon still
        advancing
  - [x] Success: no residue; no production impact
  - [x] Effort: 1
  - [x] Done 2026-08-17: pg_ctl stop, rm -rf /data/restore-test (no sudo — all manta-owned), 682 GB free again; prod confirmed serving and archive health PASS immediately after.

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
  - [ ] Ready 2026-08-17: drill passed (7.3/7.4) and the offsite copy is checksum-verified (6.5) — both preconditions met. The clone is /data/trading-db-backup, postgres-owned, deletion needs PM sudo; awaiting PM go-ahead.

---

## Section 8 — Point-in-time recovery proof

Distinct from Section 7. Section 7 proves the base backup restores; this proves
**WAL replay works**, which is the only thing that turns "segments are being
copied" into "recovery to an arbitrary point exists."

- [x] **8.1 Plant a timestamped sentinel on prod**
  - [x] Create a scratch table on prod and insert a sentinel row; note the
        timestamp precisely (before and after)
  - [x] Keep it trivially reversible and clearly named as scratch — this is a
        deliberate write to production for test purposes
  - [x] Success: sentinel exists with known before/after timestamps recorded
  - [x] Effort: 1
  - [x] Done 2026-08-17: pitr_sentinel_915 created and one row inserted as trading_migrate; commit 21:40:57.36-06, bracketed T_BEFORE 21:40:55.348 (LSN 11A6/9D143570) and T_AFTER 21:40:59.362 (LSN 11A6/9D170130); pg_switch_wal immediately after — segment 00000001000011A60000009D archived at 21:41:00, failed_count 0.

- [x] **8.2 Recover to before the sentinel and confirm absence**
  - [x] Restore a copy with `recovery_target_time` set **before** the sentinel
        insert
  - [x] Confirm the scratch row is **absent**
  - [x] Success: the row is absent — proving replay stopped where instructed
        rather than replaying everything
  - [x] Effort: 3
  - [x] Done 2026-08-18: after the PM's reload-only archive_command fix (chmod 644 on each archived segment; ACL masking made the directory default-ACL approach fail) and max_worker_processes=64 in the drill config (archive recovery refuses to start below the primary's 51 — crash recovery never checks this), the restore replayed from the 20260817 backup through the production archive with recovery_target_time = T_BEFORE (21:40:55.348-06). Log: 'recovery stopping before commit of transaction 70691056, time 03:40:57.357+00' — the sentinel's own commit; last completed transaction 03:40:55.05+00. to_regclass('pitr_sentinel_915') IS NULL = true; cluster promoted. Replay provably traversed ~17 minutes of archived WAL past backup-end to find that boundary.

- [x] **8.3 Recover to after the sentinel and confirm presence**
  - [x] Restore with `recovery_target_time` **after** the insert
  - [x] Confirm the scratch row is **present**
  - [x] Both directions are required: absence alone is also what a failed replay
        looks like. Presence in the second run is what distinguishes working
        PITR from broken recovery (success criterion 7)
  - [x] Success: present in the after-restore, absent in the before-restore
  - [x] Effort: 2
  - [x] Done 2026-08-18: second full restore with recovery_target_time = T_AFTER (21:40:59.362-06): sentinel_present=true, row (1, 03:40:57.359+00) is the last completed transaction, replay stopped before the next commit at 03:41:30+00. Present-after + absent-before together satisfy success criterion 7. Full cycle cost per direction: ~14 m extract + ~1 m replay.

- [x] **8.4 Drop the scratch table from prod**
  - [x] Remove the sentinel scratch table under the maintenance credential
  - [x] Success: prod schema is back to its pre-drill state
  - [x] Effort: 1
  - [x] Done 2026-08-18: DROP TABLE pitr_sentinel_915 as trading_migrate, to_regclass confirms gone; drill cluster torn down, 759 GB free on /data, prod schema back to pre-drill state.

---

## Section 9 — Scheduling and runbook

- [ ] **9.1 Schedule the two tiers via cron**
  - [x] Metadata tier nightly; base-backup tier on the infrequent cadence
        settled against 3.4's measured duration and 4.5's retention (D4)
  - [x] Use `cron`, not a systemd timer — the host has no process manager and no
        units installed (D4, confirmed by the production-deploy runbook). Note
        in a comment that this migrates to a timer if the `/opt` + systemd
        deployment lands
  - [x] Ensure cron's environment is sufficient: cron does not load the
        operator's shell profile, so absolute paths and explicitly-set variables
        are required. Verify by observing an actual scheduled run, not by
        reading the crontab
  - [ ] Ensure the base-backup schedule does not collide with acquisition peaks
        or whatever re-invokes the acquisition passes (1.6)
  - [ ] In progress 2026-08-18: installed in the manta crontab (existing @reboot entry preserved) — half-hourly archive_health_cron, nightly 02:00 cron_nightly_metadata (dump + offsite checksum sync), weekly Sun 03:00 cron_weekly_base (backup + verify + offsite + retention prune, guarded by the health flag), plus a TEMPORARY one-shot base entry 2026-08-19 03:00 so the scheduled-base-backup observation lands tonight instead of next Sunday (remove after it runs). Comment in crontab notes migration to systemd timers if the /opt deployment lands. Nothing re-invokes acquisition (measured 1.6), so no collision. Remaining: observe the real scheduled runs tonight.
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

- [x] **9.3 Write the backup-and-restore runbook**
  - [x] Create `project-documents/user/runbooks/backup-and-restore.md`, alongside
        `cagg-maintenance-pausing.md` and `coverage-cagg-rebuild.md`
  - [x] Include the **real commands as executed** and the **observed timings**
        from 3.4, 6.5, 7.2, and 8.2 — not idealized ones (success criterion 9)
  - [x] Cover all four recovery scenarios from the LLD's recovery-path table:
        metadata-only restore, whole-cluster restore, PITR to a chosen time, and
        the degraded base-backup-only case
  - [x] Follow the coverage-cagg-rebuild runbook's structure: numbered steps
        with explicit credentials per step. Note which steps need the maintenance
        credential and which need the `postgres` OS user
  - [x] Include the archive-failure response: what the alarm looks like, and what
        to do when `pg_wal` is growing
  - [x] Record the credential-extraction idiom used by the existing runbooks
        (grep from `.env`, never `source` — the `$`-in-password trap)
  - [x] Success: someone following the runbook under pressure, without this
        context, can restore. That is the test — not whether it is complete on
        its own terms
  - [x] Effort: 3
  - [x] Done 2026-08-18 (iteratively since 20260816): project-documents/user/runbooks/backup-and-restore.md — as-executed commands with observed timings for every step (backup 2h03m–2h09m, verify 15m, offsite up 4h44m–5h06m / down 2h05m, extract 13m47s, PITR replay ~1m), all four recovery scenarios (metadata-only, whole-cluster, PITR section tested both directions, from-B2 degraded path with measured download), archive-failure response with the demonstrated alarm and self-recovery, credential-extraction idiom (grep from .env, never source), per-step credentials, and the hard-won gotchas (localhost-only replication, PG17 pg_verifybackup plain-format-only, ACL masking vs archive_command chmod, max_worker_processes floor for archive recovery, rclone ≥1.75 for large-object download).

- [x] **9.4 Record the drill result and set a repeat expectation**
  - [x] Write the drill's date, duration, and outcome into the runbook
  - [x] State when it should next be repeated. The realistic failure mode of
        backup work is that it is verified once and rots (LLD risks)
  - [x] Success: the runbook says when this was last proven and when it is due
        again
  - [x] Effort: 1
  - [x] Done 2026-08-18: runbook 'Drill record' table lists each proven capability with date, duration, and outcome (restore drill 2026-08-17, PITR both directions 2026-08-18, offsite round trip, alarm fire + self-recovery). Repeat expectation stated: re-run the drill and one PITR direction quarterly or after any PG/TS major upgrade, next due 2026-11-17.

---

## Section 10 — Completion

- [x] **10.1 Full validation pass**
  - [x] Run unit and integration tiers separately via `scripts/run_tests.py`
  - [x] `ruff check` and `ruff format --check` clean on slice-touched files
  - [x] Both static prod-URL ratchet guards still pass — Section 2's tests are
        the ones that could regress them
  - [x] Success: tiers green apart from documented pre-existing failures
  - [x] Effort: 2
  - [x] Done 2026-08-18: tiers run separately via `scripts/run_tests.py`. Unit
        tier: 1991 passed, 45 skipped, 0 failed — 29 slice-specific unit tests
        all pass (16 in test_backup_scripts.py, 4 in test_offsite_sync.py, 8 in
        test_backup_cron_glue.py, 1 unit ratchet guard). Integration tier: 171
        passed, 144 skipped, 2 failed — exactly the two documented pre-existing
        failures in test_cli_lists.py (hard-coded `priority1` named list absent
        from config/symbol-lists.yaml; same two slice 913 recorded; the four
        news-subsystem failures it listed were deleted by slice 914). Both static
        prod-URL ratchet guards pass (test/unit/test_unit_prod_url_guard.py and
        test/integration/test_integration_prod_url_guard.py). ruff check and
        ruff format clean on all four slice-touched Python files (one file,
        test/unit/test_backup_cron_glue.py, needed reformatting, fixed in
        commit 5f39b4a). Real regression found and fixed: unguarded `ALTER ROLE
        ... REPLICATION` in scripts/provision_roles.sql (commit 3b0b881) aborted
        artifact for any executor lacking REPLICATION attribute, erroring all 30
        tests in test/integration/data/test_role_privileges.py. Production
        shares this cluster; granting REPLICATION to test admin would let test
        credential stream production WAL, undoing slice 913. Fixed opt-in behind
        `-v with_replication=1` (matching `with_test_admin` idiom); runbook Step
        1 updated; all 30 role-privilege tests now pass; production unchanged
        (`trading_migrate` still `rolreplication=t`, `rolsuper=f`). Known
        instability (not a slice defect): across three full integration runs,
        one or two tests rotate between passing and erroring with
        `psycopg.errors.InternalError_: tuple concurrently deleted` during DDL
        (e.g., `DROP MATERIALIZED VIEW IF EXISTS minute_coverage`). Different
        test hit it on each run (test_health_and_gap_columns_unaffected_by_source_swap,
        then test_daily_timestamps_exact_not_merely_same_date, plus one in
        test_migration_050). All pass in isolation — 16 passed when affected
        files run alone. Root cause: PostgreSQL catalog race from ephemeral test
        databases on same cluster as production and its TimescaleDB background
        workers; visibility increased once 30 role-privilege tests executed DDL
        instead of erroring at setup. Recommend folding into slice 907's
        pre-existing-failure baseline quarantine.

- [x] **10.2 Verify every success criterion has evidence**
  - [x] Walk the LLD's nine success criteria and record which task produced the
        evidence for each. Criteria 2, 6, and 7 require **demonstrated**
        behavior — an alarm that fired, a drill that ran, a PITR in both
        directions — not implemented tooling
  - [x] Success: each of the nine maps to a concrete recorded observation
  - [x] Effort: 2
  - [x] Done 2026-08-18: All nine LLD criteria mapped to evidence: (1)
        archive_mode=on via 4.3–4.4; (2) archive-failure monitoring firing via
        5.3 (dated attended observation 2026-08-18); (3) base backup LIVE +
        pg_verifybackup via 3.4 + addendum; (4) metadata tier derived via 2.1,
        2.3–2.4; (5) offsite checksum verified via 6.4–6.5; (6) restore drill
        with parity via 7.2–7.4 (dated attended observation 2026-08-17); (7)
        PITR demonstrated via 8.1–8.4 (dated attended observation 2026-08-18);
        (8) wrapper isolation via 3.3; (9) runbook with measured timings via
        9.3. Criteria 2, 6, 7 verified by attended drill observations, not
        tooling alone.

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
