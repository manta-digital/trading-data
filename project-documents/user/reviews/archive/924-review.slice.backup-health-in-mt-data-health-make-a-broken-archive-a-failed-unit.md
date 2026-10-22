---
docType: review
layer: project
reviewType: slice
slice: backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md
aiModel: xiaomi/mimo-v2.6-pro
aiProfile: openrouter
status: complete
dateCreated: 20261022
dateUpdated: 20261022
reviewedSha: 6b8cb9cf2e74edaf0bc8c12768a22098159b1ca6
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 24
turns: 14
promptTokens: 492681
cachedTokens: 433920
completionTokens: 19924
reasoningTokens: 16832
durationSeconds: 544.4
costUsd: 0.044631
costBasis: alias_pricing
runId: run-20261022-p4-82fbb102
squadronVersion: 0.23.8
findings:
  - id: F001
    severity: concern
    category: design-alignment
    summary: "`env_set_key.sh` duplicates the existing `env_add_keys.sh` atomic-write helper"
    location: "project-documents/user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md:72"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Failure behavior of the new env-file write path is implicit"
    location: "project-documents/user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md:218-224"
  - id: F003
    severity: note
    category: design-alignment
    summary: "D5's \"the same rule the table applies\" overstates the setting's validation rule"
    location: "project-documents/user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md:237"
  - id: F004
    severity: note
    category: design-alignment
    summary: "D4's deviation from the slice plan is justified and verified — reconcile the plan entry"
    location: "project-documents/user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md:209"
  - id: F005
    severity: note
    category: error-handling
    summary: "Detail rendering when `detected:` is absent is unspecified"
    location: "project-documents/user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md:188-195"
  - id: F006
    severity: pass
    category: design-alignment
    summary: "Correct maintenance-band alignment and honored upstream contracts"
    location: "project-documents/user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md"
---

# Review: slice — slice 924

**Verdict:** CONCERNS
**Model:** xiaomi/mimo-v2.6-pro

## Findings

### [CONCERN] `env_set_key.sh` duplicates the existing `env_add_keys.sh` atomic-write helper

The repository already has `deploy/lib/env_add_keys.sh` (slice 226) implementing exactly the mechanics D4 specifies for the new helper (lines 218–224): content written to a mode-0600 temp file in the same directory, owner set, `mv` over the target so a failure part-way leaves the old file whole, anchored `^KEY=` matching, values never printed. The proposed `env_set_key` reimplements that core with replace-or-append semantics and `--reference` instead of an explicit owner argument — two subtly divergent, security-sensitive writers for one file (`/etc/manta-trading.env`), which violates the project's "Do not duplicate logic" rule. Neither the "Included" list nor D4's "Considered and rejected" (line 225) mentions the existing helper or why it isn't extended. Lighter option: give `env_add_keys.sh` a set-or-append mode (or extract one shared write core) and drop the new file; the shell test at line 350 would also largely overlap the existing helper's coverage. Note this also interacts with line 308 ("Provides to Other Slices: `env_set_key`, for any later managed key") — publishing a second helper makes the duplication a permanent surface.

### [CONCERN] Failure behavior of the new env-file write path is implicit

Each new I/O path needs its failure modes enumerated with explicit handling. For the write to `/etc/manta-trading.env` the design gives the success contract (atomic, owner/mode preserved, `unchanged`/`set`, never prints the value) and one abort case ("a malformed table stops the script before the environment file is touched"), but says nothing about `env_set_key` itself failing (mv failure, unwritable `/etc`, disk full) or where it runs in step 4 relative to the existing chown/chmod re-assert. Verified: `deploy/install-production.sh` runs `set -euo pipefail` with a documented "fix the cause, re-run" recovery, so the likely behavior is safe — abort with the env file whole, and a key that never lands surfaces as the explicit `FAIL backup` row (SC 5). But that handling lives in the host script's ambient contract, not in the slice. One sentence stating it (env_set_key failure aborts the install; env file unchanged; re-run heals) closes the gap.

### [NOTE] D5's "the same rule the table applies" overstates the setting's validation rule

Verified in `deploy/lib/backup_clusters.sh`: the table's root rule is absolute path **under `/data/`** (`BC_ROOT_PREFIX`) with no `..` segment — stricter than D5's "absolute path with no `..` segment, the same rule the table applies." The looser rule is deliberate and needed: SC 5's negative cases name only relative paths and `..`, and the verification walkthrough (line 381) uses a `mktemp -d /tmp/…` scratch root the table would reject. A literal implementer copying the table's rule would break the walkthrough and the writer-to-reader test. Tighten the clause to say which parts of the table rule apply.

### [NOTE] D4's deviation from the slice plan is justified and verified — reconcile the plan entry

The plan entry (900-slices.foundation-cleanup.md:78) names `setup-backup.sh` as the writer of `MT_BACKUP_ROOTS`; D4 moves it to `install-production.sh`. The reasons check out: `setup-backup.sh` takes an operator-supplied `--env-file` holding backup credentials (it reads `MT_BACKUP_S3_BUCKET` from it) and is absent from `deploy/sudoers.d/manta-ops`, while `install-production.sh` is NOPASSWD under `MT_INSTALL` and owns `/etc/manta-trading.env`. Scope is not dropped — the setting is still derived from the cluster table at the pinned ref. This is a how-change, not a scope change, but the plan entry should be updated so the two documents agree.

### [NOTE] Detail rendering when `detected:` is absent is unspecified

The writer (`scripts/backup_health_cron.sh` `set_flag`) rewrites a flag with a plain `{ … } > "$path"` truncate-and-rewrite, so a reader concurrent with the writer can observe a partially written flag. D2 covers the verdict (flag presence fails; a flag with no `FAIL ` line falls back to its first non-empty line) but the detail template at line 188 — `<FLAG> since <detected>: …` — has no specified rendering when the `detected:` line is missing or yet unwritten. Fail-closed is already guaranteed, so this is cosmetic; one clause (e.g. omit "since …", or print "since ?") would complete the torn-read handling.

### [PASS] Correct maintenance-band alignment and honored upstream contracts

Verified against the parent architecture and the repo: the slice is corrective, not additive (a flag file nobody is obliged to read — the same silent-failure class slice 919 removed), and it honors rather than rewrites the originating contracts — 920's writer is explicitly untouched (Excluded, read-only consume), 227's table is consumed through `deploy/lib/backup_clusters.sh`'s parser, 919/234's `HealthCheck`/`CheckSeverity`/`_ago`/gather-before-connect framework is reused via the same deferred-import pattern (`health.py:284` mirrors the plan). Explicit-failure holds at every edge (unset setting, malformed entry, unreadable root, dead checker all fail loudly; the only `OSError` swallow is converted into a failing detail with a stated justification). The no-validator `Settings` field (D5) is a reasoned deviation from the architecture's instantiate-time validation that still satisfies "explicit failure" — a bad value fails one named check rather than every command. Constants centralize all names/separators/thresholds per "no magic strings"; the check surfaces through `mt data health`, so the CLI remains the verification surface. No NFR in the parent architecture applies to this once-hourly file-read path. Scope is proportionate (one module, one setting, one shell helper, disciplined exclusions; effort 2/5).

### Run Digest

- Response length: 7687 chars
- Response is newline-free: no
- Tool calls made: 24
- Tool calls failed: 2
- Stop reason: stop
- Output budget: 131072 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 69593
- Effort: backend default
- Turns: 14
- Tokens — prompt / cached / cache write / completion / reasoning: 492681 / 433920 / not computed / 19924 / 16832
- Duration: 544.4 s
- Cost: $0.04 (alias_pricing)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
