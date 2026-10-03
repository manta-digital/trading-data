---
docType: review
layer: project
reviewType: tasks
slice: proof-on-existing-data
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md
aiModel: deepseek/deepseek-v4.1-flash
status: complete
dateCreated: 20261003
dateUpdated: 20261003
reviewedSha: 6561553d17b4a9950247d74d53c6340396f14c3c
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 33
turns: 20
promptTokens: 827665
cachedTokens: 708864
completionTokens: 88152
reasoningTokens: 78093
durationSeconds: 971.1
runId: run-20261003-p5-29c1b523
squadronVersion: 0.18.2
findings:
  - id: F001
    severity: concern
    category: test-collateral
    summary: "Tests asserting `tick_trade` has no compression will break under `tick_007`, but 8.3 does not enumerate them"
    location: "test/integration/data/test_tick_storage_track.py#test_trade_hypertable_has_no_compression"
  - id: F002
    severity: concern
    category: sequencing
    summary: "8.6's grant fix has no path to the already-provisioned databases"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:135"
  - id: F003
    severity: note
    category: process
    summary: "Load-tier CI gating is explicitly deferred, not left implicit"
    location: ".github/workflows/ci.yml"
  - id: F004
    severity: note
    category: consistency
    summary: "\"Through `tick_007`\" is a moving target once 9.4/10.4 may append migrations"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:230"
  - id: F005
    severity: note
    category: conventions
    summary: "`archive-check`'s manifest reader is private, so \"reuse if public\" resolves to a re-implementation"
    location: "src/manta_trading/data/tick/adopt_files.py#_read_manifest"
  - id: F006
    severity: pass
    category: coverage
    summary: "Every slice Success Criterion traces to a task, with no orphan tasks"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md"
---

# Review: tasks — slice 226

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash

## Findings

### [CONCERN] Tests asserting `tick_trade` has no compression will break under `tick_007`, but 8.3 does not enumerate them

Task 8.3 scopes the test sweep to "every test that pins the tick track's newest migration id or its count (start with `git grep -n "tick_006"` under `test/`)", and its Success line claims "no other integration test newly fails." But `tick_007` sets `timescaledb.enable_columnstore`, and `test_tick_storage_track.py` asserts `hypertables.compression_enabled = false` for `tick_trade` (and asserts the primary key is the table's only index). Those assertions flip the moment `tick_007` is appended to `TRACKS["tick"]`, because the file's fixtures migrate the real track — no `tick_006`-string grep finds them. The same file's `test_trade_has_only_the_primary_key_index` is at risk if the chosen layout or the Q1–Q4 miss in 10.4 adds an index. 8.3 should enumerate these by name (or instruct a `tick_trade`-geometry sweep) rather than relying on the migration-id grep and the "no other test newly fails" clause, which asserts a result without a step that produces it.

### [CONCERN] 8.6's grant fix has no path to the already-provisioned databases

Task 8.6 says that if `tick_app` cannot insert into a compressed chunk, "extend `provision_tick_roles.sql` with the minimal grant and add a test for it." Editing that SQL file does nothing on its own: grants reach a database only when `scripts/provision_tick_cluster.sh` applies the file, and that script is run once, by the PM, in part 1 (3.7) — before Section 8 runs, with a re-run that reconciles settings and restarts `postgresql@17-tick`. Nothing in 8.6 tells the implementer how the new grant lands in `trading_tick_proof` (a second script run, a targeted `ALTER ROLE`/`GRANT` under the maintenance credential, or an amendment to 3.7's checkpoint), nor how it lands in `trading_tick` before 10.3/10.4 — while the contention loop from 7.5 may still be holding the proof database. This is a genuine fork in the task: once the grant is found necessary, the step needs the propagation command, not just the source edit.

### [NOTE] Load-tier CI gating is explicitly deferred, not left implicit

`test/load/test_226_tick_query_nfr.py` (10.5) is a proper NFR load test with a stated gate (`MT_RUN_LOAD_TESTS=1`, which `scripts/run_tests.py`'s `load` tier sets) and a real bound. There is no CI wiring task, but this is not an omission in the breakdown: the repository's only workflow (`.github/workflows/ci.yml`) is a publish-on-tag job with no test job, the design states "CI runs no test job (slice 907)", and slice 907's architecture entry names load-tier gating as its own scope. The task file says so twice (10.5 and 14.3). Recording it here only so the deferral is visible; nothing in this breakdown needs to change.

### [NOTE] "Through `tick_007`" is a moving target once 9.4/10.4 may append migrations

Task 10.3 confirms the migration "applies through `tick_007`", and 10.4 permits a late `tick_00N` index/aggregate migration for a Q1–Q4 miss (9.4 permits `tick_008`). 14.3 already hedges this ("`tick_008` if it exists"), but 10.3's own instruction and the Success line pin `tick_007` literally. Wording them as "the newest tick migration in `TRACKS["tick"]`" keeps 10.3, 14.1's test-update step and 14.3 in agreement whichever optional migrations land.

### [NOTE] `archive-check`'s manifest reader is private, so "reuse if public" resolves to a re-implementation

Task 13.2 correctly identifies that `sha256sum -c` cannot parse the archive's `manifest.json` (the design's walkthrough step 8 is wrong on that point, and 14.1 amends it) and points at `adopt_files.py`'s reader, conditional on it being public. It is not — `_read_manifest`, `_Manifest` and `_ManifestEntry` are all underscore-private. "Reuse if public" therefore silently degrades to a second parser over provider metadata, which is the kind of duplication the project guidelines forbid. Either the step should name the small extraction it will make, or it should state plainly that the reuse does not apply.

### [PASS] Every slice Success Criterion traces to a task, with no orphan tasks

Criteria 1 and 7 map to part 1 (3.1–3.7, 1.1–1.5); Criterion 5 to 5.1; Criterion 6 to 8.3/8.5/8.6; Criterion 3 to 8.2–8.4 plus 10.4; Criteria 2 and 9 to 10.3/10.4/12.3 and 11.1–11.2/13.2; Criterion 8 to 12.1–12.3; Criterion 4 to 13.1, which requires a report citation for every TD 3 row. The Technical Requirements are covered in 8.3 (migrations README), 3.5 (`.env` writer and log-scrub tests), 2.3/10.2/11.2 (guard tests), 14.1–14.2 (contract, plan, architecture, CHANGELOG, README) and 14.3 (shellcheck, mypy/ruff, prod-cluster checksum comparison, timer check, walkthrough 1–9). The only task that adds scope beyond the design — 13.2's `archive-check` — is justified by the design's own incorrect `sha256sum -c` claim and is folded back into the design by 14.1, so it is a correction rather than creep. Section checkpoints with commits (1.5, 2.4, 3.6/3.7, 4.2, 5.4/5.6, 6.6, 7.5, 8.8, 9.2, 10.4, 11.2, 12.4, 13.2, 14.2) are spread through the work, and each task is small enough for a junior implementer with the Success line as the definition of done.

### Run Digest

- Response length: 6715 chars
- Response is newline-free: no
- Tool calls made: 33
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 305721
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / completion / reasoning: 827665 / 708864 / 88152 / 78093
- Duration: 971.1 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
