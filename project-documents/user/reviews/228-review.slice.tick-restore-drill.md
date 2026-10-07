---
docType: review
layer: project
reviewType: slice
slice: tick-restore-drill
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/228-slice.tick-restore-drill.md
aiModel: claude-opus-5-5
status: complete
dateCreated: 20261006
dateUpdated: 20261006
reviewedSha: 3f2f3c829306112f36da1b33da7c6500e92a938c
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 5
durationSeconds: 93.0
squadronVersion: 0.18.4
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "The restored archive and the restored database come from different points in time"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:106-107"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Cleanup can't remove root-owned files left by a partial restore"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:107"
  - id: F003
    severity: concern
    category: dependencies
    summary: "The role and privileges on the production tick cluster aren't specified"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:105-106"
  - id: F004
    severity: concern
    category: architecture
    summary: "The rebuild's production reads are read-only only by assumption"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:57, 133"
  - id: F005
    severity: note
    category: architecture
    summary: "The socket-URL exception to the TCP-by-host-name rule is stated and checked"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:133"
  - id: F006
    severity: note
    category: error-handling
    summary: "Two waits are missing from TD6's bounds table"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:184-198"
  - id: F007
    severity: note
    category: testing
    summary: "The primary restore checks only row counts for bookkeeping tables"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:121-123"
  - id: F008
    severity: pass
    category: scope
    summary: "The slice delivers what the architecture asks of the restore drill"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md#technical-scope"
  - id: F009
    severity: pass
    category: architecture
    summary: "The rebuild comparison uses the architecture's definition of \"the same\""
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:144-172"
  - id: F010
    severity: pass
    category: error-handling
    summary: "Isolation, cleanup and failure handling"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:110-119, 174-182"
  - id: F011
    severity: pass
    category: architecture
    summary: "NFRs and the realtime-paths check"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md#realtime-paths"
---

# Review: slice — slice 228

**Verdict:** CONCERNS
**Model:** claude-opus-5-5

## Findings

### [CONCERN] The restored archive and the restored database come from different points in time

Step 2 brings the database restore up to *now* by switching the WAL under the advisory lock. Step 3 restores `restic … latest`, which is the last nightly snapshot. If any tick run (adopt, pass or ingest) finished between that snapshot and the drill, two checks fail even though nothing is wrong with the backups:
- Step 3: the file count and bytes won't match the live archive.
- Step 7: the rebuild, made from the older archive, won't match the current restored database.

The lock protects the drill window, not the gap before it. Step 3 would also report this as an *archive restore* failure, which is misleading.

Fix: add an explicit precondition in step 0 or step 1, inside the lock. Refuse with a named error ("archive changed since snapshot <time>; run the restic backup first") when:
- any file in `/data/tick-archive` is newer than the latest snapshot's time, or
- the file count or bytes already differ from the snapshot.

The other option is to take a fresh archive snapshot under the lock. Either way, write the rule into runbook 200's drill procedure.

### [CONCERN] Cleanup can't remove root-owned files left by a partial restore

Step 3 runs `sudo restic restore` and then `sudo -n chown`. If either fails after restic has written files, the drill directory holds root-owned files. That can happen four ways:
- the 30-minute bound kills restic;
- restic itself errors;
- `sudo -n chown` fails because the sudo timestamp expired during a long restore;
- the run is killed between the two commands.

Both step 8 ("remove the drill directory", run as manta) and TD5's leftover sweep would then fail, and every later drill would fail at step 0. TD5 only covers a stopped scratch server, not root-owned contents.

Fix: state how cleanup and the leftover sweep handle this. For example, a `sudo -n chown -R manta:manta` or `sudo -n rm -rf`, limited to a marked `228-drill-*` path (the same path check step 3 already does), with a named error if sudo isn't available. Add a unit test for the case.

### [CONCERN] The role and privileges on the production tick cluster aren't specified

Steps 1, 2 and 5 need privileges that aren't named anywhere:
- `pg_switch_wal()` (superuser or an explicit grant);
- `SELECT` on every public table, including all bookkeeping tables, for the counts;
- reads of the tick advisory lock and the server settings.

The design says "psql on 17/tick" but doesn't say which role, how it authenticates, or how it connects. That last point matters because the architecture requires TCP by host name for `17/tick` (arch line 156) and 913 defines least-privilege roles. The "Prerequisites" section only mentions the now-unused `trading_tick_drill` `pg_hba` entry. This is a hidden dependency.

Separately, "Nothing is … written to … either production cluster" (line 93) isn't quite true. `pg_switch_wal` forces a segment switch, which is archived to B2. It's harmless, but the claim should be stated precisely.

Fix: name the role and connection path, check its grants in step 1 with a named failure, and soften the "nothing written" wording.

### [CONCERN] The rebuild's production reads are read-only only by assumption

The drill's own production connections set `default_transaction_read_only=on`. The step 6 subprocesses (`mt data migrate apply`, `adopt`, `pass --estimate-only`, `ingest`) connect to `MT_TIMESCALE_DB_URL` from `.env` without that setting. The "Production safety" claim (line 293) for those commands rests only on line 57's statement that tick code never writes production.

That statement matches slice 225 TD7 (ingest does not extend the calendar). The architecture still says the opposite in "Session model": "it runs the same shared extension for the CME calendar when the unit's range reaches past the populated horizon" (arch line 149). The architecture's text is stale, and the slice's safety argument relies on behaviour the architecture doesn't describe.

Fix:
- Enforce it: set `PGOPTIONS='-c default_transaction_read_only=on'` in the step 6 subprocess environment, next to the `MT_TICK_*` overrides. A write attempt then fails by name instead of silently changing production.
- Ask the architecture owner to update "Session model" to match 225 TD7.

### [NOTE] The socket-URL exception to the TCP-by-host-name rule is stated and checked

The architecture says `MT_TICK_DB_URL` "connects over TCP by host name, never through a local socket". The slice names its socket URLs as a drill-only exception, keeps them in the subprocess environment only (never in `.env`), and checks them against settings validation and the migrate CLI in development step 2. That's acceptable. The architecture's 2026-10-04 revision entry could mention the exception so the two documents agree.

### [NOTE] Two waits are missing from TD6's bounds table

TD6 bounds every production wait. Two scratch-server waits aren't listed:
- step 5's reads on the restored database (the table only lists step 7 for the scratch server);
- `pg_ctl stop -m fast` during cleanup and the leftover sweep.

`pg_ctl` has its own `-t` default, but the bound should be stated next to the others so cleanup can't hang the drill.

### [NOTE] The primary restore checks only row counts for bookkeeping tables

Step 5 compares `tick_trade` by fingerprint, but the bookkeeping tables by row count only. The architecture calls bookkeeping the part that can't be rebuilt, so it matters most to the primary path. `pg_verifybackup` and the WAL CRCs make silent value corruption unlikely, so this is acceptable. A per-table `md5(string_agg(... ORDER BY pk))` on the bookkeeping tables would turn "restored" into "restored exactly" for little cost.

### [PASS] The slice delivers what the architecture asks of the restore drill

- Archive and database are both drilled.
- Both procedures go into runbook 200.
- The drill proves the policy 227 chose (full weight, rebuild as fallback).
- Rebuilding from the *restored* archive (TD2) directly proves the "files are the record" principle.

The exclusions (automating production's drill, scheduling, point-in-time recovery) stay inside the slice's boundaries. The claim that the restore step can serve the later hammerhead move doesn't add work.

### [PASS] The rebuild comparison uses the architecture's definition of "the same"

TD3 matches the architecture's "row-for-row the same projection (physical layout, including compression state, is not part of the claim)":
- it hashes every column except `unit_id`, with the column list taken from `storage_columns.py`;
- it orders rows by the natural key.

TD4 matches rows by natural key and lists the expected differences from the 224 design, so it respects the rule that bookkeeping can't be rebuilt. Both are defined once and pinned by tests.

### [PASS] Isolation, cleanup and failure handling

- Emptying and checking `postgresql.auto.conf` keeps the scratch server from archiving into production's WAL archive and on to B2.
- The scratch server is socket-only with `archive_mode=off`, and its TimescaleDB background workers are off.
- The production settings it needs are read from the server, not typed in.
- The drill takes its own `flock`, writes a marker before creating anything, and refuses to touch unmarked directories. This follows CLAUDE.md's rule for destructive actions.
- These failure cases each have an explicit outcome, not "TBD": lost lock connection, recovery stopping early, the server exiting, timeouts, and the production database or Databento being unreachable.

### [PASS] NFRs and the realtime-paths check

The architecture states no latency or throughput NFR for restore. The 226 rebuild cost (212.5 s) is cited and the drill records the rebuild time without gating on it, which is appropriate. The realtime-paths check required of every slice is present and correct: the drill changes nothing about the per-unit row id, supersession or ledger grain.

### Run Digest

- Response length: 9861 chars
- Response is newline-free: no
- Tool calls made: 5
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 93.0 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 11
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 11
- Finding-shaped matches — surviving validation: 11
