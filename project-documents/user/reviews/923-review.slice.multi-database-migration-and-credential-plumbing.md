---
docType: review
layer: project
reviewType: slice
slice: multi-database-migration-and-credential-plumbing
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/923-slice.multi-database-migration-and-credential-plumbing.md
aiModel: x-ai/grok-4.7
status: complete
dateCreated: 20260927
dateUpdated: 20260927
reviewedSha: d513c3bf8ef1a6ba266321016c6a958dd27450b4
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 17
findings:
  - id: F001
    severity: concern
    category: integration
    summary: "`_create_timescale_db` still defaults to the primary URL"
    location: "project-documents/user/slices/923-slice.multi-database-migration-and-credential-plumbing.md#technical-scope"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Tick connection failures have no handling strategy"
    location: "project-documents/user/slices/923-slice.multi-database-migration-and-credential-plumbing.md#error-handling"
  - id: F003
    severity: pass
    category: scope
    summary: "Scope and dependency direction match the assigned foundation slice"
    location: "project-documents/user/slices/923-slice.multi-database-migration-and-credential-plumbing.md#excluded"
---

# Review: slice — slice 923

**Verdict:** CONCERNS
**Model:** x-ai/grok-4.7

## Findings

### [CONCERN] `_create_timescale_db` still defaults to the primary URL

The slice plan requires `_create_timescale_db` to take its URL from the track rather than the context. The design routes `migrate apply`/`status` and `init --database` through `resolve_database_url`, and says `_get_maintenance_url`'s six callers stay primary-only. It never names `_create_timescale_db`. That helper still defaults to `settings.timescale_db_url` when `conninfo` is omitted, and `migrate status` calls it that way today. A track-routed status path that resolves the tick application URL but then opens the pool through the unchanged helper still queries the primary ledger. State the factory's contract: every track-routed command passes the resolved URL, and the no-`conninfo` default remains only for primary read paths.

### [CONCERN] Tick connection failures have no handling strategy

Error Handling covers unset credentials and `LedgerMisrouteError`, both raised before a connection. It does not cover the new I/O path: opening `TimescaleMinuteDataDB` (or the status pool) against `MT_TICK_MAINTENANCE_URL` / `MT_TICK_DB_URL` when the peer is down, the connect hangs, or the peer drops during the guard `SELECT`. Those are distinct from "variable unset", and a second host is one of the three placements this slice claims to support. Name the failure (connect error, timeout, disconnect mid-guard), the exit code, and that no DDL runs. Do not leave it as an uncaught psycopg error. No parent NFR with a numeric target applies to this migration path, so none is missing here.

### [PASS] Scope and dependency direction match the assigned foundation slice

Tick tables, Timescale extension DDL, write-surface grants, second serving pools, and tick backup are excluded and left to 222, 223, and 227. The resolver sits under `market/schema`, not the CLI, which is the direction 223's consumers need. The misroute guard and the no-fallback rule implement 220's hard gate that tick migrations must not land in `trading`. The `parent` frontmatter points at the slice plan, which is the expected reference.

### Run Digest

- Response length: 2864 chars
- Response is newline-free: no
- Tool calls made: 17
- Tool calls failed: 0
- Stop reason: stop
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 3
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 3
- Finding-shaped matches — surviving validation: 3
