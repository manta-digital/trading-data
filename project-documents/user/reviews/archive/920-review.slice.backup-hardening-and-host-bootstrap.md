---
docType: review
layer: project
reviewType: slice
slice: backup-hardening-and-host-bootstrap
project: trading-data
verdict: FAIL
sourceDocument: project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260905
dateUpdated: 20260905
reviewedSha: da6980002cbbadd2db206ef1b071bcf2cb14f8aa
findings:
  - id: F001
    severity: fail
    category: correctness
    summary: "Weekly `rclone sync` of the WAL directory has no guard against an empty or unmounted source"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:227-231"
  - id: F002
    severity: concern
    category: error-handling
    summary: "The restic system-backup tier has no failure detection and no alarm path"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:327-331"
  - id: F003
    severity: concern
    category: coupling
    summary: "Routing `offsite_wal_stale` into `ARCHIVE-BROKEN` lets a B2 outage block the weekly base backup"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:266-270"
  - id: F004
    severity: concern
    category: error-handling
    summary: "The `prune_permission` canary is unnamed and its failure and interaction paths are unspecified"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:249"
  - id: F005
    severity: concern
    category: error-handling
    summary: "`sync_wal_offsite.sh` failure modes are named but not resolved: flock mode and hang handling"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:219-226"
  - id: F006
    severity: concern
    category: traceability
    summary: "The design amends \"916 ADR role 2,\" which does not exist in slice 916, and no deliverable updates it"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:110-116"
  - id: F007
    severity: concern
    category: hidden-dependency
    summary: "Frontmatter `interfaces: []` hides the dependency on slice 917's test cluster"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:7"
  - id: F008
    severity: note
    category: scope
    summary: "The maintenance band's \"corrective, not additive\" test is not applied to the slice's genuinely new capabilities"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:70-87"
  - id: F009
    severity: note
    category: architecture-principle
    summary: "No `mt` surface is delivered, and the rationale is given only for the health-check fold-in"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:88-99"
  - id: F010
    severity: note
    category: security
    summary: "The restic include-by-default rule sweeps credentials offsite without naming encryption as the control"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:332-348"
  - id: F011
    severity: note
    category: under-specification
    summary: "`--check` exit semantics for the step 8 warning are ambiguous"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:133-135"
  - id: F012
    severity: pass
    category: dependencies
    summary: "Dependency direction is correct for a maintenance-band slice"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:101-116"
  - id: F013
    severity: pass
    category: architecture-principle
    summary: "Explicit-failure and no-magic-strings principles are honored concretely"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md#technical-decisions"
---

# Review: slice — slice 920

**Verdict:** FAIL
**Model:** claude-opus-5

## Findings

### [FAIL] Weekly `rclone sync` of the WAL directory has no guard against an empty or unmounted source

D4 makes the weekly reconcile an `rclone sync` of `/data/backup/wal` to `b2:$BUCKET/wal`, so that local prune deletions propagate offsite. The Risks section (lines 574–576) defends this with "It deletes only what local retention already deleted" — that claim holds only when the source directory reflects reality. `rclone sync` mirrors the source unconditionally: if `/data` is unmounted, the archive directory is empty after an operator recovery step, or a `--wal-dir` argument is rendered wrong by `setup-backup.sh` step 5's path substitution (D7, lines 272–279), the Sunday 03:00 job deletes the entire offsite WAL chain. That chain is the primary deliverable of this slice and the sole input to success criterion 7's B2-sourced PITR. No `--max-delete` ceiling, no mountpoint or non-empty assertion, and no "refuse if local segment count dropped by more than the prune accounts for" check appears anywhere in D4, D11, or the walkthrough. D11's ordering argument ("the first offsite reconcile is the first destructive offsite action and runs only after the local chain has been drilled", lines 398–400) protects the *first* run only; every subsequent unattended run is unguarded. This also contradicts the architecture's "Explicit failure" principle (900-arch.foundation-cleanup.md:51) — a silently-empty source is exactly a default that masks a misconfiguration.

### [CONCERN] The restic system-backup tier has no failure detection and no alarm path

D9 adds a daily root cron job (`cron_system_backup.sh`) and a monthly `restic check --read-data-subset=5%`, both new I/O paths to B2. No failure mode is enumerated for either: a stale repository lock left by an interrupted run (restic's most common recurring failure — every subsequent backup then fails), a B2 credential or network failure, a missing `MT_BACKUP_RESTIC_PASSWORD`, or `forget --prune` dying mid-operation. Nothing states where the job's output goes or what makes a failure visible. The WAL tier gets four named alarms feeding `ARCHIVE-BROKEN` (D5/D6), but the restic tier gets none, and slice 919 established that this host has no mail transport, so cron stderr goes nowhere. Success criterion 12 (lines 466–469) proves the *first* snapshot and restore; nothing proves day 30. This is the same shape as the day-one ACL defect described in the Overview (lines 25–28): a scheduled job that fails silently because nothing is obliged to notice.

### [CONCERN] Routing `offsite_wal_stale` into `ARCHIVE-BROKEN` lets a B2 outage block the weekly base backup

D6 states that the four new failures feed the existing `ARCHIVE-BROKEN` flag, "so the existing alarm surface is reused rather than a second one added." Runbook 200 (line 362) confirms `cron_weekly_base.sh` **refuses to take a base backup while the flag exists**. Three of the four new checks are genuine archive-integrity conditions, but `offsite_wal_stale` (line 252) fires on a network, B2-auth, or uplink condition that says nothing about local archive health. A B2 outage lasting more than two hours across Sunday 03:00 therefore cancels the weekly base backup — degrading a healthy local tier because a remote tier is degraded, and with no signal distinguishing the two. The design does not analyse this consequence; it should either exclude `offsite_wal_stale` from the base-backup gate or state explicitly that blocking is the intended behavior and why.

### [CONCERN] The `prune_permission` canary is unnamed and its failure and interaction paths are unspecified

D5's canary creates and deletes a file inside `--wal-dir` every 30 minutes as the cron user. The file's name is never specified, and three consumers now read that same directory: `sync_wal_offsite.sh` (D4, excludes only `*.tmp`, so a canary older than `--min-age 2m` is uploaded to B2), `pg_archivecleanup` in the prune (D3), and `rclone check --one-way` (D4, which would then report a difference and fail success criterion 6). If the health check is killed between create and delete — the disk-full scenario this slice was written for — the leftover canary is permanent. `archive_tmp_leftover` (line 251) only matches `*.tmp` and will not catch it. Specify the canary name, put it under the `*.tmp` pattern or a dedicated excluded prefix, and state the cleanup-on-crash behavior.

### [CONCERN] `sync_wal_offsite.sh` failure modes are named but not resolved: flock mode and hang handling

D4 says the 15-minute push runs "under `flock` so overlapping runs cannot race" without saying whether the lock is non-blocking (`flock -n`, skip this run) or blocking. Blocking is the default reading and is wrong here: a hung rclone against an unresponsive B2 endpoint would queue one waiting process every 15 minutes indefinitely. There is also no timeout on the rclone invocation and no bandwidth bound, on a host whose incident record (Overview, lines 18–23) is a Kalshi drain that raised WAL from 8 GiB/day to ~100 GB/day — the ~40 minutes/day figure at line 225 is derived from the 16 GiB/day steady state, not from the drain rate the slice exists because of. The `offsite_wal_stale` alarm detects the *outcome* after two hours, which is detection, not handling. State the flock mode, an rclone timeout, and what the script does on repeated failure.

### [CONCERN] The design amends "916 ADR role 2," which does not exist in slice 916, and no deliverable updates it

"Interfaces Required" states that "916 ADR role 2 ('deploys') extended by one line to 'deploys and backups'." Slice 916's design contains no ADR section and no numbered roles — the string "ADR" does not appear in `916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md` at all. Separately, D7 (lines 272–279) explicitly overrides 916's decision that the user crontab is PM-owned host config. The architecture's maintenance-band rule requires that "the originating initiative's contracts are honored, not rewritten" (900-arch.foundation-cleanup.md:27); because 916 is a sibling in the same band, amending it is permissible, but the amendment must land somewhere. The Implementation Notes changed-files list (lines 587–592) updates runbooks 200 and `__readme.md` but no 916 artifact, so the design's justification for keeping the maintenance credential in the dev checkout rests on a citation a reader cannot resolve.

### [CONCERN] Frontmatter `interfaces: []` hides the dependency on slice 917's test cluster

`interfaces` is empty, yet D10's acceptance test part 2 (lines 372–379) requires a full provisioning run and one **PostgreSQL restart** on hammerhead — 917's dedicated test cluster — which "interrupts any integration run in flight." That is a cross-slice impact on the infrastructure 917 delivered and 918 is still debugging. The out-of-scope list (lines 92–94) also defers work to 919's `mt data health`. Sibling slice 919 populates `interfaces: [265, 267]` for exactly this kind of relationship. Declare 917 (and 919) so the coupling is visible to anyone scheduling test-cluster work.

### [NOTE] The maintenance band's "corrective, not additive" test is not applied to the slice's genuinely new capabilities

The architecture permits maintenance slices to touch any layer but constrains them to be "corrective, not additive — a maintenance slice fixes behavior that is already specified and already wrong" (900-arch.foundation-cleanup.md:26). Scope items 1–4 and 7 pass that test cleanly (ACL defect, wedge, missing prune, unreproducible host state). Scope item 5 does not: restic backup of `/home/manta`, `/root`, and `/etc` is new capability for state no initiative ever specified as backed up — the design itself says `/home/manta` has "no backup layer at all" (line 55), i.e. nothing specified is wrong. The same applies to timeshift management (D8). The 900 slice plan entry authorizes both, so this is not unauthorized scope, but the design should say in one line why host-OS state has no owning initiative and therefore lands here rather than reading as feature work under a maintenance label.

### [NOTE] No `mt` surface is delivered, and the rationale is given only for the health-check fold-in

The architecture states "CLI is the verification surface: if a feature can't be exercised through `mt`, it doesn't exist yet" (900-arch.foundation-cleanup.md:53). Every deliverable here is a shell script invoked by cron or an operator. The out-of-scope list gives a good, specific reason for not folding the archive health check into `mt data health` — the service account cannot read `/data/backup` under `ProtectSystem`, and the check needs the maintenance credential — and that reasoning generalizes to the whole tier, but the design never says so. One sentence stating that the backup tier is deliberately outside the `mt` surface, and why, would close the gap against the principle rather than leaving it implicit.

### [NOTE] The restic include-by-default rule sweeps credentials offsite without naming encryption as the control

D9's rule is "exclude what is derivable, keep what is not," with `/home/manta` included and only caches, Steam, Trash, venvs, and FUSE mounts excluded. That sends `~/.ssh`, the dev checkout's `.env` (holding `MT_TIMESCALE_MAINTENANCE_URL`, the four B2 keys, and provider API keys), and any browser credential store to B2 daily. restic encrypts at rest and the design mentions this once in passing as a tool property (line 317), but never as the control that makes the include-by-default rule safe. Given CLAUDE.md's credential rules and slice 913's least-privilege work, state it explicitly — and note that the repo password protecting these secrets is itself stored in the `.env` that is inside the backup.

### [NOTE] `--check` exit semantics for the step 8 warning are ambiguous

D1 defines `--check` as reporting `OK` / `DRIFT` / `MISSING` and exiting "non-zero on any drift," while step 8 (line 151) only *warns* when the legacy user-crontab lines remain. Success criterion 1 requires `--check` to exit 0 with every item `OK`, and criterion 10 requires the user crontab to be clean — so whether a warning counts as drift determines whether criterion 1 can pass before the PM performs the cutover step. D10's runbook ordering happens to place crontab removal before the `--check` green step, so the two are consistent in the happy path, but the classification should be stated rather than inferred.

### [PASS] Dependency direction is correct for a maintenance-band slice

The architecture is explicit that the "900 precedes 100-180" statement describes foundation slices only, and that maintenance slices "by construction come after the work they correct and depend on it" (900-arch.foundation-cleanup.md:29). This slice depends on 915 (archive layout, B2 remote, drill procedure) and 916 (the `install-production.sh` mould, `/etc/cron.d` install shape), both earlier in the same band, and takes no dependency on initiatives 100-180. It consumes 915's interfaces rather than redefining them — `check_archive_health.sh` keeps its four checks and the `ARCHIVE-BROKEN` flag contract (D6), and `pg_archivecleanup`-based pruning is extended with `-x`, not replaced.

### [PASS] Explicit-failure and no-magic-strings principles are honored concretely

D1 requires all arguments with no defaults and states the script "refuses ambient guesses the same way the 915 tools refuse an ambient DB URL," matching the architecture's "Explicit failure" principle (900-arch.foundation-cleanup.md:51). D2 writes the `archive_command` "as one string from one variable — the same value is what `--check` compares and what the runbook prints," and D5 requires each new threshold to be a named constant at the top of the script; both satisfy "No magic strings" (line 49) and CLAUDE.md's single-definition rule. D3's go/no-go is a measured 1.5× ratio with a specified fallback (uncompressed atomic archiving) rather than a guess, and D3's single-commit rule correctly binds `archive_command`, `restore_command`, prune, and health check together. The parent architecture states no latency or throughput NFR on any path this slice touches, and the slice nonetheless quantifies its own targets (2-hour stamp staleness, 10-minute `.tmp` age, 7-day retention, ~40 min/day upload at the measured 16 GiB/day).
