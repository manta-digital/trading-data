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
verdict: FAIL
verdictSource: stated
sourceDocument: project-documents/user/slices/924-slice.backup-health-in-mt-data-health-make-a-broken-archive-a-failed-unit.md
aiModel: claude-haiku-5-5
aiProfile: sdk
status: complete
dateCreated: 20261023
dateUpdated: 20261023
reviewedSha: d3ceb9fc7fb1a6930b193469bc7e4d25496db441
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
diffTruncated: false
turns: 3
promptTokens: 160196
cachedTokens: 17590
cacheWriteTokens: 142602
completionTokens: 9247
durationSeconds: 45.5
costUsd: 0.11599
costBasis: provider
squadronVersion: 0.23.8
findings:
  - id: F001
    severity: fail
    category: correctness
    summary: "`ran` is possibly unbound in `check_backup_root`"
    location: "src/manta_trading/cli/commands/health_backup.py:162"
  - id: F002
    severity: concern
    category: typing
    summary: "`gather_backup` and `gather_before_connect` take `settings: Any`"
    location: "src/manta_trading/cli/commands/health_backup.py:210"
  - id: F003
    severity: concern
    category: design
    summary: "`parse_backup_roots` returns either a list or an error string"
    location: "src/manta_trading/cli/commands/health_backup.py:unverified (function `parse_backup_roots`, roughly lines 66–90)"
  - id: F004
    severity: concern
    category: test-reliability
    summary: "Root-permission test lacks the skip guard its neighbour has"
    location: "test/unit/test_provision_tick_lib.py:unverified (`test_set_key_unwritable_directory_leaves_the_file_whole`)"
  - id: F005
    severity: concern
    category: shell-correctness
    summary: "`_env_commit` lets a trap-based cleanup depend on the editor's exit status"
    location: "deploy/lib/env_add_keys.sh:38-43"
  - id: F006
    severity: note
    category: style
    summary: "`read_root` raises an exception only to catch it in the same function"
    location: "src/manta_trading/cli/commands/health_backup.py:195-196"
  - id: F007
    severity: note
    category: correctness
    summary: "`summarise_flag` falls back to all failures when none match the flag's class"
    location: "src/manta_trading/cli/commands/health_backup.py:unverified (function `summarise_flag`, the `own` filter)"
  - id: F008
    severity: note
    category: shell-style
    summary: "Shell library defines global variables without a namespace"
    location: "deploy/lib/env_add_keys.sh:unverified (the `ENV_BACKUP_ROOTS_SEPARATOR` and `ENV_BACKUP_ROOTS_KEY` assignments near the top of the file)"
  - id: F009
    severity: note
    category: docs
    summary: "Install script header comment is over-long and wrapped unevenly"
    location: "deploy/install-production.sh:6-9"
  - id: F010
    severity: pass
    category: test-coverage
    summary: "Writer, reader, and env setter are checked against each other"
    location: "test/unit/test_health_backup.py:unverified (`TestCronTemplateParity` and `TestWriterToReader`)"
  - id: F011
    severity: pass
    category: error-handling
    summary: "Exception handling follows the project pattern"
    location: "src/manta_trading/cli/commands/health_backup.py:203"
---

# Review: code — slice 924

**Verdict:** FAIL
**Model:** claude-haiku-5-5

## Findings

### [FAIL] `ran` is possibly unbound in `check_backup_root`

`ran` is assigned only in the `else` branch at line 154. Line 162 reads it after the `if problems:` return. The code is correct at runtime, because a `None` log mtime always adds a problem and returns early. Pyright cannot see that correlation, so strict mode will likely report `"ran" is possibly unbound`. Project rules make zero pyright errors a merge blocker. Fix by computing `ran` once before the branch, for example `ran = _ago(now, obs.log_mtime)` guarded by the `None` check, or by returning the healthy verdict from inside the `else`.

### [CONCERN] `gather_backup` and `gather_before_connect` take `settings: Any`

Both functions accept `Any`, which removes type checking on `settings.backup_roots`. The project rules require type-hinted signatures. Type the parameter with the `Settings` class from `manta_trading.config`. Tests can still pass a mock or a real `Settings` instance.

### [CONCERN] `parse_backup_roots` returns either a list or an error string

The return type `list[str] | str` uses the string as an error channel, and callers discriminate with `isinstance(roots, str)` in `gather_backup`. A mistyped caller that forgets the check would iterate over characters of the message. Use a small result type, or raise a `ValueError` and let `gather_backup` convert it into the one `SETTING_CHECK` row. Either keeps the sentinel out of the value domain.

### [CONCERN] Root-permission test lacks the skip guard its neighbour has

The test relies on `chmod 0555` blocking writes, which root ignores. Under root, `result.returncode != 0` fails. The sibling test `test_unreadable_flag` in `test_health_backup.py` has `@pytest.mark.skipif(os.geteuid() == 0, ...)`. Add the same marker here so the two tests behave consistently.

### [CONCERN] `_env_commit` lets a trap-based cleanup depend on the editor's exit status

The RETURN trap removes `$tmp` on every return from `_env_commit`, including after a successful `mv`. The comment says it is a no-op after the `mv`, which holds only because `mv` removes the source. This works, but it is subtle. Add a short note that the trap relies on `mv` consuming `$tmp`, or unset the trap after a successful `mv`. This is a clarity fix, not a bug.

### [NOTE] `read_root` raises an exception only to catch it in the same function

The `NotADirectoryError` is constructed and caught within `read_root`. Returning `RootObservation(root, read_error=f"cannot read {base}: Not a directory")` directly is clearer and avoids the `errno` and `os.strerror` imports.

### [NOTE] `summarise_flag` falls back to all failures when none match the flag's class

`fails = [...] or fails` shows the other class's failures in a flag file when the `FAILED` line names classes but none match this flag. The intent is probably to avoid an empty detail, but it can attribute a stale failure to the archive flag. The existing test covers only the matching path. Either document the fallback or add a test for the mismatch case.

### [NOTE] Shell library defines global variables without a namespace

These assignments run whenever the file is sourced, so they leak into `install-production.sh`. Declaring them `readonly` or making them `local` would reduce surprise. Tolerable here, since the install script does not reuse the names.

### [NOTE] Install script header comment is over-long and wrapped unevenly

The reflowed line "...sets the one key it manages, MT_BACKUP_ROOTS), the" runs past 88 columns, and the following line is reflowed differently. Rewrap the comment block to match the surrounding lines.

### [PASS] Writer, reader, and env setter are checked against each other

The cron template is held to the shared flag constants, the writer's real output is parsed by `read_root`, and the Python and shell separator are checked by round-tripping through `parse_backup_roots`. The health command's exit-code and print-before-outage behaviour are covered as well.

### [PASS] Exception handling follows the project pattern

The `OSError` catch in `read_root` and the `FileNotFoundError` catches in `_read_flag` and `_log_mtime` each carry a comment justifying why swallowing is correct, and the error is reported as the root's failing verdict. No bare `except` or `except Exception` is introduced.

### Run Digest

- Response length: 5783 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 160196 / 17590 / 142602 / 9247 / not computed
- Duration: 45.5 s
- Cost: $0.12 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 11
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 11
- Finding-shaped matches — surviving validation: 11
