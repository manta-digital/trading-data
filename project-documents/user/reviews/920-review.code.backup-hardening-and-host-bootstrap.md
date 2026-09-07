---
docType: review
layer: project
reviewType: code
slice: backup-hardening-and-host-bootstrap
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260907
dateUpdated: 20260907
reviewedSha: 65a9b531d99bd7e5cf4facd71fb35d35503767d9
findings:
  - id: F001
    severity: concern
    category: duplication
    summary: "Env-value grep helper duplicated, drifted safety"
    location: "scripts/cron_weekly_backup.sh:80"
  - id: F002
    severity: concern
    category: error-handling
    summary: "backup_prod.sh/prune failures bypass logger alert"
    location: "scripts/cron_weekly_backup.sh:1307"
  - id: F003
    severity: note
    category: documentation
    summary: "apply() doc comment says 3 params, takes 5"
    location: "deploy/setup-backup.sh:130"
  - id: F004
    severity: note
    category: correctness
    summary: "fail_names() mixes classes across flag messages"
    location: "scripts/backup_health_cron.sh:891"
  - id: F005
    severity: note
    category: simplification
    summary: "Untested hex-fallback branch for timeline arg"
    location: "scripts/wal_segment_name.py:47"
---

# Review: code — slice 920

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] Env-value grep helper duplicated, drifted safety

Four independent copies of the same `grep | sed | tr` env-file value extraction exist in this diff; two of them (`deploy/lib/restic_repo.sh`, `deploy/setup-backup.sh`) got a `head -1` + `=`-anchored hardening, but `scripts/backup_health_cron.sh:60` and `scripts/cron_weekly_backup.sh:80` kept the older, unanchored, unguarded form. A stray duplicate/prefix-matching env-file line would silently corrupt `$DB_URL` with an embedded newline in the latter two scripts. This is exactly the kind of "define once" duplication CLAUDE.md calls out.

### [CONCERN] backup_prod.sh/prune failures bypass logger alert

Every other failure branch in `cron_weekly_backup.sh` goes through `die()` (stderr + `logger -t manta-backup`), but the base-backup and prune calls are unwrapped and rely only on `set -e` propagation. A failure there produces no syslog signal — inconsistent with the script's own documented alerting contract — and surfaces only via staleness alarms up to 9 days later.

### [NOTE] apply() doc comment says 3 params, takes 5

The inline comment `# apply <item> <check-fn> <act-fn>` is stale; the function actually takes 5 positional args (item, check-fn, status-word, detail, act-fn), as every call site shows.

### [NOTE] fail_names() mixes classes across flag messages

`fail_names()` returns all FAIL names regardless of class, so a mixed archive+stale failure run makes both the `ARCHIVE-BROKEN raised` and `BACKUP-STALE raised` syslog lines list each other's failures too — untested by `TestGlueFlags`, which only stubs single-class failures.

### [NOTE] Untested hex-fallback branch for timeline arg

`segment_from_lsn`'s hex fallback for a non-decimal timeline has no real caller (all callers pass decimal `jq` output) and no test — unreachable, unverified complexity.
