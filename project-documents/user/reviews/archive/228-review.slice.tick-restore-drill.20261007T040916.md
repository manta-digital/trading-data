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
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20261006
dateUpdated: 20261006
reviewedSha: fb756f395da07f7a5f70615282d6ce4a98aebc67
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 3
durationSeconds: 50.8
squadronVersion: 0.18.4
findings:
  - id: F001
    severity: concern
    category: architecture-alignment
    summary: "Missing mandatory \"realtime paths\" check"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md"
  - id: F002
    severity: concern
    category: under-specification
    summary: "The `tick_trade` fingerprint may include columns that legitimately differ between the restored and rebuilt databases"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:130-135"
  - id: F003
    severity: concern
    category: error-handling
    summary: "Failure modes for the I/O steps are incomplete"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:96-124"
  - id: F004
    severity: concern
    category: architectural-boundaries
    summary: "The rebuild writes into the production tick cluster and its WAL archive, which cuts against the isolation and backup-weight design"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:66-72, 149-157"
  - id: F005
    severity: concern
    category: integration
    summary: "Side effects on production run accounting are unaddressed"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:112-119, 92"
  - id: F006
    severity: concern
    category: nfr
    summary: "The rebuild-time expectation is vague, and the restated NFR has no target"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:219, 135"
  - id: F007
    severity: note
    category: completeness
    summary: "Fingerprint and bookkeeping coverage beyond `tick_trade`"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:137-147"
  - id: F008
    severity: note
    category: scope
    summary: "Scope placement of the runbook content"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:38-42"
  - id: F009
    severity: pass
    category: architecture-alignment
    summary: "Alignment with the architecture's backup and fallback policy"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md:15-24, 126-128"
---

# Review: slice — slice 228

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Missing mandatory "realtime paths" check

The architecture (Technical Considerations, "Realtime paths") says every 220 slice design carries a short check naming any choice that forecloses realtime Path A or Path B. This slice has no such section. The drill probably forecloses nothing, since it touches only the backup, restore and rebuild paths. The slice still needs a one-line statement saying so.

### [CONCERN] The `tick_trade` fingerprint may include columns that legitimately differ between the restored and rebuilt databases

TD3 hashes "the rows' text" in natural-key order and uses it for the restored-versus-rebuilt comparison. The architecture says every tick row carries its archive unit's integer id, which is "not part of the natural key". Adoption into a fresh database assigns new unit ids, so a rebuild can number them differently from production. TD4 already expects the supersession links to differ. If the hashed text includes `unit_id`, the fallback comparison fails on a correct rebuild. If it silently omits the column, that should be stated. The slice should name the hashed column list and say it excludes the unit id (and any other rebuild-variant column). It should also add a test that proves the exclusion.

### [CONCERN] Failure modes for the I/O steps are incomplete

Only the production-DB and Databento-unreachable case (line 172) is handled explicitly. Each new I/O path needs a stated strategy, and these are missing:
- **Recovery wait (step 4):** "Wait for recovery to finish" has no bound. A broken WAL chain or a missing segment would hang the drill. The other two waits need numbers too: the `pg_switch_wal` segment wait is "bounded" with no value, and no timeout is stated for restic restore or for adopt's API calls.
- **WAL switch (step 2):** `pg_switch_wal()` is a no-op when there has been no WAL activity since the last switch, so the segment the drill waits for may never appear. Say whether that is a pass, a retry or a failure.
- **Advisory lock (step 1):** The lock is a session lock. If the connection drops mid-run, the lock is released and production can change during the comparison. That produces a false mismatch, or worse, a false pass. State that the drill detects the loss and fails.
- **Crash or kill:** `finally` does not run on SIGKILL, OOM or a host reboot. TD5 recovers only a leftover database. A leftover scratch server or root-owned drill directory is not handled at startup. Handle it the same way: detect it by marker and clean it up, or refuse.
- **Root-owned files:** Restic restores run under `sudo`, so the restored files may be root-owned. The slice does not say how manta reads them for adopt or removes them in cleanup.

### [CONCERN] The rebuild writes into the production tick cluster and its WAL archive, which cuts against the isolation and backup-weight design

The component diagram creates `trading_tick_drill` on `17/tick`. Rebuilding about 27.7 M rows there loads, compresses and drops a full copy of the data inside the cluster whose WAL is archived and base-backed-up. The drill therefore inflates the tick WAL archive and can interact with the weekly base backup. It also adds I/O load on the cluster the architecture isolates ("a filling tick volume cannot fill production's root disk"). The slice never states this cost. Either rebuild into the scratch server, which already exists and is socket-only with `archive_mode=off`, or record why production's cluster is required and bound the effect. The "only on a database the process created" rule in TD5 is also stretched by dropping a leftover database on the strength of a comment alone. The slice should acknowledge that this is the Project Manager's designation, or tighten it.

### [CONCERN] Side effects on production run accounting are unaddressed

Step 6 runs `mt data tick pass` and `ingest` with only the tick URLs overridden. `MT_TIMESCALE_DB_URL` stays on production because the calendar needs it. The architecture says tick passes record `PassKind.TICK` rows in production `pass_runs` through the shared recorder. The drill would therefore write accounting rows for the drill's runs into production, which `mt data overview` and `mt-run status` read. State whether the drill suppresses the recorder, or accept the rows and document them. The statement "production `trading_tick` is only read" is also incomplete on this point, because production main is written.

### [CONCERN] The rebuild-time expectation is vague, and the restated NFR has no target

The architecture's ingest-throughput NFR requires ingest rates to be measured and to have a pass/fail. This slice reruns the rebuild path. It only says "of the order of 226's 212.5 s plus compression". It states no threshold, and nothing says whether the time is recorded only or gated. Say explicitly that it is recorded only, or give a target. The drill's hold time on the tick advisory lock (the whole run, including a roughly 4-minute rebuild and the fingerprint scans) should also be bounded or acknowledged. The slice says nothing is blocked today because tick runs are manual, which is acceptable for now.

### [NOTE] Fingerprint and bookkeeping coverage beyond `tick_trade`

TD4 lists differences only for `tick_request` and `tick_archive_unit`. "Everything else must be equal" implicitly covers definitions, the ingest ledger and the day-condition table. The slice does not confirm that a rebuild from adopt, `pass --estimate-only` and `ingest` repopulates definitions. The architecture says the acquisition pass projects definitions and ingest resolves against them. Confirm during implementation that the rebuilt definitions and ledger are populated and compared, and pin that with the column-name test the slice already plans.

### [NOTE] Scope placement of the runbook content

The architecture says the restore procedures belong in "the backup runbook". The slice puts them in runbook 200's tick section. That is consistent if runbook 200 is the backup runbook. Confirm this, since the architecture does not use the number 200 for a runbook.

### [PASS] Alignment with the architecture's backup and fallback policy

The drill proves both archive restore and database restore, and a rebuild from the restored archive acts as the fallback. That matches "Backup is part of done" and the 2026-10-04 revision-log entry. Rebuilding from the restic copy rather than the live archive (TD2) is a sound way to prove the archive backup alone is sufficient. The dependency direction (228 consumes 223, 224, 226 and 227) is correct, and the exclusions (no scheduling, no PITR, no automating production's drill) keep scope tight.

### Run Digest

- Response length: 8047 chars
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
- Duration: 50.8 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
