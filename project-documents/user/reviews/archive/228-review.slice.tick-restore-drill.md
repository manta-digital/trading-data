---
docType: review
layer: project
reviewType: slice
slice: tick-restore-drill
targetKind: slice
rulesSource: project
project: trading-data
verdict: FAIL
verdictSource: stated
sourceDocument: project-documents/user/slices/228-slice.tick-restore-drill.md
aiModel: claude-opus-5-5
status: complete
dateCreated: 20261006
dateUpdated: 20261006
reviewedSha: dbb5945ddfe32c11fa51dcb49d47c53bc0230f76
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 12
durationSeconds: 97.8
squadronVersion: 0.18.4
findings:
  - id: F001
    severity: fail
    category: production-safety
    summary: "Scratch server inherits `archive_mode=on` from `postgresql.auto.conf`"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:107-112"
  - id: F002
    severity: concern
    category: under-specification
    summary: "Archive recovery is under-specified: no `recovery.signal`, no matching settings"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:106-114"
  - id: F003
    severity: concern
    category: failure-modes
    summary: "Leftover clearing can destroy a concurrent drill run"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:168-170"
  - id: F004
    severity: concern
    category: failure-modes
    summary: "`sudo` and production reads have no stated bound"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:174-187"
  - id: F005
    severity: concern
    category: under-specification
    summary: "The bookkeeping natural key doesn't cover requests without a job id"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:151-160"
  - id: F006
    severity: note
    category: integration
    summary: "The claim of no production writes depends on the calendar horizon"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:57,93"
  - id: F007
    severity: note
    category: integration
    summary: "API key variable name differs from the architecture"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:58"
  - id: F008
    severity: note
    category: integration
    summary: "The rebuild uses socket URLs, unlike production's TCP-only rule"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:127"
  - id: F009
    severity: pass
    category: scope
    summary: "Scope and boundaries match the architecture"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md#technical-scope"
  - id: F010
    severity: pass
    category: alignment
    summary: "The fingerprint matches the architecture's projection claim"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md#td3-one-tick_trade-fingerprint"
  - id: F011
    severity: pass
    category: nfr
    summary: "NFRs"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:127,216"
---

# Review: slice — slice 228

**Verdict:** FAIL
**Model:** claude-opus-5-5

## Findings

### [FAIL] Scratch server inherits `archive_mode=on` from `postgresql.auto.conf`

Step 4 writes a new `postgresql.conf` and `pg_hba.conf` and depends on `archive_mode=off` for safety (also stated at line 279). But 227 applies the tick cluster's `archive_mode` and `archive_command` "through `pg_settings.sh`, exactly as for production" (227, line 199). Runbook 200, Step 6, says production's settings live in `postgresql.auto.conf`. The base backup carries that file, and it overrides `postgresql.conf`.

A restored tick tree started as-is would therefore run with `archive_mode=on` and an `archive_command` that writes into `/data/backup/17-tick/wal`. After recovery promotes, the scratch server's new timeline and its `.history` file would land in the live tick WAL archive, and the hourly push would copy them to B2. The runbook calls this out explicitly: "empty the restored `postgresql.auto.conf` first … must be empty before every start", and check it with `grep -c archive`.

The step must truncate `postgresql.auto.conf` and confirm no `archive` setting is left before `pg_ctl start`. The slice's claims that the scratch server "can't write into the tick WAL archive" and that "Production is only read" are false until it does.

### [CONCERN] Archive recovery is under-specified: no `recovery.signal`, no matching settings

The drill restores "to the end of the archived WAL" through `restore_command`. PostgreSQL only uses `restore_command` when `recovery.signal` is present. Without it, the server does crash recovery from the base backup's own `pg_wal` and stops at the end of the backup. Step 5 would then always fail on counts, and the cause would look like a WAL-chain gap when it isn't.

Runbook 200's PITR section also says archive recovery refuses to start below the primary's `max_worker_processes` and `max_locks_per_transaction`. Emptying `postgresql.auto.conf` (finding above) removes those values, so they have to be set in the scratch `postgresql.conf`. The slice should:
- list `recovery.signal`;
- name the settings that must be at least the primary's values;
- read those values from `17/tick` at step 1, not hard-code them.

Line 114 should also distinguish "recovery stopped early" (startup log, end LSN against the segment named in step 2) from a data mismatch. As written, both show up as a step-5 count failure, which is harder to diagnose.

### [CONCERN] Leftover clearing can destroy a concurrent drill run

Step 0 stops and removes every marked `228-drill-*` directory. The tick advisory lock isn't taken until step 1. The marker records a pid, but nothing says the pid is checked. If a second invocation starts while a first one is mid-rebuild (steps 6–9 run after the lock is released), it will stop the first run's scratch server and delete its directory.

Fix: remove a marked directory only when its pid is no longer alive, and refuse otherwise, naming the pid. Or take a drill-local lock, such as `flock` on `/data/restore-test`, before step 0. Add the live-pid case to the unit tests at line 230.

### [CONCERN] `sudo` and production reads have no stated bound

TD6 bounds four waits. Two failure modes are left out:
- **`sudo`:** the walkthrough says the drill "prompts once for `sudo`" (line 243), but the first `sudo` call is restic at step 3, after the production advisory lock is taken. An unattended prompt there blocks for an unbounded time while the lock is held. Validate credentials (`sudo -v`) at step 0, before the lock, and use `sudo -n` afterwards so an expired timestamp fails by name instead of hanging.
- **Step 5 reads on `17/tick`:** these are full ordered fingerprint scans (0.65 GiB today, growing) with no bound. The architecture's precedent for harness reads of production is "read only with a 5 s statement timeout" (arch, revision log 2026-10-03). A 5 s limit won't cover a fingerprint scan, but the slice should set an explicit `statement_timeout`, defined once in TD6, and say that the connections are read-only (`default_transaction_read_only`).

`pg_verifybackup`, base-backup extraction and the step-7 comparisons also have no bounds. They run only against local scratch data, so a bound is less critical there, but TD6 says "each wait or subprocess has a bound", and these are subprocesses too.

### [CONCERN] The bookkeeping natural key doesn't cover requests without a job id

TD4 keys `tick_request` by `provider_job_id` and units by `(provider_job_id, unit_date)`. The architecture writes the request row at *requested*, before the submit. Unknown-outcome submits whose job is not listed end up exhausted, still with no job id (arch, revision log 2026-09-28). Such rows have no natural key under TD4, and the expected-difference set covers only "paid jobs with no archived files".

Production unit states that a rebuild can't reproduce are also missing from the set. These include retention-expired failures, `FAILED_RETRYABLE`/`RETRY_EXHAUSTED` attempt counts, and `PROVIDER_HOLE` units. A rebuild has none of these; every adopted unit ends *ingested*. Say how rows without a job id are matched or excused, and add the state and attempt columns, or the rows that can't be rebuilt, to the expected set. If not, the drill will either fail on a correct rebuild or need ad-hoc exceptions later. TD4 leaves column names to implementation (line 162), which is acceptable; the key rule is a design decision and shouldn't be.

### [NOTE] The claim of no production writes depends on the calendar horizon

Lines 57 and 93 say no tick command writes to the production database. The architecture's session model says the ingest pass "runs the same shared extension for the CME calendar when the unit's range reaches past the populated horizon", and that extension is a write to `17/main`'s calendar tables. For today's 2024 archive it won't run. The statement should be qualified (no write "while the archive lies inside the populated calendar horizon"), or the drill should check the horizon before step 6.

### [NOTE] API key variable name differs from the architecture

The slice names `DATABENTO_API_KEY`. The architecture ("Secrets and spend guards") and the code (`providers/profiles.py` tests; `test_data_tick.py` checks for `MT_DATABENTO_API_KEY` in the output) use `MT_DATABENTO_API_KEY`. Use the project name so the runbook's drill section doesn't send operators to the wrong variable.

### [NOTE] The rebuild uses socket URLs, unlike production's TCP-only rule

`MT_TICK_DB_URL` and `MT_TICK_MAINTENANCE_URL` point at the scratch socket. The architecture requires TCP by host name for the production URL ("Keeping the move cheap from day one"). It's a deliberate drill-only override, set in the subprocess environment only, and that's fine. Two things should be said, though:
- Confirm that settings validation and the migrate CLI accept a socket-path URL.
- Record the drill's production reads (the lock, the WAL switch, the fingerprint scans) in the architecture's 2026-10-04 revision entry, the way 226's harness reads were recorded.

### [PASS] Scope and boundaries match the architecture

The slice delivers exactly the anticipated "Tick restore drill" item: both media restored, both procedures written into runbook 200. It excludes automating main's drill, scheduling and PITR targets, and so avoids scope creep. Dependencies run in the right direction: it consumes 227's cluster table and parser, 226's rebuild commands and 224's list of rebuild losses, and adds no new product edges.

### [PASS] The fingerprint matches the architecture's projection claim

There is one SQL definition. Its column list comes from `storage_columns.py`, and it leaves out `unit_id` with a stated reason, checking unit attribution through the ledger by natural key instead. Compression state is explicitly not compared, which follows "physical layout, including compression state, is not part of the claim". Fingerprinting one restored database against two references gives a clean separation between proving the primary restore and proving the fallback.

### [PASS] NFRs

The architecture sets no recovery-time target for this path, and 227 explicitly leaves recovery time for 228 to measure. The slice records the rebuild time and cites 226's 212.5 s, which matches. The architecture's ingest-throughput pass/fail applies to production passes, not to the drill. No NFR is missing.

### Run Digest

- Response length: 10395 chars
- Response is newline-free: no
- Tool calls made: 12
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 225
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 97.8 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 11
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 11
- Finding-shaped matches — surviving validation: 11
