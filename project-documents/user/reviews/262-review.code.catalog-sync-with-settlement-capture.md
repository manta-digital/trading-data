---
docType: review
layer: project
reviewType: code
slice: catalog-sync-with-settlement-capture
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/262-slice.catalog-sync-with-settlement-capture.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260825
dateUpdated: 20260825
reviewedSha: 01176c3adaedacefaaa22d47354f4c40a1b3a048
findings:
  - id: F001
    severity: concern
    category: async-correctness
    summary: "Blocking synchronous file I/O runs on the event loop with no executor"
    location: "src/manta_trading/data/kalshi/events.py:92-96"
  - id: F002
    severity: note
    category: observability
    summary: "Parent series/events written while resolving a markets page aren't reflected in their own phase counts"
    location: "src/manta_trading/data/kalshi/sync_writer.py:1831-1837"
---

# Review: code — slice 262

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] Blocking synchronous file I/O runs on the event loop with no executor

`JsonlSyncEventSink.emit` (`src/manta_trading/data/kalshi/events.py:92-96`) calls `Path.open("a", ...)`, `.write(...)`, and `.flush()` synchronously. It is invoked from `CatalogSync.emit` (`src/manta_trading/data/kalshi/sync.py:171-197`), itself called un-awaited from inside `async def run()` (line 146: `self.emit(SyncEventType.RUN_STARTED)`) and from `phase_finished`/`item_error`, both reached from async code throughout `sync.py`, `sync_awaiting.py`, and `sync_settled.py`. None of this is dispatched via `run_in_executor` or a thread. Per this project's async rule ("Any async def function that calls synchronous code must guarantee that the synchronous code runs in <1ms in the worst case... Anything CPU-bound must use run_in_executor... Reviewers MUST verify this"), a blocking file open/write/flush has no such guarantee — under disk contention, a slow/NFS-backed `--events-file` target, or a run with many `item_error` events (each triggering its own emit → write → flush), this stalls the event loop for the duration of each syscall. I confirmed `ruff check --select ASYNC --preview` does not catch this (the blocking calls are one frame removed from the `async def`, so the linter's textual rule can't see them) — this is exactly the case the project's rule calls out for manual review rather than mechanical enforcement.

Failure scenario: a `mt data kalshi sync --events-file <path-on-slow-storage>` run hits a page with many out-of-vocabulary rows (the row-by-row `IntegrityError` fallback in `sync_writer.py`), each producing an `item_error` event; each is a synchronous open/write/flush blocking the loop while other coroutines (e.g., the HTTP client's own retry/backoff timers) are starved.

### [NOTE] Parent series/events written while resolving a markets page aren't reflected in their own phase counts

`_own_kind` (used by `write_page`) reports only the phase's "own kind" of row — e.g. for `SyncPhase.MARKETS`, only markets written, even though the same page write can also insert parent `series`/`events` rows resolved via `_resolve_parents` (`sync.py:1403-1431`). Those parent writes never show up in `result.phases[SyncPhase.SERIES]` / `result.phases[SyncPhase.EVENTS]`, nor anywhere else in the summary/JSON output or `mt data kalshi status`. This looks like a deliberate simplification (each phase reports only its "own" kind), so it's not a bug, but an operator using the sync summary to gauge series/events churn will see `0` written there even on a run that created new parent rows purely as a side effect of the markets walk or the settled/awaiting phases. Worth a doc note if intentional, or folding the counts in if not.
