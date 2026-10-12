---
docType: review
layer: project
reviewType: tasks
slice: active-contract-and-roll-methods
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/229-tasks.active-contract-and-roll-methods.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261012
dateUpdated: 20261012
reviewedSha: ec9dfeaaad1850e0ffd9fb193ee8b20a00c5cc2f
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
durationSeconds: 48.3
squadronVersion: 0.21.5
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria coverage is complete"
    location: "project-documents/user/tasks/229-tasks.active-contract-and-roll-methods.md"
  - id: F002
    severity: concern
    category: error-handling
    summary: "`latest_active` does not convert connection and timeout failures into line errors"
    location: "project-documents/user/tasks/229-tasks.active-contract-and-roll-methods.md:323-329"
  - id: F003
    severity: concern
    category: completeness
    summary: "`latest_active` has no defined calendar range or way to find L"
    location: "project-documents/user/tasks/229-tasks.active-contract-and-roll-methods.md:323-329"
  - id: F004
    severity: concern
    category: sequencing
    summary: "Wrong cross-reference, and the fixture script is written before the move it depends on"
    location: "project-documents/user/tasks/229-tasks.active-contract-and-roll-methods.md:41-43"
  - id: F005
    severity: note
    category: task-sizing
    summary: "Task 5.6 bundles five independent test concerns"
    location: "project-documents/user/tasks/229-tasks.active-contract-and-roll-methods.md:331-344"
  - id: F006
    severity: note
    category: documentation
    summary: "Deviations from the slice text are declared but the design is not updated"
    location: "project-documents/user/tasks/229-tasks.active-contract-and-roll-methods.md:30-43"
  - id: F007
    severity: note
    category: commit-checkpoints
    summary: "Doc edits from 7.2 and 7.3 are committed only with the final validation"
    location: "project-documents/user/tasks/229-tasks.active-contract-and-roll-methods.md:428-456"
  - id: F008
    severity: note
    category: completeness
    summary: "`active` default product scope is not stated in task 6.2"
    location: "project-documents/user/tasks/229-tasks.active-contract-and-roll-methods.md:367-379"
---

# Review: tasks — slice 229

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria coverage is complete

Every functional requirement maps to at least one task:
- FR1 maps to 2.2 and 2.3.
- FR2 and FR3 map to 3.3 and 4.3.
- FR4 maps to 3.5.
- FR5 maps to 3.3.
- FR6 maps to 6.1, 6.2 and 7.1.
- FR6a maps to 3.5.
- FR7 maps to 6.4 and 6.5.
- FR8 maps to 6.3.
- FR8a maps to 6.5.
- FR8b maps to 5.6 and 6.5.
- FR8c maps to 6.5.
- FR9 maps to the grep check in 6.5.

The technical requirements are also covered:
- The no-I/O resolver is covered by 5.7.
- The TD10 timing re-run is covered by 7.1.
- The contract rows are covered by 7.2.
- The gating check is covered by 7.3.
- The Kalshi diff test is covered by 7.4.

No task lacks a traceable criterion. Test-with sequencing holds throughout, and commits fall at the end of Sections 2, 3, 4, 5 and 6.

### [CONCERN] `latest_active` does not convert connection and timeout failures into line errors

Task 5.5 turns only `RollInputError` and `TickPreflightError` into `ActiveLine.error`. The slice design (TD9 table, FR8b) says an unreachable database, a `statement_timeout` (`QueryCanceled`, an `OperationalError`) and a lost peer must also become an `error:` line in `status`, with exit 3. Calendar failures reach the line as `TickCalendarError`, which the design says must do the same.

Task 6.5 tests the lock-held timeout and expects an `error:` line and exit 3. Nothing in 5.5 or 6.4 implements that conversion, so the test would find an exception escaping `status`. The same exceptions must still propagate out of `build_schedule` so `active` exits 4 (task 6.2).

Fix 5.5 to name `OperationalError` and `TickCalendarError` as the per-rule line errors. Keep the catches specific, each with a comment, as CLAUDE.md requires. Also say whether a connection failure covers all rules, since they share one snapshot, or only the failing rule.

### [CONCERN] `latest_active` has no defined calendar range or way to find L

Task 5.4 specifies the calendar window for `build_schedule` from `start`, `end` and the expiration sessions. Task 5.5 takes no range. It must find the latest complete session L, which needs session verdicts, which in turn need calendar sessions. It also needs sessions through L + 2 for the volume forecast and through the candidates' expirations for the calendar forecast, and `known_through` for calendar depends on the last populated session. The task never says how the window is chosen, for example from the earliest ledger session through the latest candidate expiration.

A junior implementer would have to invent this. The invention affects correctness: a window that is too short raises a spurious `RollInputError`, and one that is too long can hit the `TICK_ROLL_MAX_SESSIONS` cap. Add a step stating the window and whether the cap applies to it.

### [CONCERN] Wrong cross-reference, and the fixture script is written before the move it depends on

The Context Summary says the `calendar_sessions` move happens in "task 4.1". The move is task 5.1; 4.1 is the fixture script.

This hides a real ordering problem. Task 4.1 must record "the verdict per session", so the script needs `session_verdicts`. That function still lives in `tick_status_build.py` until 5.1 moves it. Task 5.1 updates imports in `tick_status_build.py`, `tick_coverage.py` and tests, but not in `scripts/record_229_roll_fixture.py`. Task 1.1's caller inventory also omits scripts, because the script does not exist yet.

Either move 5.1 ahead of 4.1, or add the script to 5.1's import-update list and have 4.1 import from the final location. Correct the Context Summary reference either way.

### [NOTE] Task 5.6 bundles five independent test concerns

Task 5.6 covers:
- the seeded resolve;
- the missing-row error;
- supersession of `basis_unit_ids`;
- the concurrent-commit snapshot test;
- the lock-held timeout;
- the `roll_conninfo` contents;
- the cap refusal before any connection.

All of these trace to FR8b or the technical requirements, so none is scope creep. The snapshot and lock tests need concurrent connections and are the likeliest to take several attempts. Consider splitting them into their own task so a stalled concurrency test does not hold up the basic schedule tests. This is optional.

### [NOTE] Deviations from the slice text are declared but the design is not updated

The task file moves `DEFAULT_ROLL_RULE` and `STATUS_ROLL_RULES` into `roll_rules.py`. The design says they are in `constants.py`, and FR9 and TD5 say "defined once in `constants.py`". The justification (an import cycle) is sound, and the grep in 6.5 already allows `roll_rules.py`. The tests also live under `test/`, not `tests/`.

Walkthrough step 8 in the design runs `pytest tests/unit/data/tick`. Task 7.1 says to run "steps 1-8" without noting that this path must change to `test/unit/data/tick` or the repo's test script. Add that note to 7.1. Also consider having 7.3's gating check update the design text for these placements.

### [NOTE] Doc edits from 7.2 and 7.3 are committed only with the final validation

Tasks 7.2 and 7.3 edit the contract and possibly the architecture documents, but have no commit step of their own. Both are covered by 7.4's single commit, whose message names only the data-correctness contract. A 7.3 correction to the architecture or slice plan would be hidden under that message. Add a commit to 7.3, or broaden the 7.4 message.

### [NOTE] `active` default product scope is not stated in task 6.2

The design signature is `[--product P]`, which prints "for each product" when omitted. Task 6.2 lists `--product` but does not say what happens without it. Add the all-products default, and its test case in 6.3, so the omitted-flag behaviour is not guessed.

### Run Digest

- Response length: 6632 chars
- Response is newline-free: no
- Tool calls made: 2
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 48.3 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
