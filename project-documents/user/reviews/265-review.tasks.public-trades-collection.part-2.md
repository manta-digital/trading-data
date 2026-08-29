---
docType: review
layer: project
reviewType: tasks
slice: public-trades-collection
project: trading-data
verdict: FAIL
sourceDocument: project-documents/user/tasks/265-tasks.public-trades-collection-2.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260829
dateUpdated: 20260829
reviewedSha: ca487a1be8edf17a7906aa83be8f8cd6954044af
findings:
  - id: F001
    severity: fail
    category: correctness
    summary: "Task 5.1 instructs both \"re-export from `status.py`\" and \"no re-export through `status.py`\""
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:49-63"
  - id: F002
    severity: concern
    category: completeness
    summary: "The `status` Rich block prints a `cutoff` value that no task and no persisted column can supply"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:70-72"
  - id: F003
    severity: concern
    category: sequencing
    summary: "Task 5.2b depends on Task 5.4, which is two tasks later"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:92-93"
  - id: F004
    severity: concern
    category: correctness
    summary: "Task 7.5's compressed timing is not comparable to Task 7.3's uncompressed baseline"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:246-259"
  - id: F005
    severity: concern
    category: completeness
    summary: "Task 9.3 requires reading `capped` and `requests` from a timer firing with no source named"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:352-354"
  - id: F006
    severity: note
    category: testability
    summary: "Task 8.1's success criterion is not objectively checkable"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:300"
  - id: F007
    severity: note
    category: test-coverage
    summary: "No load test task, and none is required — the slice restates no NFR"
    location: "test/load"
  - id: F008
    severity: note
    category: task-sizing
    summary: "Tasks 5.1 and 5.2a are granular enough to merge"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:49-80"
  - id: F009
    severity: pass
    category: completeness
    summary: "All 13 success criteria trace to tasks, with no untraced tasks"
    location: "project-documents/user/slices/265-slice.public-trades-collection.md:372-386"
  - id: F010
    severity: pass
    category: sequencing
    summary: "Commit checkpoints are distributed, and the wait-blocked work is correctly excluded"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:136-138"
---

# Review: tasks — slice 265

**Verdict:** FAIL
**Model:** claude-opus-5

## Findings

### [FAIL] Task 5.1 instructs both "re-export from `status.py`" and "no re-export through `status.py`"

The first bullet says to put `TradeStatus` / `read_trade_status` in a new `data/kalshi/trade_status.py` "and **re-export them from `status.py`** so the CLI's import site does not fragment." The third bullet, in bold, says "**No re-export through `status.py`.** Task 5.3 wires the one CLI call site directly to `trade_status`; a module whose only job is to forward a name is the complexity CLAUDE.md tells us to resist."

These are mutually exclusive and appear in the same task, four lines apart — the second reads like a review fix appended without editing the original bullet. A junior AI executing top-to-bottom writes the forwarding import, then reads the next bullet telling it not to. Downstream, Task 5.3 (line 107-109) assumes the direct-import form, so the first bullet is also the wrong one. Delete the re-export clause from the first bullet.

### [CONCERN] The `status` Rich block prints a `cutoff` value that no task and no persisted column can supply

Task 5.2a enumerates the `TradeStatus` fields as `last_phase_at`, `tape_through`, `lag`, `behind`, `coverage_from` — matching the design's field list at `265-slice.public-trades-collection.md:326`. But the design's Rich block one line below (`:336`) and the walkthrough step 5 sample output (`:441`) both print `cutoff 2026-06-29` on the trades header line, and Task 5.3 requires "the Rich block renders **every field**" in "the design's *Rich block* layout".

The trades cutoff is fetched from `GET /historical/cutoff` per run and only logged (Data Flow step 1); it is not a `sync_state` column — `kalshi_006` adds only `coverage_from_ts`. Since Criterion 11 and Decision 10 forbid `trade_status` from importing the client, there is no legal source for it. The example values coincide only because `coverage_from == cutoff` on day one; they diverge by ~1 day/day thereafter, so silently rendering `coverage_from` as "cutoff" is a wrong label, not a shortcut. Task 5.2a or 5.3 must either drop `cutoff` from the block or add a persisted `cutoff_observed_at`-style column to `kalshi_006` (which is part 1's Task 2.2 — so this must be settled before Section 2 is committed, not at Section 5).

### [CONCERN] Task 5.2b depends on Task 5.4, which is two tasks later

Task 5.2b says "Get the boundary cases from **Task 5.4** green before moving on," and its success criterion is explicitly delegated forward ("assert this as a sum against the total in Task 5.4", line 99-100). Task 5.4 sits after Task 5.3 (rendering), so the stated order is 5.2b → 5.3 → 5.4 while the real dependency is 5.2b ↔ 5.4.

This is the one place in the breakdown that breaks the test-with-implementation pattern the rest of the file honors (2.2→2.3, 3.2→3.3, 4.2→4.3a/4.3b all pair correctly). The four closed-market counts are called out as "where the risk in the whole section sits," which makes it the worst task to leave provable only after a rendering task intervenes. Swap 5.3 and 5.4, or split 5.4's boundary-case tests into 5.2b itself and leave the `None`/field-value cases in 5.4.

### [CONCERN] Task 7.5's compressed timing is not comparable to Task 7.3's uncompressed baseline

Criterion 12's second clause asks for per-window wall time "before and after compressing that chunk." Task 7.3 records the baseline from the **first** pass over three fresh windows — every row a real insert. Task 7.5 records the comparison figure from a re-walk of an already-walked window after `compress_chunk`, where every row hits `ON CONFLICT DO NOTHING` and writes nothing.

Two variables change at once (compression state *and* insert-vs-conflict path), so a difference between the two numbers does not attribute to compression, which is the whole point of the measurement and of the pause/resume lever it is meant to justify. Task 7.4 already performs an uncompressed re-walk of a previously-walked window — that is the matched baseline. Task 7.5 should compare against 7.4's re-walk timing (and Task 7.4 should be told to record it), not against 7.3's.

### [CONCERN] Task 9.3 requires reading `capped` and `requests` from a timer firing with no source named

The task asks the PM to confirm "the phase summary reports `capped: true` with `requests` at or just above `TRADE_REQUESTS_PER_PASS`" from a `sudo mt-run kalshi` firing. But the two journal greps the design specifies for step 9 (`kalshi pass finished` and `trades window`) carry neither field: the per-window INFO line format is `pages N fetched F written W unknown U excluded X` (design `:167`), and `capped`/`requests` live only in `TradeResult.to_dict()`, rendered by `print_trade_summary` (part 1 Task 4.6).

This is the *only* production observation of Criterion 8, so it cannot rest on an unstated assumption that `mt-run`'s stdout reaches the journal in a parseable form. Name the concrete source — `--events-file` on the supervised firing, a `jq` over the events JSONL, or an explicit journal line the pass-finished summary must carry.

### [NOTE] Task 8.1's success criterion is not objectively checkable

"A reader following the runbook can update the host without consulting the slice design" cannot be verified by the agent writing it. Every other task in the file has a mechanical success test (a command that must be green, a number that must be recorded, an output captured). Replace with the checkable form: the runbook's Kalshi subsection names `kalshi_006_trades`, the `MT_KALSHI_COLLECTION_*` prefix, the ~10-day drain, and the by-hypertable-name policy lookup; and `grep -c MT_KALSHI_CANDLE_ project-documents/user/runbooks/100-production-operations.md` returns 0.

### [NOTE] No load test task, and none is required — the slice restates no NFR

The repo does have a load tier (`test/load/test_167_data_status_nfr.py`, `test_187_api_nfr.py`, gated by `MT_RUN_LOAD_TESTS` in `scripts/run_tests.py:45`), so the "load test in the load tier + CI gating" rule is live here in principle. But neither `260-arch.kalshi-event-contract-data.md` nor the 265 slice contains an NFR section or a restated threshold — the performance figures in the slice (~15-minute capped pass, ~3-minute steady state, per-window wall time) are operational estimates verified by rehearsal measurement and single-firing observation, not gated thresholds. This matches part 1's recorded disposition of the same question ("F009 no action — no NFR to gate on"). Recording it here so a later reviewer does not re-open it. Note also that `.github/workflows/ci.yml` does not invoke the load tier at all, so any future NFR for this initiative would need CI wiring added as a separate concern.

### [NOTE] Tasks 5.1 and 5.2a are granular enough to merge

Task 5.1's entire deliverable is creating an empty-ish `trade_status.py` and adding it to the `test_status_imports.py` guard list; Task 5.2a then fills it. Neither is independently meaningful — 5.1's success criterion ("both modules are under ~300 lines") is trivially true of an empty file. Merging them at effort 3 would still sit inside the breakdown's stated ceiling and would remove the task boundary that the Task 5.1 contradiction above straddles. Task 5.2b stays separate on its own merits (it is the risky one).

### [PASS] All 13 success criteria trace to tasks, with no untraced tasks

Cross-referencing both parts: C1→4.4/6.1/7.3/9.2; C2→3.2/3.3/4.3b·4/7.3; C3→3.3·4/6.1/7.4; C4→4.3b·5,6/7.4; C5→§1/3.3·5/7.2; C6→4.3b·1,8/9.2; C7→4.3b·2/7.3; C8→4.3b·7/9.3; C9→3.3·2/4.3b·10/7.3/7.6; C10→6.2/7.2; C11→5.1–5.4; C12→2.3/7.5; C13→9.2/9.3 plus an explicit handoff. Every task in part 2 traces back to a criterion, the design's *Tests* section, or the Risk Assessment (Task 7.4's late-arrival re-walk) — no scope creep found.

### [PASS] Commit checkpoints are distributed, and the wait-blocked work is correctly excluded

Each section ends in its own checkpoint commit (5.5, 6.3, 7.6, 8.2), matching the design's *Development Approach* sections and the PM-confirmed per-section granularity; Section 9 correctly has none, being host observation. Task 8.2 also explicitly explains why its gate list differs (markdown only) rather than silently omitting the gates. Criterion 13's multi-day drain is carried as a handoff note and 266 prerequisite (lines 367-376) rather than as a checklist item, honoring the standing veto on wait-blocked tasks, and it names the four mechanisms that *are* proven without waiting.
