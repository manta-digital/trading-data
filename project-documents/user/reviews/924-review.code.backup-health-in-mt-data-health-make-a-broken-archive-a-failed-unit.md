---
docType: review
layer: project
reviewType: code
slice: backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, shell.md, sql.md, testing.md]
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md
aiModel: claude-haiku-5-5
aiProfile: sdk
status: complete
dateCreated: 20261023
dateUpdated: 20261023
reviewedSha: 2107c2dc05b1f41fd9c095c5352f90430d36e21a
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
diffTruncated: false
turns: 3
promptTokens: 159133
cachedTokens: 28716
cacheWriteTokens: 130413
completionTokens: 11280
durationSeconds: 55.7
costUsd: 0.115802
costBasis: provider
squadronVersion: 0.23.8
findings:
  - id: F001
    severity: concern
    category: type-safety
    summary: "Settings parameter typed as `Any` in backup health gatherers"
    location: "src/manta_trading/cli/commands/health_backup.py#gather_backup"
  - id: F002
    severity: concern
    category: type-safety
    summary: "Test helper relies on `# type: ignore` to pass strict pyright"
    location: "test/unit/cli/commands/... (test_health_backup.py, `_obs`)"
  - id: F003
    severity: concern
    category: design
    summary: "`parse_backup_roots` returns either a list or an error string"
    location: "src/manta_trading/cli/commands/health_backup.py#parse_backup_roots"
  - id: F004
    severity: concern
    category: design
    summary: "Private helper imported across modules"
    location: "src/manta_trading/cli/commands/health_backup.py#check_backup_root"
  - id: F005
    severity: note
    category: correctness
    summary: "`unchanged` can be reported when the file's bytes change"
    location: "deploy/lib/env_add_keys.sh#_env_replace_key"
  - id: F006
    severity: note
    category: lint
    summary: "Shellcheck source directive uses a literal `SCRIPTDIR` path"
    location: "deploy/install-production.sh#Step 4"
  - id: F007
    severity: note
    category: style
    summary: "Inline imports inside test functions"
    location: "test/unit/cli/commands/test_data_health.py#TestBackupRows"
  - id: F008
    severity: note
    category: correctness
    summary: "Clock skew produces a negative \"ago\""
    location: "src/manta_trading/cli/commands/health_backup.py#check_backup_root"
  - id: F009
    severity: note
    category: design
    summary: "Manual edits to `MT_BACKUP_ROOTS` are overwritten on every install run"
    location: "deploy/install-production.sh#Step 4"
---

# Review: code — slice 924

**Verdict:** CONCERNS
**Model:** claude-haiku-5-5

## Findings

### [CONCERN] Settings parameter typed as `Any` in backup health gatherers

`gather_backup` and `gather_before_connect` take `settings: Any`. Under the required `typeCheckingMode = "strict"`, this removes the check that `settings.backup_roots` exists and is a `str | None`. A typo in the attribute name would pass pyright silently. Annotate with the `Settings` class from `manta_trading.config` (a `TYPE_CHECKING` import is enough to avoid cycles if needed).

### [CONCERN] Test helper relies on `# type: ignore` to pass strict pyright

`_obs(**kwargs: object)` builds `RootObservation(**fields)` and suppresses the mismatch with `# type: ignore[arg-type]`. The project treats zero pyright errors in `tests/` as a merge blocker, and a suppression hides real mismatches from future edits. Replace it with explicit keyword parameters with defaults (`archive_flag: str | None = None`, and so on), which needs no ignore and reads more clearly.

### [CONCERN] `parse_backup_roots` returns either a list or an error string

The return type `list[str] | str` mixes data and error text, and callers branch with `isinstance(roots, str)`. Any future caller that forgets the check will iterate over the characters of an error message. A small result type (a dataclass with `roots` and `error`), or raising a `ValueError` that `gather_backup` catches and turns into the `SETTING_CHECK` row, would make the contract explicit.

### [CONCERN] Private helper imported across modules

`health_backup` imports `_ago` from `health.py`. Importing an underscore-prefixed name across a module boundary couples the two modules through a name that is not part of the contract. Either make `_ago` public (for example `ago`) in `health.py`, or move it to a shared helper. The lazy import in `health.py` to avoid a cycle is correct, so only the naming needs fixing.

### [NOTE] `unchanged` can be reported when the file's bytes change

`_env_replace_key` rewrites every line with `printf '%s\n'`, so a last line without a trailing newline gains one. The function still prints `unchanged` when the value matched. The status word is then accurate about the key but not about the file. This is harmless for the install flow, but the header comment ("the file already held exactly that value") could be read as byte-identical. Either say "key value unchanged" in the comment, or add a test for the no-trailing-newline case that asserts the status.

### [NOTE] Shellcheck source directive uses a literal `SCRIPTDIR` path

The directives `# shellcheck source=SCRIPTDIR/lib/env_add_keys.sh` and `# shellcheck source=SCRIPTDIR/backup_clusters.sh` in `env_add_keys.sh` use `SCRIPTDIR` as a literal path prefix. shellcheck documents `SCRIPTDIR` as a special value for `source-path=`, not for `source=`. I could not run shellcheck here to confirm whether it warns. Run it on both files and, if it complains, switch to `# shellcheck source-path=SCRIPTDIR` plus a plain relative `source=` path.

### [NOTE] Inline imports inside test functions

`test_a_flag_fails_the_row_and_the_run` imports `json` inside the function, and `test_rows_print_before_the_primary_outage_error` imports `psycopg` the same way. Move both to the module top, matching the file's existing imports.

### [NOTE] Clock skew produces a negative "ago"

If `backup-health.log` has an mtime in the future (clock step, NFS skew), `now - obs.log_mtime` is negative, the staleness check passes, and `_ago` renders a negative duration. The check fails safe (it does not report stale), but the detail text is wrong. Consider clamping to zero or treating a future mtime as a failing row.

### [NOTE] Manual edits to `MT_BACKUP_ROOTS` are overwritten on every install run

This is the intended D4 behaviour, and the comments in `manta-trading.env.example` and the install script say so. An operator who hand-sets the key in `/etc/manta-trading.env` will see it replaced on the next run without warning beyond the `set` status line. Consider printing the old value's hash in the status line, as the script already does for the whole file, so the overwrite is visible in the log.

### Run Digest

- Response length: 5223 chars
- Response is newline-free: no
- Tool calls made: 2
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 3
- Tokens — prompt / cached / cache write / completion / reasoning: 159133 / 28716 / 130413 / 11280 / not computed
- Duration: 55.7 s
- Cost: $0.12 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
