---
docType: review
layer: project
reviewType: code
slice: tick-archive-adoption
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/223-slice.tick-archive-adoption.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20260929
dateUpdated: 20260929
reviewedSha: c4e0103e6b3a72395d902f1099516fa213d45f75
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 0
diffTruncated: true
squadronVersion: 0.16.0
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "Malformed manifest entries escape as raw exceptions, not refusals"
    location: "src/manta_trading/data/tick/adopt_files.py:_listed"
  - id: F002
    severity: concern
    category: error-handling
    summary: "`adopt` exits 0 when verification fails"
    location: "src/manta_trading/cli/commands/tick.py:tick_adopt"
  - id: F003
    severity: concern
    category: error-handling
    summary: "`ProviderError` during verification leaves a partly adopted job"
    location: "src/manta_trading/data/tick/adopt.py:adopt_job"
  - id: F004
    severity: concern
    category: security
    summary: "`reset` skips confirmation in JSON mode"
    location: "src/manta_trading/cli/commands/tick.py:tick_reset"
  - id: F005
    severity: note
    category: naming
    summary: "Redundant and obscure logic in `reset_units` and the CLI"
    location: "src/manta_trading/data/tick/reset.py:reset_units"
  - id: F006
    severity: note
    category: testing
    summary: "Slow, contrived test for the unreachable calendar"
    location: "test/integration/data/test_tick_adopt.py:test_unreachable_calendar_raises_with_no_rows"
  - id: F007
    severity: note
    category: design
    summary: "Archive path duplicated between the backup script and the setting"
    location: "scripts/cron_system_backup.sh:31-35"
  - id: F008
    severity: note
    category: naming
    summary: "Verify script's step 7 only echoes"
    location: "scripts/verify_tick_archive_backup.sh:130-133"
  - id: F009
    severity: pass
    category: design
    summary: "Consolidation, compare-and-set design and schema"
    location: "src/manta_trading/data/tick/manifest_repo.py"
---

# Review: code — slice 223

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5
**Diff:** truncated: 100000 of 158269 characters reached the model

## Findings

### [CONCERN] Malformed manifest entries escape as raw exceptions, not refusals

`_read_manifest` wraps only `json.loads` and the top-level `body["job_id"]` and `body["files"]` lookups in `try`. `_listed(entry)` runs afterwards in a list comprehension, outside that `try`. A missing `filename`, `hash` or `size` key raises `KeyError`. A non-numeric `size` raises `ValueError`. A non-dict entry raises `TypeError`. None of these is a `TickAdoptionRefused`, so `_exit_code` returns `None`. The CLI then shows a traceback instead of exit 1 with a message, although the module docstring promises refusals for bad manifests. Handle the same exception types inside `_listed`, or validate the manifest with a Pydantic model. Project rules call for Pydantic on file-parsing boundaries. A test using a real provider manifest with a missing field would catch this.

### [CONCERN] `adopt` exits 0 when verification fails

`adopt_job` collects `verify_failures` and records them as deterministic unit failures. `tick_adopt` then calls `print_adopt` and returns normally, so the process exits `EXIT_OK` even when files failed verification. A script or cron wrapper cannot tell a clean adoption from one with failed units. Exit non-zero, or add a distinct code, when `result.verify_failures` is non-empty. The failure is also visible only in rendered output.

### [CONCERN] `ProviderError` during verification leaves a partly adopted job

The request and units are committed by `_insert` before `check` runs. `check` calls the provider's `record_count`, and a `ProviderError` propagates as exit 2. A re-run then reports "already adopted" and does nothing, while the units stay `downloaded`. The docstring for `verify.py` says this is deliberate ("run-level failure for the caller's phase"). Nothing in the adopt output or exit message tells the operator that the units are unverified and what to do next. Either add that guidance to the error path or note the recovery route in the CLI epilog.

### [CONCERN] `reset` skips confirmation in JSON mode

`if not yes and not json_output` means `--json` silently bypasses the typed confirmation for `reset --all`. Turning an output-format flag into an implicit `--yes` is surprising. Require `--yes` explicitly in JSON mode and refuse otherwise.

### [NOTE] Redundant and obscure logic in `reset_units` and the CLI

`every = isinstance(unit_ids, str)` is computed and then the same `isinstance` check is repeated in the `wanted` expression. Use `every` in both places. The CLI guard `if every == bool(unit_ids)` is an XOR trick that needs a comment, or two explicit conditions.

### [NOTE] Slow, contrived test for the unreachable calendar

The test deliberately waits out the calendar pool's fixed 30 s connection timeout, with a 90 s pytest timeout. That adds a 30 s tail to every integration run. Consider injecting a shorter pool timeout or a fake calendar to cover the `TickCalendarError` mapping. The `_running` wrapper around `make_run` adds nothing, since `make_run` already returns an async context manager.

### [NOTE] Archive path duplicated between the backup script and the setting

`/data/tick-archive` is hard-coded in `INCLUDE_PATHS` and must equal `MT_TICK_ARCHIVE_DIR`. The comment and `verify_tick_archive_backup.sh` mitigate this, and the duplication is acknowledged. Note also that the header comment line at the top of the script now exceeds 88 columns.

### [NOTE] Verify script's step 7 only echoes

The actual cleanup runs in an `EXIT` trap. The "7. clean up" step prints a message before the removal happens, so the log line precedes the action it describes. Functionally correct, but slightly misleading.

### [PASS] Consolidation, compare-and-set design and schema

Hashing is now a single `sha256_file` shared by download, adopt and verify, which removes the duplicate `_sha256`. Every unit update is a compare-and-set that names its source state and raises `ManifestTransitionError`. The read/write split keeps the files under ~300 lines. The migration renders enum checks from enums, the exception handlers are justified, and the tests are parametrized across the transition matrix.

### Run Digest

- Response length: 5019 chars
- Response is newline-free: no
- Tool calls made: 0
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
