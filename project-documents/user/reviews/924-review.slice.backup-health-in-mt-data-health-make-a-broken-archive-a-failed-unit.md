---
docType: review
layer: project
reviewType: slice
slice: backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: PASS
verdictSource: stated
sourceDocument: project-documents/user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md
aiModel: xiaomi/mimo-v2.6-pro
aiProfile: openrouter
status: complete
dateCreated: 20261022
dateUpdated: 20261022
reviewedSha: e966efd20eec4752d5cb9eeca54cb34a983320a7
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 13
turns: 7
promptTokens: 121412
cachedTokens: 101632
completionTokens: 9366
reasoningTokens: 7400
durationSeconds: 225.0
costUsd: 0.017159
costBasis: alias_pricing
runId: run-20261022-p4-82fbb102
squadronVersion: 0.23.8
findings:
  - id: F001
    severity: pass
    category: scope
    summary: "Scope matches the amended band slice plan entry exactly"
    location: "project-documents/user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md:63"
  - id: F002
    severity: pass
    category: error-handling
    summary: "Every new I/O path has explicit, non-silent failure handling"
    location: "project-documents/user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md:180"
  - id: F003
    severity: pass
    category: integration
    summary: "Prerequisite contracts are consumed, not rewritten"
    location: "project-documents/user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md:145"
  - id: F004
    severity: note
    category: config
    summary: "D5 deliberately sits at the edge of the Settings validation rule"
    location: "project-documents/user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md:226"
  - id: F005
    severity: note
    category: structure
    summary: "health.py ↔ health_backup.py import cycle handled by deferred import"
    location: "project-documents/user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md:100"
  - id: F006
    severity: note
    category: scope
    summary: "Additive edge of the maintenance-band rule is crossed but justified"
    location: "project-documents/user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md:32"
---

# Review: slice — slice 924

**Verdict:** PASS
**Model:** xiaomi/mimo-v2.6-pro

## Findings

### [PASS] Scope matches the amended band slice plan entry exactly

Every element of plan entry 25 (`900-slices.foundation-cleanup.md:78`) is delivered: per-root checks failing on `ARCHIVE-BROKEN`/`BACKUP-STALE` and on a quiet writer cron, `MT_BACKUP_ROOTS` written by `install-production.sh` from the cluster table, explicit failure on unset/unreadable root, pure verdict functions with unit tests on real flag contents, no new CLI command. D4's deviation (install script instead of `setup-backup.sh`) is not dropped scope — the plan entry already names `install-production.sh`, so the entry is amended as the design states. The added 234 prerequisite is a consumed pattern, not scope creep.

### [PASS] Every new I/O path has explicit, non-silent failure handling

All four new I/O paths are covered with named handling, no "TBD": flag reads and log stat (`Data Flow` step 4 — `OSError` captured into the observation as a failing detail, never propagates; absent log fails as "never run"); a present-but-truncated flag still fails on presence with degraded detail (D2); the env-file write (D4 — `die` on temp/chown/`mv` failure, atomic `mv` leaves the old file whole, install stops before units are copied and systemd reloaded); and the cluster-table read (D4 — `die` before the environment file is opened). Local filesystem reads have no peer-disconnect or timeout surface beyond `OSError`, which is the handled case. D3's quiet threshold is a named constant with the cron cadence cited, and an absent log fails rather than passing.

### [PASS] Prerequisite contracts are consumed, not rewritten

920's writer is read-only (Excluded section) with the D7 parity test pinning reader constants to the cron template — verified against the real template line. 919/234's framework is used as-is: `HealthCheck`, `CheckSeverity.VERDICT`, `_ago`, gather-before-connect ordering, unchanged `--json` shape so slice 922's `verdict_line` pickup and `pass_runs` keep working. 227's table is consumed through its existing `deploy/lib/backup_clusters.sh` parser. Dependency direction is correct throughout (reader depends on writer and framework, never the reverse).

### [NOTE] D5 deliberately sits at the edge of the Settings validation rule

The architecture states Settings values are "Validated at instantiation — missing required values raise immediately." D5 makes `backup_roots: str | None` with no validator and defers parsing to the check. The field is optional so nothing "required" is missing, the decision is documented with a sound rationale (a Settings error would break every `mt` command including acquisition), and the failure is explicit at the point of use (one failing `backup` check naming the problem) — consistent with "Explicit failure." Worth keeping the rationale comment in the code so a future contributor does not "fix" this into a validator.

### [NOTE] health.py ↔ health_backup.py import cycle handled by deferred import

`health_backup.py` imports `HealthCheck` from `health.py` while `health.py` defers its `gather_backup` import inside `data_health`. The design documents this and it mirrors the existing 919/234 `health_tick` pattern, so it is consistent with the codebase rather than a hidden dependency. A shared `HealthCheck` module would remove the cycle outright, but that is a refactor of 919's contract territory and out of scope for a 2/5 reader slice.

### [NOTE] Additive edge of the maintenance-band rule is crossed but justified

The architecture's "Corrective, not additive" constraint is at its most strained here: the slice adds a new setting, module, check row, and shell helper. It holds because the layer being extended — `mt data health`, the backup cron, `install-production.sh`/`env_add_keys.sh` — is itself 900-band deliverable (919/920/916), so this band is the owning initiative, and the band's own slice plan entry 25 assigns this exact scope. The framing as a closed silent-failure defect (a flag nobody must read) fits "misleading operator surface." No action required; noted so the boundary stays visible for future maintenance slices.

### Run Digest

- Response length: 6187 chars
- Response is newline-free: no
- Tool calls made: 13
- Tool calls failed: 1
- Stop reason: stop
- Output budget: 131072 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 31145
- Effort: backend default
- Turns: 7
- Tokens — prompt / cached / cache write / completion / reasoning: 121412 / 101632 / not computed / 9366 / 7400
- Duration: 225.0 s
- Cost: $0.02 (alias_pricing)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
