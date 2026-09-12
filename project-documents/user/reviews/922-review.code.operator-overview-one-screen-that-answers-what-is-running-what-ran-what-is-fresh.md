---
docType: review
layer: project
reviewType: code
slice: operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/slices/922-slice.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md
aiModel: z-ai/glm-5.3
status: complete
resolution: addressed
resolvedSha: c39539a
resolvedDate: 20260912
priorReview: archive/922-review.code.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md
dateCreated: 20260912
dateUpdated: 20260912
reviewedSha: 64575d671b9820d4eb704f3864642bc00e587248
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 39
findings:
  - id: F001
    severity: concern
    category: performance
    summary: "Overview's \"database gets the other half\" budget is not enforced — the DB connect timeout is 10s, not 5s"
    location: "src/manta_trading/constants.py#EODHD_ACCOUNT_TIMEOUT_SECONDS"
  - id: F002
    severity: note
    category: duplication
    summary: "The \"open gap\" status set is defined twice — in the SQL view builder and the footer renderer"
    location: "src/manta_trading/cli/rendering/status_table.py#render_gap_status_line"
  - id: F003
    severity: note
    category: error-handling
    summary: "gather's credit-call guard swallows any exception and logs without a traceback"
    location: "src/manta_trading/cli/commands/overview.py:219"
  - id: F004
    severity: note
    category: design
    summary: "`getattr(settings, \"minute_firing_days\", None)` conflates a missing attribute with \"every day\""
    location: "src/manta_trading/cli/commands/overview.py:196"
  - id: F005
    severity: note
    category: duplication
    summary: "`next_minute_firing_at` duplicates the search loop of `next_firing_at`"
    location: "src/manta_trading/firing_schedule.py#next_minute_firing_at"
  - id: F006
    severity: note
    category: error-handling
    summary: "Daemon recorder pool is bounded on connect but not on checkout"
    location: "src/manta_trading/cli/commands/_pass_run.py#make_pass_run_recorder"
  - id: F007
    severity: pass
    category: testing
    summary: "Recording, migration, and drift-guard design is consistently enforced rather than aspirational"
    location: "src/manta_trading/data/acquisition/daemon/pass_run_recorder.py"
---

# Review: code — slice 922

**Verdict:** PASS
**Model:** z-ai/glm-5.3

## Findings

### [CONCERN] Overview's "database gets the other half" budget is not enforced — the DB connect timeout is 10s, not 5s

`EODHD_ACCOUNT_TIMEOUT_SECONDS = 5.0` is documented as "Half of the overview's ten-second budget; the database gets the other half." But `data_overview` (src/manta_trading/cli/commands/overview.py#data_overview) connects with `connect_timeout=DB_CONNECT_TIMEOUT_SECONDS` imported from `manta_trading.data.kalshi.constants`, where I verified `DB_CONNECT_TIMEOUT_SECONDS = 10` (src/manta_trading/data/kalshi/constants.py:230). So the DB half is bounded at ten seconds, not five: a slow-but-successful connect plus queries plus the 5s credit call can exceed the design's ten-second total, and an unreachable database alone consumes the entire budget before any query runs. The load tier's assertion (`_DB_BUDGET_SECONDS + EODHD_ACCOUNT_TIMEOUT_SECONDS <= _TOTAL_BUDGET_SECONDS` in test/load/test_922_overview_nfr.py) checks constant arithmetic, not the connect timeout actually passed by the command, so the guard does not catch this. Either give the overview its own five-second connect timeout (or reuse `PASS_RUN_DB_CONNECT_TIMEOUT_SECONDS`), or correct the constant's docstring and the load test to match the real bound.

### [NOTE] The "open gap" status set is defined twice — in the SQL view builder and the footer renderer

`render_gap_status_line` hardcodes "still asking" as `FetchStatus.UNKNOWN + FetchStatus.FAILED_RETRYABLE`, while `data_status.gap_count` derives its open-gap predicate from `_open_gap_predicate()` in src/manta_trading/market/schema/migrations/minute.py. Both enumerate the same pair of enum members today, but the pairing lives in two places; if the definition of "open" ever changes in one, the footer's "still asking" line would disagree with the `gap_count` it sits directly beneath — precisely the drift CLAUDE.md's "define it once" rule exists to prevent. A shared tuple (e.g. `OPEN_FETCH_STATUSES`) consumed by both the migration renderer and the footer would close it.

### [NOTE] gather's credit-call guard swallows any exception and logs without a traceback

The `except Exception` around `fetch_credits` (with `# noqa: BLE001`) is justified by the overview's never-fail-on-the-credit-line contract, and the message is redacted — good. But `fetch_credit_usage` documents exactly what it raises (`httpx.HTTPError`, `KeyError`, `ValueError`), so the breadth mainly risks masking programming errors (an `AttributeError` inside a future version of the fetch would print "unavailable (…)" with only a one-line WARNING and no stack). Either narrow the catch to the documented types, or keep the breadth but log with `exc_info=True` so the journal carries the traceback for diagnosis.

### [NOTE] `getattr(settings, "minute_firing_days", None)` conflates a missing attribute with "every day"

In `FiringSchedule`, `weekdays=None` means *daily*. `gather` reads the setting with a `getattr(..., None)` default, so a settings object lacking the attribute would silently render the minute cadence as "13:05" (daily collecting) rather than erroring — a silent fallback of exactly the kind CLAUDE.md forbids ("Never use silent fallback values"). The real `Settings` always defines it, so today this only affects duck-typed test doubles, but a future rename would degrade to a plausible wrong line instead of a loud failure. Read `settings.minute_firing_days` directly (the signature is already `settings: Any`), or assert presence.

### [NOTE] `next_minute_firing_at` duplicates the search loop of `next_firing_at`

After the generalization, the same offset-day/times search algorithm exists twice in one file. `next_minute_firing_at` could delegate: `next_firing_at(after, FiringSchedule(times, weekdays))`. Low stakes — the loops are small and tested — but the generalization was the natural moment to collapse them.

### [NOTE] Daemon recorder pool is bounded on connect but not on checkout

`PASS_RUN_DB_CONNECT_TIMEOUT_SECONDS` is passed as `kwargs={"connect_timeout": ...}` only; psycopg_pool's `pool.connection()` checkout uses the pool's default `timeout` (30s). The recorder's "never aborts a pass" contract holds (a checkout timeout raises a pool error that the recorder's `psycopg.Error` catch handles), but on a database that accepts connections and then stalls, each `progress` write in the daemon's minute cycle can block the loop for up to that default before giving up. Consider `ConnectionPool(..., timeout=PASS_RUN_DB_CONNECT_TIMEOUT_SECONDS)` so the "gives up quickly" posture in the constant's docstring covers the checkout path too.

### [PASS] Recording, migration, and drift-guard design is consistently enforced rather than aspirational

The slice's invariants are mechanically guarded, which is what makes them real: every enum→value mapping (kalshi `EXIT_BY_OUTCOME`, `PASS_RUN_OUTCOME_BY_SYNC_OUTCOME`, minute `_MINUTE_PASS_RUN_OUTCOME`, the renderer's `_OUTCOME_TEXT`) is followed by an import-time exhaustiveness assert; the timer files are pinned to the constants by drift tests in test/unit/deploy/test_units.py (including "no firing the constant doesn't know about" in both directions, and the accounting-fires-after-collecting ordering); migration 055 is placed before 021 in list order with the position-critical comment, and 056 re-issues the view for already-migrated databases; failed rows are closed on every error path in health/accounting/kalshi (F005); recorder writes in the async Kalshi pass go through `asyncio.to_thread` (F003); the pool is closed in `finally` on the `--stop-when-done` path (F009); the API token is redacted at the raise site *and* in `gather` (F001); and the universe label is shared by writer and reader with a test fed by the real `summary_line` (F007). The load tier exists for the overview's budget per the project's load-test rule, and `MINUTE_UNIVERSE_LABEL` follows the `MINUTE_TRAILING_COMPLETE_LINE` precedent for single-definition values. This is the pattern the rest of the change should be judged against, and it holds up.

---

## Resolution

All six addressed in `c39539a`. Verdict was already PASS; none of these
blocked the gate. Unit tier 3430 passed / 0 failed, load tier 3 passed.

The prior round's review is at `archive/` (same filename). Comparing the
two, three of these six restate a theme from that round in a new location —
worth naming, because that is the signal that a fix was local rather than
general:

| re-review | round-1 theme it echoes | verdict |
|---|---|---|
| F002 open-gap set defined twice | F007 label used as logical structure — "define it once" | **real repeat**, different pair of call sites. Fixed the same way: one shared definition. |
| F003 blind catch without a traceback | F009 blind catch without a suppression | **real repeat, adjacent block.** Round 1 flagged `_pass_run.py` and I cited `overview.gather` as the correct precedent; round 2 held that precedent to a higher standard. Both now carry suppression *and* `exc_info`. |
| F004 `getattr` default as a silent fallback | F010 a claim asserted without evidence | same family (a plausible value standing in for a real one). Fixed. |

F001 is genuinely new and the only CONCERN: a documented budget that the
code did not enforce, with a load-tier guard that compared constants to each
other rather than to what the command passed — so the guard passed while the
bound was wrong. That is the finding worth remembering from this round.

Nothing was rejected. No finding contradicted a deliberate choice from the
first round; in particular the reviewer did not re-raise `TimeoutStartSec`,
and the PASS section confirms every round-1 fix by number.
