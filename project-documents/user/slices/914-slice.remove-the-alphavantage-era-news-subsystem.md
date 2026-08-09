---
docType: slice-design
slice: remove-the-alphavantage-era-news-subsystem
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: []
interfaces: []
dateCreated: 20260808
dateUpdated: 20260809
status: complete
review: none
---

# Slice Design: Remove the AlphaVantage-Era News Subsystem

## Overview

Slice 152 deleted AlphaVantage but left the news subsystem that was built on
top of it. What remains is a stub that raises unconditionally
([news.py:25-27](../../../src/manta_trading/news/news.py)):

```
"AlphaVantage was removed in slice 152. News via AV is not supported."
```

Service initialization fails, the database handle stays `None`, and
`_verifyNewsDb()` dies on `'NoneType' object has no attribute 'connect'`. The
code cannot run. Four integration tests exercise it and fail on every tier run
— not because they caught a regression, but because the thing they exercise no
longer exists. That is the actual cost of leaving it in place: **noise is how a
real regression gets missed.** Slice 913 spent real effort distinguishing
genuine failures from this permanent background, and one of the subsystem's
own tests carries a `pymongo` server-RTT thread that trips the 30-second pytest
timeout and has taken an unrelated neighboring test down with it in an
otherwise-clean run.

This slice deletes the subsystem outright rather than repairing or
re-stubbing it.

## Value

Developer-facing and architectural: makes the integration tier's signal
trustworthy again. Four fewer permanent failures, one fewer timeout hazard, and
two dependencies (`pymongo`, `motor`) dropped from a codebase that no longer
uses them. No behavior available to a user today is lost — the stub already
raises unconditionally, so nothing currently works.

**Not a deferral of news as a capability.** The PM's assessment: the
AlphaVantage product's value was pre-sentiment-analyzed articles, which
realtime article feeds do not provide — that analysis would have to happen in
this codebase instead. If news returns, it arrives with a different provider
and a different shape, so the AV-era schema and ingestion assumptions are a
liability rather than a head start. Git history preserves the code if it's
ever wanted for reference.

## Technical Scope

**Included:**
- Delete `src/manta_trading/news/` in full (6 source modules + `__init__.py`).
- Delete `src/manta_trading/agents/newsagent.py`.
- Delete the 8 test files that exercise the subsystem (listed under Migration
  Plan below).
- Remove `pymongo` (main dependency) and `motor` (dev dependency) from
  `pyproject.toml`, and refresh `uv.lock`.

**Explicitly excluded:**
- `chromadb` — present in `pyproject.toml` but grep confirms zero source or
  test references anywhere in the tree. It is unused, but it did not arrive
  with the news subsystem and removing it is a separate, unrelated cleanup.
  Not touched by this slice.
- `pyproject.toml`'s `[project.description]` field ("manta.digital news and
  market data management utilities") is stale relative to this slice's outcome
  but is a one-line packaging metadata fix outside a code-removal slice's
  scope. Left as a follow-up note (see Implementation Notes).
- Any new news capability or provider. Out of scope by design — see Value.

## Dependencies

### Prerequisites
None. Slice plan lists no dependencies, and verification below confirms the
subsystem is already fully non-functional and isolated.

### Interfaces Required
None. Nothing in this slice needs anything from another slice.

## Architecture

### Component Structure

Everything being removed lives in exactly two directories plus their tests:

```
src/manta_trading/news/
├── __init__.py
├── news.py                       # command dispatch, NewsCommandOptions — the stub entry point
├── newsdb.py                     # NewsDB — Mongo/motor-backed storage
├── newsdbmigrationutility.py     # NewsDbMigrationUtility
├── newsfields.py                 # field/schema constants
├── newsservice.py                # NewsService
└── newsutility.py                # helpers

src/manta_trading/agents/
└── newsagent.py                  # NewsAgent — imported by news.py, nothing else
```

`news.py` imports `NewsAgent` from `agents/newsagent.py`; that is the only
cross-directory edge, and both sides are deleted together. No file outside
these two locations imports anything from either.

### Data Flow

N/A — this is a deletion. There is no data flow to preserve; the subsystem's
DB handle is already `None` at runtime and no write path is reachable.

## Migration Plan

This is a removal, not a move — "migration" here means the mechanical
deletion and verification that nothing is left dangling.

### Source files being deleted (7 modules, 1,546 lines)
- `src/manta_trading/news/__init__.py`
- `src/manta_trading/news/news.py`
- `src/manta_trading/news/newsdb.py`
- `src/manta_trading/news/newsdbmigrationutility.py`
- `src/manta_trading/news/newsfields.py`
- `src/manta_trading/news/newsservice.py`
- `src/manta_trading/news/newsutility.py`
- `src/manta_trading/agents/newsagent.py`

### Test files being deleted (8 files, 1,008 lines)
- `test/unit/testnews.py`
- `test/unit/testnewsdb.py`
- `test/unit/testnewsdbmigration.py`
- `test/unit/testnewsagent.py`
- `test/unit/testnewsservice.py`
- `test/integration/testnewsdbmigrationintegration.py`
- `test/integration/testnewsdbintegration.py`
- `test/integration/testNewsIntegration.py`

### Consumers requiring updates
None found. Verified by grep across `src/` and `test/`, case-insensitive, for
`news` outside the paths above: the only hit is
`test/unit/test_chunking_strategy.py`, which uses the literal string
`"NEWSTOCK"` as an arbitrary test symbol — unrelated to the news subsystem,
not touched by this slice. No references from `cli/`, `api_server/`, or
`config/`; no `NEWS_DB` settings key; no entry in `.env_sample`.

### Dependency removal
- `pymongo>=4.9.2` — remove from `[project] dependencies`.
- `motor>=3.6.0` — remove from `[project.optional-dependencies] dev`.
- Regenerate `uv.lock` (`uv lock`) so the lockfile matches the pruned
  dependency set; commit both files together.
- `chromadb` stays — confirmed unused but out of scope (see Technical Scope).

### Verification that behavior is preserved
There is no behavior to preserve — the subsystem is already non-functional
(the stub raises unconditionally on every entry point). Verification instead
confirms complete removal with no collateral damage:
- `grep -ri news src/ test/` returns nothing outside git history (the
  `NEWSTOCK` string in `test_chunking_strategy.py` is expected and unrelated).
- `uv sync` resolves cleanly without `pymongo`/`motor` in the tree.
- Full test suite (`ruff`, `mypy`, unit, integration per subpackage) passes
  with the same or fewer failures than baseline, and specifically 4 fewer
  integration failures.
- `mt --help` and all existing subcommands behave identically — nothing in
  `cli/` referenced the news subsystem, so no CLI surface changes.

## Integration Points

### Provides to Other Slices
None — nothing downstream depends on this deletion completing.

### Consumes from Other Slices
None.

## Success Criteria

### Functional Requirements
- `src/manta_trading/news/` and `src/manta_trading/agents/newsagent.py` no
  longer exist.
- The 8 listed test files no longer exist.
- `pymongo` and `motor` no longer appear in `pyproject.toml` or `uv.lock`.
- `mt` CLI behavior is unchanged (no command referenced the subsystem).

### Technical Requirements
- `grep -ri news src/ test/` returns no hits outside
  `test_chunking_strategy.py`'s unrelated `"NEWSTOCK"` string.
- `uv sync` succeeds with no `pymongo`/`motor` resolution.
- `ruff check src/ test/` and `mypy` (per the current project gate) show no
  new violations introduced by the deletion (fewer files, so likely fewer
  violations).
- Per-subpackage test suites (unit, integration, load) all pass, matching or
  improving on the pre-slice baseline.

### Integration Requirements
- Integration tier failure count drops by exactly 4 relative to the
  pre-slice baseline, with no new failures introduced.
- The 30-second pytest timeout hazard attributable to the `pymongo` server-RTT
  background thread no longer exists in any test run.

### Verification Walkthrough

Verified 20260809 against the actual implementation (commits cda10b7,
c77f450, 879b1d6 on branch `914-slice.remove-the-alphavantage-era-news-subsystem`).
Pre-slice baseline was captured first (task 1.1) so each step below compares
against a recorded number rather than an assumed one.

1. **Confirm the subsystem is gone:**
   ```
   find src/manta_trading/news src/manta_trading/agents/newsagent.py
   ```
   Expect: `find: ... No such file or directory` (BSD/macOS `find`; GNU `find`
   emits the same message with a different leading token) for both paths.
   Confirmed.

2. **Confirm no dangling references:**
   ```
   grep -ri news src/ test/ --include="*.py"
   ```
   Expect: only `test/unit/test_chunking_strategy.py` lines containing the
   literal `"NEWSTOCK"` test symbol — nothing else. Confirmed (5 matching
   lines, all `NEWSTOCK`).

3. **Confirm dependency removal:**
   ```
   grep -i "pymongo\|motor" pyproject.toml uv.lock
   ```
   Expect: no matches (exit code 1). Then:
   ```
   uv sync --extra dev
   ```
   Expect: clean resolution, no errors. Confirmed — `uv lock` reported
   "Removed dnspython v2.8.0 / Removed motor v3.7.1 / Removed pymongo
   v4.16.0"; `uv sync --extra dev` resolved 105 packages with no errors.

4. **Confirm the test suite is cleaner, not just smaller:**
   Run `uv run --extra dev pytest test/unit/ -q` and
   `uv run --extra dev pytest test/integration/ -q`. Compare against the
   pre-slice baseline captured in task 1.1 (not the 913 wrap-up estimate,
   which task 1.1 superseded with an exact count): baseline was unit 1892
   passed/45 skipped/0 failed, integration 12 passed/6 failed/250
   skipped/2 errors (4 news-related + 2 unrelated `test_cli_lists.py`
   failures). Post-removal actual: unit 1868 passed/45 skipped/0 failed
   (24 fewer tests, matching the 5 deleted unit test files, no new
   failures); integration 3 passed/2 failed/255 skipped/0 errors — exactly
   the baseline minus the 4 news failures and minus the 2 errors (both were
   on now-deleted news tests), leaving only the pre-existing unrelated
   `test_cli_lists.py::test_lists_ls_includes_priority1` and
   `test_cli_lists.py::test_lists_show_priority1_emits_ten_symbols`
   failures. Confirmed, no new failures anywhere.

   Also confirmed (task 4.3): load tier (`MT_RUN_LOAD_TESTS=1 uv run --extra
   dev pytest test/load/ -q`) — 6 passed, 7 skipped, unchanged from
   pre-slice (no news tests exist in this tier).

5. **Confirm the CLI is unaffected:**
   ```
   mt --help
   ```
   Expect: identical output to before the slice — no news-related subcommand
   existed, so none disappears. Confirmed via
   `diff /tmp/mt-help-before.txt /tmp/mt-help-after.txt` (captured
   before/after the slice per task 1.1 / task 4.5): zero-length diff.

**Caveat discovered during implementation:** ruff (1788→1460) and mypy
(93 errors/19 files/149 files→90 errors/17 files/141 files) counts both
dropped along with the deleted files, as expected since the deleted files
themselves carried violations — this is a byproduct of deletion, not a
verification target in its own right, but confirms no orphaned-import
regressions were introduced elsewhere in the tree.

## Implementation Notes

### Development Approach
Single-pass mechanical deletion:
1. Delete the 8 source files (news/ + newsagent.py).
2. Delete the 8 test files.
3. Remove `pymongo` and `motor` from `pyproject.toml`; run `uv lock` to
   refresh `uv.lock`; commit both.
4. Run the full verification walkthrough above.
5. Run `ruff`/`mypy` to confirm no orphaned imports or references remain
   (there should be none, since nothing outside the deleted paths referenced
   them).

No intermediate state is needed — unlike a refactor, there are no consumers
to repoint, so this does not require staged commits to keep the tree working
throughout. A single commit deleting both source and tests together is
correct; splitting further adds no safety.

### Special Considerations
- **Stale packaging metadata.** `pyproject.toml`'s `[project.description]`
  still reads "manta.digital news and market data management utilities."
  Fixing it is a one-line, unrelated packaging edit — flagged here as a
  follow-up note rather than pulled into this slice's scope, per the
  slice-plan's effort-1 sizing. If the Project Manager wants it folded in,
  it's a trivial addition to the same commit.
- **`__pycache__` artifacts.** The directories being deleted have compiled
  `.pyc` files checked into the working tree under `__pycache__/` (not
  git-tracked, but present on disk). A plain recursive delete of the parent
  directories removes them; no special handling needed.
