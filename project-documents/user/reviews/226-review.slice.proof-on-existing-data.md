---
docType: review
layer: project
reviewType: slice
slice: proof-on-existing-data
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/226-slice.proof-on-existing-data.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20261003
dateUpdated: 20261003
reviewedSha: 84bddf3b9a68c489d64dc3d212cfb5d535c4d3d1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 3
durationSeconds: 81.5
squadronVersion: 0.18.2
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "Memory-headroom guard trips against the slice's own stated baseline"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md:513-516, 857-862"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Disk-full on `/data` is not enumerated, and `/data` also holds the archive"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md:269-273, 325-333"
  - id: F003
    severity: concern
    category: dependency-direction
    summary: "The harness opens an unspecified production-database read path"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md:150-153, 483-496"
  - id: F004
    severity: concern
    category: security
    summary: "Committed provisioning log and `.env` handling lack a secrets-leak check"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md:258-262, 269-270, 696-705"
  - id: F005
    severity: note
    category: scope
    summary: "Space partitioning is decided without a measurement, as a deliberate narrowing of the architecture"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md:391-406"
  - id: F006
    severity: note
    category: scope
    summary: "Contention is measured against the Kalshi pass, not the minute pass"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md:105-111, 475-507"
  - id: F007
    severity: note
    category: scope
    summary: "Scope is broad for a proof slice"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md:63-101, 897-900"
  - id: F008
    severity: note
    category: error-handling
    summary: "Destructive-statement guard for `drop-proof` needs a connection design"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md:212-217, 298"
  - id: F009
    severity: pass
    category: nfr
    summary: "Restated NFRs carry specific targets"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md:339-349"
  - id: F010
    severity: pass
    category: architecture-alignment
    summary: "Boundaries, realtime check and failure-mode table follow the architecture"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md:229-333, 594-608"
---

# Review: slice — slice 226

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Memory-headroom guard trips against the slice's own stated baseline

The first guard row stops the contention loop if `CommitLimit − Committed_AS` is below 4 GiB. The Risk section gives the 2026-10-03 baseline as `Committed_AS` 62.7 GiB against a `CommitLimit` of 66.4 GiB, which is 3.7 GiB of headroom. The guard would therefore trip at the first sample, before any load. The tick cluster's 4 GB `shared_buffers` reduces the headroom further. The same section notes overcommit is heuristic (mode 0), so the limit is not enforced and the threshold does not map to an actual failure point. The architecture's isolation goal and its "two clusters cannot together oversubscribe the host" requirement depend on this guard being meaningful.
- Define the threshold relative to the pre-load baseline, for example a drop of N GiB from the step's own starting sample.
- Alternatively, rely on `MemAvailable` alone, which reflects real pressure.
- State what the step does when the baseline is already below the threshold, with an explicit exit.

### [CONCERN] Disk-full on `/data` is not enumerated, and `/data` also holds the archive

The failure-mode table covers provider, cluster-down and partial-completion paths. It does not cover `/data` filling during a step. The only disk control is the 100 GB free check at provisioning (line 270). `/data` hosts the tick archive, which the architecture calls "the record" and which is not yet backed up (227), as well as the tick cluster's data and WAL. The contention loop repeatedly truncates and reloads about 27.7 M rows, which is heavy WAL and bloat churn. Neither `max_wal_size` nor any free-space check inside the harness is specified.

A tick-cluster disk-full event could also hit the archive volume. That is the "filling tick volume" scenario the isolation goal constrains. Add:
- a free-space guard in the harness, alongside the contention guards;
- `max_wal_size` in the cluster settings block;
- a table row stating the handling: stop, report, and leave the archive untouched.

### [CONCERN] The harness opens an unspecified production-database read path

The contention step reads `pass_runs` and the production cluster's `pg_stat_database`, and the rebuild reads the CME calendar. The architecture enumerates four tick → production edges, all in product code, and none is a monitoring read of production statistics. The slice does not say which credential the harness uses. It also does not say whether a read-only role under the 913 least-privilege set suffices, or whether the harness may read `MT_TIMESCALE_DB_URL` at all. It also does not state how a production outage affects each step (exit code, partial report). Specify a read-only role and the failure behaviour. Mention the harness's production reads in the Revision Log entry so the architecture's edge list stays accurate.

### [CONCERN] Committed provisioning log and `.env` handling lack a secrets-leak check

The script generates passwords and writes URLs containing them. Success criterion 1 requires its log to be committed under `user/notes/`. The design says the script "never prints a password", but the unit test covers only the `.env` writer (adds absent keys only, mode 0600). Nothing verifies that the committed log and the notes copy contain no credential or URL with embedded password. Add a test or `--check` assertion that the log is scrubbed. Specify an atomic write (temp file, then rename) for `.env`, so a failure mid-write does not leave a truncated file.

### [NOTE] Space partitioning is decided without a measurement, as a deliberate narrowing of the architecture

The architecture says the physical-grouping class (space partitioning and compression layout) is "made once, from the proof's measurements". The slice rejects space partitioning on structural grounds (one data volume, chunk-count multiplication) and records the deviation with its reasoning. It also lists the architecture's Revision Log among the required docs (lines 709-722). The reasoning is sound and the deviation is visible. The note is that the amendment must actually land, and the `size` step's skew figure should still be reported as evidence.

### [NOTE] Contention is measured against the Kalshi pass, not the minute pass

The architecture's "Cross-source arbitration" and "Proof on existing data" entries name the minute pass. The slice substitutes the Kalshi pass on a PM decision (2026-10-03) and moves the minute overlap to 232. The slice records this in the Revision Log, the plan Notes and the 232 entry. It also refuses to set weights from the Kalshi-only numbers, which keeps the architecture's "no weights before measurement" rule intact. The Kalshi load profile (hourly, small writes) differs from the minute pass's 13,083-symbol walk, so 232 must not treat a "none measured" verdict here as a clearance.

### [NOTE] Scope is broad for a proof slice

The slice bundles cluster provisioning, a root script, a measurement harness, a migration, a 225 bug fix, a production rebuild, the `TICK_UNIVERSE` edit and several documents. Each item has a stated justification: the PM placed the cluster, no earlier slice creates it, and contention needs the production host. The slice also states a split point at lines 897-900. The bad-header fix and the cluster provisioning are infrastructure rather than proof. Hold the (a)/(b) split as the default if the task breakdown is large.

### [NOTE] Destructive-statement guard for `drop-proof` needs a connection design

The guard checks `current_database() = TICK_PROOF_DB_NAME`. `DROP DATABASE` cannot run from a connection to the database being dropped, so `drop-proof` must connect elsewhere and the `current_database()` check does not apply as written. State how the guard works for that one step, for example by matching the target name against the constant before issuing the drop. The project's destructive-statement rule is otherwise met: the PM-run script creates and thereby designates the proof database.

### [PASS] Restated NFRs carry specific targets

The architecture's ingest throughput pass/fail is restated with numbers. The slowest unit must be ≤ 120 s against 24 h of market time, and the 78-unit, 3.5-month set must be ≤ 2 h, which maps to the "month within an operator's working session" bound. Query latency (Q1–Q4 ≤ 1 s warm, planning ≤ 50 ms), the chunk-count range, and the contention bound (324 s Kalshi maximum) are likewise stated as decision rules rather than left as "TBD".

### [PASS] Boundaries, realtime check and failure-mode table follow the architecture

- The cluster placement matches the PM decision: second cluster, `/data`, TCP by host name, matching TimescaleDB version check, and production left untouched.
- The harness drives the shipped CLI and pass function rather than a parallel code path.
- The design keeps `service environment` wiring in 233, the backup policy in 227, and the minute-pass overlap in 232.
- Realtime Paths A and B are checked explicitly, and a Kalshi contract diff is a named task.
- The failure-mode table covers provider metadata, cluster restart, each re-ingest, `layouts`, `final` and the production rebuild, with a resumption strategy for each.
- Supersession and overlap on compressed chunks are re-verified before production data is loaded, as 225 handed over.

### Run Digest

- Response length: 8760 chars
- Response is newline-free: no
- Tool calls made: 3
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 81.5 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
