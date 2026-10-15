---
docType: review
layer: project
reviewType: code
slice: cross-source-acquisition-arbitration
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: PASS
verdictSource: stated
sourceDocument: project-documents/user/slices/233-slice.cross-source-acquisition-arbitration.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261014
dateUpdated: 20261014
reviewedSha: ceee3dbb73f14363974d95d692a82f12d161311a
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 0
diffTruncated: false
durationSeconds: 16.4
squadronVersion: 0.21.9
findings:
  - id: F001
    severity: pass
    category: error-handling
    summary: "Derivation script is read-only and fails explicitly"
    location: "scripts/acquisition_memory_bound.py:114-130"
  - id: F002
    severity: pass
    category: testing
    summary: "Tests use real journal formats and cover the failure paths"
    location: "test/unit/scripts/test_acquisition_memory_bound.py:20-60"
  - id: F003
    severity: note
    category: design
    summary: "Membership logic is written twice, with slightly different semantics"
    location: "test/unit/deploy/test_units.py:477-486"
  - id: F004
    severity: note
    category: error-handling
    summary: "`journalctl` not being installed raises an uncaught `FileNotFoundError`"
    location: "scripts/acquisition_memory_bound.py:80-88"
  - id: F005
    severity: note
    category: style
    summary: "Minor style and typing nits"
    location: "test/unit/scripts/test_acquisition_memory_bound.py:139"
  - id: F006
    severity: note
    category: style
    summary: "Unrelated whitespace cleanup in the test diff"
    location: "test/unit/deploy/test_units.py:252"
---

# Review: code — slice 233

**Verdict:** PASS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Derivation script is read-only and fails explicitly

`main()` catches only `MemoryBoundError`, which is the script's own error type. The comment marks it as a process-boundary handler, so it meets the exception-handling rule (c). A missing journal peak or a failing `journalctl` exits 2 with the unit named, and no default stands in for the missing value. This follows the "no silent fallback" principle. The constants (`MEMORY_BOUND_HEADROOM`, `JOURNAL_WINDOW_DAYS`, `ACQUISITION_SLICE`) are each defined once.

### [PASS] Tests use real journal formats and cover the failure paths

The fixtures are journal lines copied from the host. They cover G and M suffixes, fractional values, a trailing swap-peak clause, and lines with no peak. The tests also read the real unit files for membership, which matches the parsing rule about testing real-world input. `main()` is tested for exit 0 with no files written, and for exit 2 in both failure modes.

### [NOTE] Membership logic is written twice, with slightly different semantics

`scripts/acquisition_memory_bound.py#members` finds members with a regex that matches `Slice=` anywhere in the file. `test_every_member_is_oneshot` finds them by parsing `[Service] Slice`. The two will agree today, but the logic is duplicated, so they could drift. The test could call `amb.members()`, or the script could reuse the configparser approach.

### [NOTE] `journalctl` not being installed raises an uncaught `FileNotFoundError`

If `journalctl` is absent, for example when the script runs on a non-systemd host, `subprocess.run` raises `FileNotFoundError` with a raw traceback instead of the clean exit 2 used for other failures. The comment in `main` says other exceptions are meant to be bugs. A missing binary is an environment error, so wrapping it in `MemoryBoundError` would be more consistent.

### [NOTE] Minor style and typing nits

- The `lambda *a, **k` in `test_main_exits_2_on_a_failed_journal_read` is untyped and will likely trigger pyright strict errors.
- `write_text` calls in `test_members_excludes_other_slices` omit `encoding="utf-8"`, unlike the production code.
- `main()` prints MiB with a literal `1024**2` while `GIB` is a named constant.
- `main()` has no docstring.

### [NOTE] Unrelated whitespace cleanup in the test diff

The removal of a stray blank line has nothing to do with this slice's change. It is harmless.

### Run Digest

- Response length: 3103 chars
- Response is newline-free: no
- Tool calls made: 0
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 16.4 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
