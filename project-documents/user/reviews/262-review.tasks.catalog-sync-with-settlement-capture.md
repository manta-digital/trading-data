---
docType: review
layer: project
reviewType: tasks
slice: catalog-sync-with-settlement-capture
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/262-tasks.catalog-sync-with-settlement-capture.md
aiModel: z-ai/glm-5.3
status: complete
dateCreated: 20260824
dateUpdated: 20260824
reviewedSha: db876c4e2ca0fc80d06ab7d6cbd5c29b29e5a961
findings:
  - id: F001
    severity: pass
    category: traceability
    summary: "All eleven success criteria trace to scheduled tasks; no scope creep"
    location: "project-documents/user/tasks/262-tasks.catalog-sync-with-settlement-capture.md"
  - id: F002
    severity: pass
    category: sequencing
    summary: "Sequencing, test-with pattern, and commit cadence are sound"
    location: "project-documents/user/tasks/262-tasks.catalog-sync-with-settlement-capture.md"
  - id: F003
    severity: concern
    category: coverage
    summary: "SC7's \"provider abort demonstrated with an unreachable base URL\" has no task"
    location: "project-documents/user/tasks/262-tasks.catalog-sync-with-settlement-capture.md"
  - id: F004
    severity: concern
    category: specification-clarity
    summary: "Task 5.6: whether the final, clamped settled window advances the watermark is ambiguous"
    location: "project-documents/user/tasks/262-tasks.catalog-sync-with-settlement-capture.md"
  - id: F005
    severity: concern
    category: test-coverage
    summary: "`refresh_awaiting_close_times` is implemented but never tested"
    location: "project-documents/user/tasks/262-tasks.catalog-sync-with-settlement-capture.md"
  - id: F006
    severity: note
    category: specification-clarity
    summary: "Task 5.8 contradicts itself on the classification return type"
    location: "project-documents/user/tasks/262-tasks.catalog-sync-with-settlement-capture.md"
  - id: F007
    severity: note
    category: task-sizing
    summary: "Task 8.3 and Section 5 are the two oversized units"
    location: "project-documents/user/tasks/262-tasks.catalog-sync-with-settlement-capture.md"
  - id: F008
    severity: note
    category: coverage
    summary: "SC9's mve_filter assertion covers only the walk queries"
    location: "project-documents/user/tasks/262-tasks.catalog-sync-with-settlement-capture.md"
  - id: F009
    severity: note
    category: load-testing
    summary: "No restated NFR, so no load-test task is required"
    location: "project-documents/user/slices/262-slice.catalog-sync-with-settlement-capture.md"
---

# Review: tasks — slice 262

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.3

## Findings

### [PASS] All eleven success criteria trace to scheduled tasks; no scope creep

Criterion-to-task mapping: SC1 → Tasks 3.2–3.7, 5.6, 5.8, 8.3 (first-run integration test asserts all three tables populated with FKs and both `sync_state` columns). SC2 → 3.2 (write-on-change test), 8.3 second-run, 10.2 rehearsal. SC3 → 3.6, 5.7, 8.3 ("close→awaiting, finalized→retired, removed-from-walk→looked-up"), 10.2 live settlement observation. SC4 → 5.6 (window-3 transient abort, resume with no gap), 8.3 (aborting fake source), 10.2 (Ctrl-C rehearsal). SC5 → 2.1/2.2, 3.4, 5.1/5.2, 8.3 (`--events-file` JSONL, 7 lines per run). SC6 → 7.1, 8.5. SC7 → 5.8 classification table, 8.3 five-outcome mapping, 8.2 preflight tests (one clause unassigned — see concern below). SC8 → 6.1. SC9 → 1.1, 5.4. SC10 → 5.8 type gate, 10.1. SC11 → 8.4(a) out-of-vocabulary status, 8.4(b) `pg_terminate_backend`, 8.2 lock test referenced by 8.4(c). Every design scope item (constants, events, repository, sync core, `kalshi_004`, `status.py`, CLI, rate override, three fixtures) has a corresponding task; conversely no task falls outside the design's Technical Scope.

### [PASS] Sequencing, test-with pattern, and commit cadence are sound

Section order mirrors the design's Development Approach: constants/events → repository with integration tests per task → test doubles (Section 4) *before* the core they support (Section 5) → migration → status → CLI → fixtures → rehearsal. Every implementation task carries its tests inline (3.2–3.7, 5.1–5.8, 6.1, 7.1, 8.2–8.5, 9.1). Eleven commit checkpoints are spread across sections (after 1.2, 2.2, 3.7, 4.2, 5.8, 6.1, 7.1, 8.4, 8.5, 9.2, 10.3) — none deferred to the end. Dependencies are acyclic: `sync.py` defines `CatalogSource` and the classification enum (4.1, 5.8), the CLI consumes them (8.1), `status.py` is independent, and Task 9.2 re-points existing fakes at recorded shapes and re-runs Section 5 tests unchanged instead of reordering work.

### [CONCERN] SC7's "provider abort demonstrated with an unreachable base URL" has no task

Success Criterion 7 in the design (`project-documents/user/slices/262-slice.catalog-sync-with-settlement-capture.md`) requires two proofs: unit tests of the classification function (covered by Task 5.8's classification table and Task 8.3's five-outcome CLI mapping) and a provider-abort demonstration "with an unreachable base URL." No task schedules the second: Task 5.2 injects `ProviderTransientError` through the fake source, Task 8.3's unit tests monkeypatch the core, and Task 8.3's integration test aborts via the fake — none exercises the real client stack end-to-end (httpx connect failure → 261's retry/transient mapping → exit 2), which is exactly what the unreachable-URL demo exists to catch. Add a case to Task 8.3 (point the client at an unroutable base URL, assert `EXIT_PROVIDER`), or record explicitly in Task 10.1's criteria walk where this clause is proven.

### [CONCERN] Task 5.6: whether the final, clamped settled window advances the watermark is ambiguous

Task 5.6 says "after each **complete** window `set_watermark(surface, b)`" and then "The final partial window ends at the run start time" — without stating whether that final partial window also writes the watermark. The design resolves this: the walkthrough shows "watermark → <run start>" after a first run, and State Management defines `watermark_ts` as the upper bound of the last completed (i.e., fully walked) window. Under the literal complete-vs-partial reading, a drain spanning less than one `SETTLED_WINDOW` (e.g., `--settled-since` under 6 h before run start) would leave `watermark_ts` NULL, contradicting SC1's "watermark_ts set." The current test ("watermark advances once per completed window") does not distinguish the two readings. State explicitly that a fully-walked final window — clamped to the run start — advances the watermark to that boundary, and assert it.

### [CONCERN] `refresh_awaiting_close_times` is implemented but never tested

The design's Repository contract lists `refresh_awaiting_close_times()`; Task 3.6 implements it and Task 5.7 invokes it, but neither task's test list covers it. Task 3.6 tests enter/not-enter/retire/`mark_checked` only; Task 5.7's tests never vary a market's `close_time`; and Task 7.1's histogram test seeds rows directly, so it would not catch a broken refresh either. A stale `close_time` silently corrupts awaiting ages (SC3's "age computable as now() − close_time") and the histogram/threshold reporting in 7.1. Add a case to Task 3.6: upsert a market with a changed `close_time`, call refresh, assert the awaiting row's `close_time` (hence age) updated.

### [NOTE] Task 5.8 contradicts itself on the classification return type

Task 5.8 opens with "Add a pure `classify(result, exc) -> int` function (or `SyncResult.exit_code` property …)" and then states "the core returns a classification enum, not integers, to avoid a core→CLI import." The closing sentence is the operative spec (and the correct one — Task 8.1 builds its mapping from the enum); drop the `-> int` annotation so a junior implementer doesn't build the int-returning variant the first clause describes.

### [NOTE] Task 8.3 and Section 5 are the two oversized units

Task 8.3 bundles the command implementation, its unit tests, and five integration scenarios that separately prove SC1–SC5; consider splitting the integration proofs into their own task so the command lands and commits before the scenario suite. Section 5 carries a single commit across eight tasks (~20 of ~63 effort points — roughly a third of the slice); an intermediate commit after Task 5.2 or 5.4 would keep reviewable units smaller. Also note CLAUDE.md's ~300-line file guidance: the tasks anticipate splitting *test* files at ~300 lines but not `sync.py` or `repository.py`, both of which plausibly exceed it (the repository gains all of 3.2–3.7 plus `open_sync_connection` from 8.2).

### [NOTE] SC9's mve_filter assertion covers only the walk queries

SC9 asserts `mve_filter=exclude` on "the fake source's received queries" for *every* markets request, but Task 5.4's assertion is scoped to the walk (it also requires a `CATALOG_WALK_FILTERS` status, which settled-window and ticker-batch queries don't carry), Task 5.6 specifies `mve_filter` for windows without an assertion, and Task 5.7's vanished batch lookups never mention `mve_filter` despite Decision 2's "every markets request." Practical risk is low (awaiting tickers are non-MVE by construction), but one extra assertion over all recorded markets queries — and an `mve_filter` note on the 5.7 lookups — would close the criterion exactly.

### [NOTE] No restated NFR, so no load-test task is required

The design restates no NFR requiring load verification: the <1 ms rule is an async-discipline code convention (Decision 8), and the ~70 GB/year storage growth is a PM-acknowledged capacity note in Risk Assessment, not an acceptance target. The breakdown accordingly and correctly contains no `tests/load/` task, and no CI-gating change is implied.
