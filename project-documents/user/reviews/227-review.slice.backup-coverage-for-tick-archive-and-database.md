---
docType: review
layer: project
reviewType: slice
slice: backup-coverage-for-tick-archive-and-database
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/227-slice.backup-coverage-for-tick-archive-and-database.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20261004
dateUpdated: 20261004
reviewedSha: db54a867beed0c5b1be5505cfdd41363526698d9
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 4
durationSeconds: 58.1
squadronVersion: 0.18.4
findings:
  - id: F001
    severity: pass
    category: alignment
    summary: "Policy decision and tooling extension match the architecture's Backup coverage intent"
    location: "project-documents/user/slices/227-slice.backup-coverage-for-tick-archive-and-database.md#Technical Decisions"
  - id: F002
    severity: pass
    category: risk-management
    summary: "Production-protection mitigations are concrete and testable"
    location: "project-documents/user/slices/227-slice.backup-coverage-for-tick-archive-and-database.md#TD3 and #Migration Plan"
  - id: F003
    severity: concern
    category: error-handling
    summary: "Failure modes for the cutover and new I/O paths are not enumerated"
    location: "project-documents/user/slices/227-slice.backup-coverage-for-tick-archive-and-database.md:209-222"
  - id: F004
    severity: concern
    category: isolation
    summary: "Tick backups nest inside production's backup root, which weakens the isolation goal"
    location: "project-documents/user/slices/227-slice.backup-coverage-for-tick-archive-and-database.md:119-129, 273-281"
  - id: F005
    severity: concern
    category: specification-consistency
    summary: "Functional criteria and walkthrough do not match the designed cutover steps"
    location: "project-documents/user/slices/227-slice.backup-coverage-for-tick-archive-and-database.md:209-222 vs 301, 326, 347"
  - id: F006
    severity: concern
    category: scope-alignment
    summary: "Parent architecture is not updated for the 227/228 split and the TD1 rationale"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:69, 167, 195"
  - id: F007
    severity: note
    category: nfr
    summary: "No numeric RPO/RTO stated; the NFR restatement requirement is not triggered"
    location: "project-documents/user/slices/227-slice.backup-coverage-for-tick-archive-and-database.md#Success Criteria"
  - id: F008
    severity: note
    category: design
    summary: "Two parsers for one table, and tick-drill provisioning in this slice"
    location: "project-documents/user/slices/227-slice.backup-coverage-for-tick-archive-and-database.md:163, 55"
---

# Review: slice — slice 227

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Policy decision and tooling extension match the architecture's Backup coverage intent

The architecture says the database policy is chosen from the measured rebuild cost and that 915/920 is "extended … rather than duplicated by hand" (arch lines 69, 167). TD1 and TD2 do this. TD1 uses the 212.5 s cost from 226. TD2 makes the existing scripts per-cluster through one table. Reusing `pg_basebackup` also keeps the architecture's cheap-move-to-hammerhead path (arch line 154). Runbook placement, retention and exclusions (TD9, Scope item 7) satisfy the "recorded in the backup runbook" requirement (arch line 115).

### [PASS] Production-protection mitigations are concrete and testable

Production's layout is kept in place. A byte-identical render test, a `--check` drift stop, and before/after config SHA-256 checks protect it. A test asserts that no main-block line names `17-tick`. Dependency direction is sound: the table is read by setup, the cutover and 228, and the backup scripts gain no cluster knowledge.

### [CONCERN] Failure modes for the cutover and new I/O paths are not enumerated

TD8 lists happy-path steps and says "Exit 0 only when every check passed". It does not say what happens when a step fails or is interrupted, and the cases below are exactly where the review criteria expect explicit handling.
- **Re-run and resume:** it is not stated whether the script can be re-run safely after a partial failure, for example after step 4 installs the cron block but step 5, 6 or 7 fails. No rollback or "resume from step N" strategy is given.
- **Cron before archiving:** step 4 installs the tick cron block before step 5 turns on `archive_mode`, and the first base backup does not exist until step 7. The hourly WAL push and health check can fire in that window. The document does not say whether `ARCHIVE-BROKEN` or `BACKUP-STALE` can be raised spuriously, or whether the flags clear on their own.
- **Restart race:** step 1 checks that the tick advisory lock is free, but step 5 restarts much later. That is a time-of-check/time-of-use gap, and TD6 does not say the lock is re-checked or held across the restart.
- **Unbounded waits:** step 6's "bounded wait" for the `.zst` segment has no value or timeout behaviour.
- **Network and B2 errors:** nothing says what an `rclone` failure, a B2 outage, or a mid-push disconnect does in steps 7 and 8. It is also unclear whether the arm file is withheld in that case. TD7 only withholds it when the final check fails.
- **Interrupted base backup:** the document does not say whether a partial `pg_basebackup` directory is cleaned up or left to fail verification.
Add a short failure-mode table covering each new I/O step (WAL archive, hourly push, metadata dump, weekly base backup, B2 sync, restart, cutover), each with an explicit strategy: fail, retry, or flag.

### [CONCERN] Tick backups nest inside production's backup root, which weakens the isolation goal

The architecture forbids tick trouble reaching production (arch line 77) and says the cluster gets "its own backup configuration rather than sharing production's" (arch line 156). The tick root is `/data/backup/17-tick`, so tick WAL and base backups share the same volume as production's roughly 300 GB of live backups. Tick WAL arrives in bursts during ingest. It is pruned only weekly, and the size is described only as "bounded by one week of ingest" with no figure. A large future purchase (230–232, or the `ohlcv-1s` item in Excluded) could therefore fill `/data/backup` and break production's WAL archiving or base backups. Please add one of three things:
- a capacity estimate and headroom check;
- a free-space guard or alarm in the tick health check;
- a stated reason why sharing the volume is acceptable.
Without one, the "own backup configuration" requirement is only partly met.

### [CONCERN] Functional criteria and walkthrough do not match the designed cutover steps

- Success criterion 5 says the cutover runs the metadata dump once if it precedes the nightly firing. TD8's ten steps contain no metadata-dump step.
- Scope item 2 removes `--cluster` and moves per-cluster roots into the table. The verification commands (lines 326 and 347) still pass `--backup-root /data/backup`. It is unclear whether that flag survives and what it means for the tick row's root.
- TD10's B2 lifecycle rule may need a manual PM console step. That step is not among the success criteria or the walkthrough, and it is unclear whether it affects the cutover exit code.
These are small, but a reader implementing from this design would have to guess.

### [CONCERN] Parent architecture is not updated for the 227/228 split and the TD1 rationale

The slice's work is correct, but the architecture still describes one backup slice: "the restore drill covering both" (lines 69, 167, 195). The 2026-10-04 split is recorded only in the slice plan. The architecture's revision log has no matching entry. The log is normally how it is kept consistent with slice designs, as the 2026-09-28 to 2026-10-03 entries show.
TD1 also rests on a premise the architecture does not state. The manifest, ledger, supersession and spend records are not rebuildable from archive files. They need the production database and the Databento API. The architecture says "the database projection can be rebuilt from the archive" (line 69) and does not carve out that bookkeeping. Add a revision-log entry for the split, the TD1 policy, and this nuance.

### [NOTE] No numeric RPO/RTO stated; the NFR restatement requirement is not triggered

The architecture states no numeric backup NFR, so nothing has to be restated. The slice still gives no recovery-point objective, even though the design implies one: continuous local WAL plus an hourly offsite push. It also gives no recovery-time target beyond the 212.5 s rebuild figure. Stating both, even as observed values, would give 228's drill something to verify against.

### [NOTE] Two parsers for one table, and tick-drill provisioning in this slice

- **Parsers:** one bash parser and one Python parser read `backup-clusters.conf`. This risks drift, but both are tested against the real file as the project parsing rule requires, and the duplication is acknowledged.
- **Drill database:** adding `trading_tick_drill` to `pg_hba` here is 228's concern. It is a tiny change and justified in "Provides to Other Slices". It is a minor boundary stretch, not scope creep.

### Run Digest

- Response length: 7843 chars
- Response is newline-free: no
- Tool calls made: 4
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 58.1 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
