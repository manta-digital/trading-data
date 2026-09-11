---
docType: slice-design
slice: operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [919, 921]
interfaces: []
effort: 2
dateCreated: 20260911
dateUpdated: 20260911
status: not_started
---

# Slice Design: Operator Overview (922) — one screen for "what is running, what ran, what is fresh"

## Overview

On 2026-09-11 the Project Manager's three routine questions — is a fetch
running now and how far along, when did each pass last run and how did it end,
and is the data fresh — had no answer from `mt-run`. The answers lived only in
`systemctl list-timers`, `systemctl list-units` and `journalctl -u mt-*`,
because a pass's start, end, phase progress and outcome (`MinutePassOutcome`,
slice 921) exist only as journal lines. `mt data health` judges, `mt data
kalshi status` covers one source, and `mt data status` prints a per-symbol
table whose footer counts terminal holes as gaps and whose STALE rule is a
one-day literal that, under the weekly minute cadence (`MT_MINUTE_FIRING_DAYS`,
0.14.7), flags every active minute symbol six days a week.

This slice makes pass runs a persisted fact (one row per firing, updated in
flight), adds `mt data overview` as a single read-only screen over that fact
plus the newest-bar and credit facts, turns `mt data status` into a summary by
default, and makes the `data_status` view's staleness follow the firings that
actually happened instead of a literal.

## Value

- **Operator**: one command, no arguments, under ten seconds, answers all
  three questions on manta9000. No `systemctl`, no `journalctl`.
- **Correctness of existing screens**: `mt data status` stops reporting
  provider holes as gaps and stops reporting the whole minute universe as STALE
  on non-firing days.
- **Architectural**: pass outcomes become data. The health check, the overview
  and any future alerting read the same row instead of re-deriving state from
  logs or systemd.

## Technical Scope

**In scope**

1. `pass_runs` table (migration 055, minute track) and a `PassRunRepository`
   written by the minute/daily runner, the Kalshi collection pass, the health
   command and the accounting command.
2. `mt data overview` (new module `cli/commands/overview.py`, renderer
   `cli/rendering/overview.py`).
3. `mt data status`: summary by default, `--detail` restores today's per-symbol
   table; footer separates "still asking" from holes and exhausted.
4. `data_status` view: `gap_count` counts open gaps only; STALE is judged
   against the last completed universe walk recorded in `pass_runs`.
5. One outcome vocabulary (`PassRunOutcome`) shared by every writer, the
   overview, and the health/status footers.
6. Firing schedule constants for the daily, Kalshi and health timers, guarded
   by the existing unit-file drift test, so the overview can print the next
   firing without reading systemd.
7. `mt-accounting.timer` (daily) so the cached universe line is refreshed
   without a human running a 90-second query.

**Out of scope**

- Any change to what the passes fetch or how they decide outcomes (921 owns
  that). This slice only records what 921 already computes.
- Alerting, mail, or a web view. `mt-run status` keeps its systemd lines; the
  overview does not call systemd.
- Retention for `pass_runs` (about sixty rows a day; revisit if it matters).
- `mt data pull` / explicit-scope `daemon run --symbols` invocations do not
  write run rows (Decision 3).

## Dependencies

### Prerequisites
- 919: `mt data health` and its hourly timer (the health row's writer).
- 921: `MinutePassOutcome`, `CycleReport.minute_trailing_completed` /
  `minute_trailing_required`, `MinutePassPhase`, `minute_pass_exit_code`,
  `MINUTE_SEED_PROGRESS_LOG_INTERVAL`, `minute_firing_schedule.py`,
  `mt data accounting` and `summary_line`.

### Interfaces Required
- `Runner._loop()` (`data/acquisition/daemon/runner.py`) around the daily and
  minute cycle calls: the only place that knows a cycle began and how it ended.
- `run_minute_cycle` / `_run_minute_phase` (`daemon/minute.py`): a progress
  callback threaded like the existing `on_symbol` callback.
- `CollectionPass.run()` (`data/kalshi/collection_pass.py`): already produces
  `PassResult` with `run_id`, `started_at`, per-phase reports and an outcome.
- `data_health` (`cli/commands/health.py`) and `data_accounting`
  (`cli/commands/accounting.py`): write their own row at start and end.
- `_build_data_status_view_sql` (`market/schema/migrations/minute.py`) and the
  view-SQL contract tests.
- `mt-run` (`deploy/mt-run`): unchanged; `mt-run data overview` works through
  its catch-all.

## Architecture

### Component Structure

```
writers                                   readers
-------                                   -------
Runner._loop  ──┐                         mt data overview ──┐
CollectionPass ─┤─► PassRunRepository ─► pass_runs ◄─────────┤
data_health ────┤     (open/progress/close)      ▲            │
data_accounting ┘                                │            │
                                                 │            │
data_status view (STALE anchor = last ended universe walk)    │
                                                 ▲            │
mt data status  ─────────────────────────────────┴────────────┘
```

- `data/acquisition/pass_runs.py` — `PassKind`, `PassRunOutcome`, `PassRun`
  dataclass, `PassRunRepository` (same shape as `HeartbeatRepository`:
  `_COLS`, row mapper, psycopg3 raw SQL, `ON CONFLICT (run_id)`).
- `data/acquisition/daemon/pass_run_recorder.py` — `PassRunRecorder`: the
  in-process object a writer holds; `open()`, `progress()`, `close()`. It owns
  the "close orphans first" rule (Decision 4) and never raises into the pass:
  a failed write logs at ERROR with `logger.exception` and the pass continues
  (recording must never abort a fetch).
- `firing_schedule.py` (rename/extend of `minute_firing_schedule.py`):
  `FiringSchedule(times_utc, weekdays)` per pass kind; `next_firing_at`.
  `MINUTE_PASS_FIRING_TIMES_UTC` stays where it is; siblings are added in
  `constants.py` for daily (`00:35`, `12:35`), Kalshi (hourly `:20`), health
  (hourly `:50`), accounting (Decision 9).
- `cli/commands/overview.py` — `gather()` (all I/O, one DB connection plus one
  HTTPS call), pure `build_overview()`, `data_overview` Typer command.
- `cli/rendering/overview.py` — text and JSON rendering.
- `provider/eodhd/account.py` — `fetch_credit_usage(http, api_key) ->
  CreditUsage(used, daily_limit, extra)` from the `user` endpoint (the
  function slice 921 removed from health, revived as a report, not a check).

### Data Flow

1. A timer fires a pass. The writer calls `recorder.open(kind, walk_anchor_at)`;
   the row exists with `ended_at IS NULL` before the first provider call.
2. Minute pass: every `MINUTE_SEED_PROGRESS_LOG_INTERVAL` symbols the cycle
   calls `on_progress(phase, done, total)`; the recorder writes `phase`,
   `progress_done`, `progress_total`, `progress_updated_at`. Kalshi: phase
   boundaries update `phase` (per-symbol counts inside a Kalshi phase are not
   tracked; the phase name and its elapsed time are enough). Daily: `phase`
   is `daily`, progress = symbols attempted of pending.
3. The pass ends. The writer maps its native result to `PassRunOutcome`
   (Decision 2), sets `ended_at`, `outcome`, `exit_code`, `detail`.
4. `mt data overview` reads: the open row per kind (running now), the latest
   ended row per kind (last run), `settings.minute_firing_days` and the
   schedule constants (next firing and cadence), newest bar per source,
   the latest health row's `detail` (verdict line), the latest accounting
   row's `detail` (universe line), and EODHD credit usage.
5. `data_status` view: STALE compares `acquisition_state.last_attempt_ts`
   with the `started_at` of the latest ended `pass_runs` row of the same
   granularity's latest ended walk (`walk_anchor_at`, Decision 6).

## Technical Decisions

1. **Pass runs are rows, written by the process that runs the pass.** Not
   parsed from the journal, not read from systemd. `daemon_heartbeat` is not
   reused: it holds one row per daemon id, upserted on transitions, and is not
   a history. The overview reads only the database, so it works from any host
   with the URL and never needs root.

2. **One outcome vocabulary, `PassRunOutcome`**, defined once and rendered into
   the table's CHECK constraint the way `FetchStatus` is:
   - `COMPLETE` — the pass did everything the firing required.
   - `COMPLETE_QUOTA` — required work done, optional work ended on quota
     (minute: trailing complete, backfill hit the allowance; a backfill-only
     day that spends the whole allowance is also `COMPLETE_QUOTA`).
   - `INCOMPLETE` — required work not finished, no crash (minute trailing cut
     off by quota; Kalshi `PARTIAL`; daily stopped by `should_continue` or the
     credit ceiling with symbols still pending).
   - `PROVIDER_UNAVAILABLE` — the provider could not be reached (minute
     `PROVIDER_UNAVAILABLE`, Kalshi `PROVIDER_ABORT`).
   - `FAILED` — an exception or storage failure ended the pass (runner's
     `report is None` path, Kalshi `STORAGE_ABORT`, health/accounting exit 2),
     or the row was found open by a later firing (Decision 4).
   The mapping from `MinutePassOutcome` × `trailing_completed` ×
   `trailing_required`, from `SyncOutcome`, and from the daily report lives in
   one function per source next to `minute_pass_exit_code`. The health verdict
   ("healthy" / "UNHEALTHY: n of m checks failing") is not an outcome: the
   health pass's outcome is `COMPLETE` when the checks ran, and the verdict
   goes in `detail`. This is what makes "complete (quota)" a result and not a
   failure on every screen.

3. **Only universe passes write rows.** The runner records a cycle only when
   its scope is `SCOPE_ALL_ACTIVE`; explicit `--symbols` / `--list`
   invocations and `mt data pull` are operator experiments and would pollute
   "last run". A `--forever` runner writes one row per cycle, which is the same
   thing a timer firing writes.

4. **An open row from a dead process is closed by the next writer of the same
   kind**, with outcome `FAILED` and `detail = "abandoned: found open at next
   firing"`. Power loss (2026-09-11 10:26 UTC) and SIGKILL cannot write an end.
   The overview shows a running row with the age of its last progress update,
   so a wedged pass reads "RUNNING trailing 6,750/13,083, progress 3 h ago"
   rather than pretending to be healthy.

5. **Daily gains an outcome.** `run_daily_cycle` sets a new
   `CycleReport.daily_pass_completed` (true when the pending list was walked
   to the end) so the daily row can say `COMPLETE` versus `INCOMPLETE`. The
   daily exit code is unchanged in this slice (it is always 0 today; changing
   it is a 921-class decision and is not needed to answer the PM's questions).

6. **STALE follows the walks that happened, not a configured literal.** A
   run that attempts every active symbol of its granularity carries a
   `walk_anchor_at`: the instant "attempted in this walk" is measured from.
   Minute: `started_at` of a firing whose trailing phase was required. Daily:
   the pass boundary the cycle already uses (UTC midnight plus
   `DAILY_CYCLE_START_OFFSET`), because the 12:35 firing only resumes symbols
   the 00:35 firing did not reach and must not re-anchor them. A symbol is
   STALE when its `last_attempt_ts` is older than the greatest
   `walk_anchor_at` among *ended* rows for its granularity. After a walk every
   attempted symbol is fresh and every unattempted one is stale, which is
   exactly the signal, including a walk cut short by quota. Until the first
   recorded walk the anchor is NULL and nothing reads STALE; the view comment
   says so. Reading `MT_MINUTE_FIRING_DAYS` into the database was rejected:
   the view would then have to be re-created whenever the env file changed.

7. **`gap_count` means open gaps.** The view's `gap_counts` CTE counts
   `UNKNOWN` and `FAILED_RETRYABLE` rows only ("still asking"); `FAILED` still
   comes from `RETRY_EXHAUSTED`; `PROVIDER_HOLE` rows are terminal answers and
   are not gaps. Column names and types are unchanged, so `CREATE OR REPLACE
   VIEW` applies and the D2 column contract holds; the API response model's
   `gap_count` docstring is updated to say open gaps. The `mt data status`
   footer adds a second line from `data_gaps` directly: still asking / holes /
   exhausted totals per granularity.

8. **Credits come from EODHD's account endpoint, shown as used against
   `EODHD_DAILY_QUOTA`.** `apiRequests` is *used*. One call, five-second
   timeout; on any failure the line reads `EODHD credits: unavailable (<reason>)`
   and the command still exits 0. No threshold — the health check dropped the
   quota floor in 921 for a reason.

9. **The universe line is the last accounting row.** `mt data accounting`
   opens and closes a `pass_runs` row of kind `accounting` with
   `detail = summary_line(rows)`. `mt-accounting.timer` runs it daily at
   16:30 UTC (after the minute pass has had its 13:05 firing and the daily
   pass its 12:35); the overview prints the line with its `ended_at`. If no row
   exists the line says so and names the command.

10. **Next firing is computed from constants that are tested against the unit
    files.** `test/unit/deploy/test_units.py` already asserts the timers'
    `OnCalendar`; the new constants are asserted the same way. The overview
    never shells out.

11. **`mt data status` becomes a summary by default; `--detail` is the old
    table.** The summary is the SOURCES block and the two footer lines. All
    existing filters (`--symbol`, `--health`, `--daily`, `--minute`, `--all`,
    `--json`) imply `--detail`; JSON output keeps its current shape plus the
    new footer counts. Scripts that relied on the default table get the same
    table with `--detail`; the `test_cli_data` tests are updated accordingly.

## Implementation Details

### Database / Storage Schema

Migration `055_create_pass_runs` (minute track; SQL idempotent per the
migrations README):

```sql
CREATE TABLE IF NOT EXISTS pass_runs (
    run_id              UUID        PRIMARY KEY,
    pass                TEXT        NOT NULL,   -- PassKind: minute|daily|kalshi|health|accounting
    walk_anchor_at      TIMESTAMPTZ,            -- set when the run attempted every active symbol:
                                                --   the instant "attempted in this walk" is measured from
    started_at          TIMESTAMPTZ NOT NULL,
    ended_at            TIMESTAMPTZ,
    phase               TEXT,                   -- MinutePassPhase / PassPhaseName / 'daily'
    progress_done       INTEGER,
    progress_total      INTEGER,
    progress_updated_at TIMESTAMPTZ,
    outcome             TEXT,                   -- PassRunOutcome, NULL while running
    exit_code           INTEGER,
    detail              TEXT,                   -- one-line summary: counts, verdict, universe line
    CONSTRAINT pass_runs_pass_check    CHECK (pass IN (...rendered from PassKind...)),
    CONSTRAINT pass_runs_outcome_check CHECK (outcome IS NULL OR outcome IN (...PassRunOutcome...)),
    CONSTRAINT pass_runs_ended_check   CHECK ((ended_at IS NULL) = (outcome IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_pass_runs_pass_started ON pass_runs (pass, started_at DESC);
```

Migration `056_data_status_view_open_gaps_and_walk_anchor` re-issues the view
(`CREATE OR REPLACE`, no column change, no CASCADE) with:

```sql
gap_counts AS (
    SELECT symbol, granularity, COUNT(*) FILTER (WHERE fetch_status IN ('UNKNOWN','FAILED_RETRYABLE')) AS gap_count,
           BOOL_OR(fetch_status = 'RETRY_EXHAUSTED') AS has_retry_exhausted
    FROM data_gaps GROUP BY symbol, granularity),
walk_anchor AS (
    SELECT pass, MAX(walk_anchor_at) AS anchor FROM pass_runs
    WHERE walk_anchor_at IS NOT NULL AND ended_at IS NOT NULL GROUP BY pass)
...
WHEN ast.last_attempt_ts IS NULL
     OR ast.last_attempt_ts < wa.anchor THEN 'STALE'   -- NULL anchor: never STALE
```

`MINUTE_STALENESS_THRESHOLD` / `DAILY_STALENESS_THRESHOLD` and
`_interval_literal` lose their only consumer and are deleted; the view comment
is re-rendered to document the anchor rule. Existing rows in `data_gaps` are
untouched; the four pre-rendered view variants are rebuilt by the same
builder, so 021's apply-time variant selection keeps working.

### CLI Specification

`mt data overview [--json]` — no other options. Exit 0 whenever the database
answered; exit 2 (`EXIT_UNAVAILABLE`, shared with health) when it did not.

```
manta-trading overview                                        2026-09-12 16:10 UTC

PASSES   cadence        now                                   last run
minute   Sat            idle                                  Sat 09-12 13:05–15:41  complete (quota)  exit 0
                                                              trailing 13,083/13,083 · backfill 4,120 symbols
daily    00:35, 12:35   idle                                  Sat 09-12 12:35–12:52  complete          exit 0
kalshi   hourly :20     RUNNING candles (since 16:20, progress 40 s ago)
                                                              Sat 09-12 15:20–15:31  complete          exit 0
health   hourly :50     idle                                  Sat 09-12 15:50        complete  healthy
next     minute Sat 09-19 13:05 · daily Sun 09-13 00:35 · kalshi 17:20 · health 16:50

SOURCES          newest                       health (15:50 UTC): healthy
minute bars      2026-09-11 20:00 UTC (20 h)
daily bars       2026-09-11
kalshi candles   2026-09-12 16:05 UTC
kalshi trades    2026-09-12 16:09 UTC

EODHD credits    65,210 / 100,000 used today (extra 0)
minute universe  (accounting 09-12 16:31 UTC) 11,595,172/13,637,498 symbol-sessions covered (85.0%);
                 438,299 untraded; 1,604,027 fillable (hole 500,716, unknown 892,063, untracked 211,213, exhausted 35)
```

Rules: outcomes render lower-case with `COMPLETE_QUOTA` as `complete (quota)`;
a running row shows phase, `done/total` when known, start time and the age of
the last progress write; a `FAILED` last run adds its `detail` on the second
line; `never run` when no row exists for a kind. `--json` emits the gathered
facts as one object (`passes`, `sources`, `credits`, `universe`, `now`).

`mt data status` (default) prints the SOURCES block plus:

```
minute   OK 12,347   GAPS 1,806   STALE 0   FAILED 107      still asking 892,063 · holes 500,716 · exhausted 35
daily    OK 12,900   GAPS 12     STALE 38  FAILED 0        still asking 14 · holes 0 · exhausted 0
```

`mt data status --detail [...existing options]` is today's table with the new
footer.

### Writers

| writer | open | progress | close |
|---|---|---|---|
| `Runner._loop` minute cycle | before `_run_minute_cycle`; `walk_anchor_at = started_at` when `is_firing_day` says trailing is required, else NULL | `on_progress` from `_run_minute_phase` every 250 symbols | from `CycleReport` via `pass_run_outcome_for_minute(...)`; `exit_code = minute_pass_exit_code(...)`; detail: trailing n/N, backfill n |
| `Runner._loop` daily cycle | before `_run_daily_cycle`; `walk_anchor_at` = the cycle's pass boundary | attempted/pending | `COMPLETE` if `daily_pass_completed` else `INCOMPLETE`; exception → `FAILED` |
| `CollectionPass.run` | at start with the existing `run_id` | phase name at each phase start | `pass_run_outcome_for_kalshi(result.outcome)`; `exit_code = EXIT_BY_OUTCOME[...]`; detail: per-phase outcomes |
| `data_health` | at start | — | `COMPLETE` + verdict line in detail, exit 0/1; unavailable → `FAILED`, exit 2 |
| `data_accounting` | at start | — | `COMPLETE` + `summary_line` in detail |

The recorder is constructed by the CLI layer with the same connection factory
the runner already receives; the cycle functions never see it (they see a
callback), keeping `run_minute_cycle` free of a new dependency.

### systemd

`deploy/systemd/mt-accounting.service` / `.timer` (oneshot, `mt data
accounting`, `OnCalendar=*-*-* 16:30:00 UTC`, `Persistent=true`,
`TimeoutStartSec=900`), installed by `install-production.sh` like the health
units; `mt-run status` gains no line (the overview is the surface).

## Integration Points

### Provides
- `pass_runs` and `PassRunRepository` for any later alerting or web view.
- `PassRunOutcome` as the one outcome vocabulary.
- `FiringSchedule` constants per pass for anything that needs "next firing".

### Consumes
- 921's outcome and phase enums unchanged. If 921 adds an outcome, the
  minute mapping's exhaustiveness assert fails the unit tier, as
  `_MINUTE_EXIT_BY_OUTCOME` does today.

## Success Criteria

1. After one firing of each timer on manta9000, `mt-run data overview` prints
   the screen above with a real last-run line for minute, daily, kalshi and
   health, in under ten seconds, from a DB-only read plus one EODHD call.
2. While a pass is running, the overview shows `RUNNING` with phase and
   progress for that pass; after it ends, the row shows start, end, outcome
   and exit code, and the outcome for a minute pass that finished trailing and
   hit the allowance is `complete (quota)`, exit 0.
3. A row left open by a killed process is closed `FAILED` by the next firing
   of the same kind; before that, the overview shows it as running with its
   last-progress age.
4. `mt data status` prints the summary by default and the old table under
   `--detail`; the footer's GAPS count excludes `PROVIDER_HOLE` rows and the
   second footer line reports still-asking / holes / exhausted per
   granularity, agreeing with `SELECT fetch_status, count(*) FROM data_gaps`.
5. On a non-firing day for minute, active minute symbols attempted in the
   last Saturday walk read OK or GAPS, not STALE; a symbol not attempted in
   that walk reads STALE; a daily symbol attempted at 00:35 is not STALE after
   the 12:35 resume. Verified by an integration test over `migrated_db` that
   inserts `pass_runs` and `acquisition_state` rows for both cases.
6. The view still exposes exactly the D2 column list; `test_data_status_view_sql`
   and `test_data_status_view` pass with the new CTEs.
7. Unit tests: outcome mappings for all three sources (exhaustive over the
   source enums), the recorder's orphan-closing rule, `next_firing_at` for
   hourly and twice-daily schedules, `build_overview` rendering for running /
   idle / never-run / failed rows, and the credit line's unavailable branch.
8. Timer constants for daily, kalshi, health and accounting are asserted
   against the unit files in `test/unit/deploy/test_units.py`.
9. CHANGELOG entry; the operator overview is documented in the README's
   operations section next to `mt data health`.

## Verification Walkthrough

Development (against `MT_TIMESCALE_TEST_URL`):

```
python scripts/run_tests.py unit
python scripts/run_tests.py integration -- -k "pass_runs or data_status or overview"
```

Host, after `deploy/install-production.sh --ref vX.Y.Z` and `mt data migrate
apply` (both already sudoers-allowed):

1. `mt-run data overview` — every pass reads `never run`; sources and credits
   are live; the universe line names `mt data accounting`.
2. `sudo mt-run kalshi` in one terminal; in another, `mt-run data overview`
   shows `kalshi RUNNING catalog|candles|trades (since HH:MM, progress N s ago)`.
   When it ends, the line becomes `HH:MM–HH:MM complete exit 0`.
3. After the 16:30 UTC accounting firing, the overview's universe line carries
   that timestamp; `mt-run data accounting` refreshes it on demand.
4. After the next Saturday 13:05 UTC minute firing: `minute` reads
   `complete` or `complete (quota)` with `trailing 13,083/13,083`;
   `mt-run data status` shows minute `STALE 0` (or the count of symbols the
   walk did not reach); `mt-run data status --detail --minute --health STALE`
   lists exactly those.
5. Kill a running pass with `sudo systemctl kill -s KILL mt-kalshi-pass.service`
   (test host only; PM's call on production). The overview shows the row
   running with a growing progress age; after the next :20 firing it reads
   `FAILED abandoned: found open at next firing`, and the new run is the last
   run.
6. `mt-run data status` default output fits one terminal screen; `--detail`
   prints the table the PM saw before this slice, plus the second footer line.

## Implementation Notes

Suggested order: (1) `PassKind` / `PassRunOutcome` / migration 055 /
repository / recorder with unit and integration tests; (2) writers, minute
first (the progress callback), then daily, Kalshi, health, accounting;
(3) schedule constants and drift tests; (4) overview gather / build / render;
(5) migration 056 and the status footer; (6) `mt data status` default flip;
(7) accounting units, CHANGELOG, README. Commit at least once per numbered
step. Every step leaves `main` deployable: rows are additive, and until 056
lands the view behaves as today.

The overview must stay under ten seconds: newest-bar queries use the
`max(time)` pattern from `check_raw_freshness`; the session-mass and cagg
checks are never re-run — their verdict is read from the health row.
