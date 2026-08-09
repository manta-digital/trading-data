---
docType: slice-design
slice: remove-the-alphavantage-era-news-subsystem
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: []
interfaces: []
dateCreated: 20260808
dateUpdated: 20260808
status: not_started
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

1. **Confirm the subsystem is gone:**
   ```
   find src/manta_trading/news src/manta_trading/agents/newsagent.py
   ```
   Expect: "No such file or directory" for both paths.

2. **Confirm no dangling references:**
   ```
   grep -ri news src/ test/ --include="*.py"
   ```
   Expect: only `test/unit/test_chunking_strategy.py` lines containing the
   literal `"NEWSTOCK"` test symbol — nothing else.

3. **Confirm dependency removal:**
   ```
   grep -i "pymongo\|motor" pyproject.toml uv.lock
   ```
   Expect: no matches. Then:
   ```
   uv sync
   ```
   Expect: clean resolution, no errors.

4. **Confirm the test suite is cleaner, not just smaller:**
   Run the project's per-subpackage suite (unit, then integration, per the
   existing `scripts/run_tests.py` invocation pattern). Compare the
   integration failure count against the pre-slice baseline recorded in the
   913 wrap-up (6 pre-existing failures: 4 news-related + 2 unrelated
   `testcli_lists.py` failures). Expect exactly 2 remaining (the unrelated
   ones), and no new failures.

5. **Confirm the CLI is unaffected:**
   ```
   mt --help
   ```
   Expect: identical output to before the slice — no news-related subcommand
   existed, so none disappears.

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
