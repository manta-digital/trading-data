---
docType: review
layer: project
reviewType: slice
slice: backup-hardening-and-host-bootstrap
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260905
dateUpdated: 20260905
reviewedSha: 122feba6a571551891049fc59e34702acc83995d
findings:
  - id: F001
    severity: concern
    category: failure-modes
    summary: "The weekly base/prune job gains new abort paths but no staleness alarm"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:300-308"
  - id: F002
    severity: concern
    category: under-specification
    summary: "`--keep-days 7` is adopted from an emergency hand-edit with no capacity derivation"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:287-291"
  - id: F003
    severity: concern
    category: observability
    summary: "`OFFSITE-BROKEN` has no delivery path and nothing is obliged to read it"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:331-339"
  - id: F004
    severity: concern
    category: dependency-direction
    summary: "The production backup tier is made permanently dependent on a dev checkout, by amending another slice's ADR in place"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:123-132"
  - id: F005
    severity: note
    category: architecture-alignment
    summary: "Deviation from \"CLI is the verification surface\" is justified per-slice rather than recorded in the architecture"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:95-99"
  - id: F006
    severity: note
    category: scope
    summary: "The restic include set is left open at design time"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:431"
  - id: F007
    severity: pass
    category: failure-modes
    summary: "Failure modes are enumerated with concrete handling for every new I/O path"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:248-256"
  - id: F008
    severity: pass
    category: architecture-alignment
    summary: "Single-definition discipline matches the architecture's \"no magic strings\" principle"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:243-247"
  - id: F009
    severity: pass
    category: correctness
    summary: "Ordering constraint keeps a restorable chain at every step"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:469-488"
  - id: F010
    severity: pass
    category: architecture-alignment
    summary: "Go/no-go on compression is decided by measurement, with both outcomes specified"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:202-214"
---

# Review: slice — slice 920

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Findings

### [CONCERN] The weekly base/prune job gains new abort paths but no staleness alarm

D4 adds three ways the weekly job can abort before doing anything: `rclone check --one-way` reporting differences (line 267), the four pre-sync guards (lines 275–286), and `--max-delete N` exceeded. The local prune runs *after* the check step (step 3 of 4), so any of these aborts also skips the prune. That is exactly the incident's mechanism — a prune that does not run, with nothing noticing.

D5 adds a stamp-and-staleness alarm for the hourly WAL push (`offsite_wal_stale`) and for the nightly restic run (`system_backup_stale`), but the weekly base/reconcile/prune job gets no equivalent. The nine checks in the Data Flow block (lines 504–508) contain no `base_backup_stale` or `prune_stale`. Failure scenario: `rclone check` reports a difference on three consecutive Sundays (B2 partial upload, clock skew, a stray file). Each run aborts, `base.log` records it, no flag is raised, `ARCHIVE-BROKEN` stays clear because local integrity is fine, and the archive grows unpruned for three weeks at 16 GiB/day (~340 GB) until `wal_disk_low` — a threshold alarm, not a cause alarm — fires or the disk fills. The design's own standard ("the two silent failures become named, tested alarms", line 64) argues for a stamp on this job too.

### [CONCERN] `--keep-days 7` is adopted from an emergency hand-edit with no capacity derivation

The slice exists because `/data` filled. It installs `--keep-days 7` as the script-managed value (line 356) and makes it govern offsite retention as well (lines 287–291), but nowhere states the resulting local footprint against `/data`'s capacity. The baseline table gives components (WAL 16 GiB/day measured, 784/day 7-day mean, bases ~81–98 G each, timeshift ~35 GB per snapshot × 2 per D8) but never sums them or names free space; the only sizing in the document is the B2 cost line ("$0.70/month", line 259). 915's runbook, by contrast, sized 21 days at ~170 GB against 759 GB free.

Two specific gaps. First, at the incident's own ~100 GB/day drain rate — which line 50 says is "still running with Crypto filtered" — 7 days of WAL is ~700 GB, so the retention this slice installs does not obviously survive a recurrence of the event that motivated it. Second, 915 D2 requires retention to be "at least as long as the interval between base backups plus a margin"; with weekly bases and 7-day retention the margin is zero, so at the moment before Sunday's run only the newest base and its WAL are guaranteed retained, leaving no fallback if that base proves bad. Neither the derivation nor the 21→7 rationale is restated here.

### [CONCERN] `OFFSITE-BROKEN` has no delivery path and nothing is obliged to read it

D6 deliberately removes the escalation that makes `ARCHIVE-BROKEN` self-announcing (a refused weekly base backup, per runbook 200's "How a failure surfaces" section). The reasoning — do not trade a backup for an alarm — is sound, but it leaves `OFFSITE-BROKEN` delivered only as a file on disk plus `logger -t manta-backup`, with the consumer being an operator who chooses to run `journalctl`. The slice notes cron mail does not exist on this host (line 411) and explicitly defers folding archive health into `mt data health` to future work (lines 105–107).

915 D2 required archive-failure monitoring be "surfaced somewhere an operator actually looks," and 919 established that surface concretely — a failed systemd unit visible in `mt-run status`, chosen precisely because the prior failures "were visible in numbers nobody was obliged to read." Failure scenario: the B2 key is rotated; the hourly push fails; `offsite_wal_stale` raises `OFFSITE-BROKEN`; the weekly base still succeeds; nothing in `mt-run status` or `systemctl --failed` changes; the offsite chain silently stops advancing until someone reads the flag file. That is the same class of defect the slice was written to eliminate. At minimum the design should name the obligated reader and the cadence, or give the check a unit/exit-code surface consistent with 919.

### [CONCERN] The production backup tier is made permanently dependent on a dev checkout, by amending another slice's ADR in place

The backup tier reads `MT_TIMESCALE_MAINTENANCE_URL`, the four `MT_BACKUP_S3_*` keys, and the new `MT_BACKUP_RESTIC_PASSWORD` from the dev checkout's `.env`, and `setup-backup.sh` takes `--checkout <path>` as a required argument (line 141–145). This makes a production-critical recovery path depend on a developer working tree in a user's home directory. The architecture's maintenance-band constraint (line 27) says a fix that requires changing another initiative's contract is escalated to the owning initiative rather than absorbed; here the 2026-08-23 ADR ("the dev checkout retains exactly two operational roles") is amended to three by this slice's own journal entry, and 916's "user crontab is PM-owned host config" is overridden by D7 (lines 343–348). Both amendments are recorded honestly where the amended decisions live (lines 695–698), which is better than a silent citation — but no alternative is evaluated. The obvious one, a root-owned `/etc` credential file (mode 0600) holding the maintenance URL and restic password, would keep the backup tier independent of any checkout and is not mentioned. This matters most for D10's acceptance test: a replacement host's recovery story now requires cloning a dev checkout before backups can run.

### [NOTE] Deviation from "CLI is the verification surface" is justified per-slice rather than recorded in the architecture

The architecture states without qualification (900-arch.foundation-cleanup.md:53) that "if a feature can't be exercised through `mt`, it doesn't exist yet." The slice places the backup tier outside `mt` and names a substitute surface (`setup-backup.sh --check` plus `check_archive_health.sh`), with real reasons: `ProtectSystem` hides `/data/backup` from the service account, and the tier needs the maintenance credential. The reasoning is good and 915 D5 set the precedent. The observation is that the exception now exists twice, re-argued from scratch each time, and the architecture document still reads as absolute. Recording a host-operations-tier exception in 900-arch would stop the next maintenance slice from re-litigating it.

### [NOTE] The restic include set is left open at design time

`/home/manta/Pictures` (67 G) and `/home/manta/ai` (60 G) are marked "**PM decides** at task time," leaving 127 GB — roughly the size of the entire estimated first snapshot (~150 GB, line 435) — undetermined. Deferring to a measurement is defensible and the cost argument makes it low-stakes, but the slice's own scope table therefore has an unbounded item; the decision and its rationale should land in the runbook, not only in a task-time conversation. Related: the plan entry authorizes `/home/manta` backup explicitly, so this is not scope creep against the band — it is just an open number.

### [PASS] Failure modes are enumerated with concrete handling for every new I/O path

The hourly WAL push names overlap (`flock -n`, exit 0 with a logged skip), hang (`timeout` set to interval minus one minute, so processes cannot queue), failure (non-zero exit, stamp untouched, `logger` line, next run is the retry), and the resulting alarm after 3× interval. The restic tier (lines 402–411) does the same and additionally handles restic's most common recurring failure — a stale repository lock from an interrupted run — with `restic unlock` made safe by the surrounding `flock`. The `rclone sync` guards (lines 275–286) enumerate unmounted `/data`, an emptied archive, a wrong rendered path, and prune/delete-count mismatch, each with an abort. No "TBD" appears on any of these paths.

### [PASS] Single-definition discipline matches the architecture's "no magic strings" principle

`WAL_OFFSITE_INTERVAL_MIN` is one constant that renders the cron.d schedule, the health check's `--stale-after`, and the push's `--timeout`; success criterion 6 makes "the interval appears exactly once in the repo" a testable condition. The `archive_command` is written from one variable that is simultaneously what `--check` compares and what the runbook prints (line 191–193), which is what keeps `postgresql.conf` from lying about the effective value (lines 171–174). Health-check thresholds are named constants at the top of the script. This is the architecture's line 49 rule applied to shell.

### [PASS] Ordering constraint keeps a restorable chain at every step

D11 sequences alarms before the things they guard, measurement before the compression decision, a mixed-archive PITR drill before offsite work, and a B2-sourced PITR drill before the first destructive offsite reconcile. The explicit statement that "nothing in steps 1–4 deletes a segment or a base outside the retention the prune already enforces" directly answers the plan entry's Risk: Med note. D3's four coupled changes shipping in one commit — `archive_command`, `restore_command`, prune `-x`, health check — correctly identifies that a `restore_command` mismatched to the archive shape fails at the worst possible moment.

### [PASS] Go/no-go on compression is decided by measurement, with both outcomes specified

D3 sets a numeric threshold (1.5× on a 200-segment sample) before the change is made, names the fallback (D2 atomic archiving without `zstd`, which still delivers the wedge fix and offsite WAL), and success criterion 3 covers both branches. This matches the architecture's "explicit failure, never silently fall back" posture and avoids the common antipattern of adopting compression because it sounds prudent.
