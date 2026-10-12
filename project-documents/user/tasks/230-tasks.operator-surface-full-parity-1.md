---
docType: tasks
slice: operator-surface-full-parity
project: trading-data
lld: user/slices/230-slice.operator-surface-full-parity.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [221, 225, 229, 922]
projectState: >
  Slices 220-229 are merged. trading_tick holds both adopted ES jobs, ingested
  and checked (trades 2024-08-30 → 09-29, tbbo 2024-11-02 → 2025-01-01), each
  with a quarterly roll. `status`, `coverage` and `active` answer what is held
  and which contract is active. Nothing hands an operator the ticks
  themselves, and accounting and overview do not know the tick tier exists.
  Slice design committed and reviewed (CONCERNS, all six findings fixed).
dateCreated: 20261012
dateUpdated: 20261012
status: not_started
---

# Tasks: Operator Surface Full Parity (part 1 of 2)

Part 1: preflight, constants and formatters, tick reads, the read plan, and
`mt data tick get`. Part 2
(`230-tasks.operator-surface-full-parity-2.md`): `debug`, the accounting and
overview lines, docs and closing checks.

## Context Summary

- Working on slice 230. It adds `mt data tick get` (ticks or bars, by contract
  or by product under a roll rule), `mt data tick debug {requests, units,
  definitions}`, a tick line in `mt data accounting`, a `futures ticks` line
  in `mt data overview`, and the runbook and contract rows for them. Read-only
  on every database. No migration, no schema change, nothing bought.
- The design (`user/slices/230-slice.operator-surface-full-parity.md`) holds
  every decision. Read TD1–TD11, Success Criteria and the Verification
  Walkthrough before starting. Tasks cite them (TDn, FRn = Functional
  Requirement n) rather than repeat them.
- **Paths differ from the design text.** Source is under
  `src/manta_trading/`. Unit tests: `test/unit/data/tick/` and
  `test/unit/cli/commands/`. Integration tests: `test/integration/data/`.
  Tick test helpers: `test/tick_support/` (`roll_seed.py`,
  `roll_synthetic.py`, `rows.py`, `database.py`). The contract is
  `user/reference/data-correctness-architecture.md`; the runbook is
  `user/runbooks/100-production-operations.md`.
- **Placements the design leaves open, decided here:**
  - **The line-state module is `data/tick/line_state.py`, not
    `availability.py`.** `data/tick/availability.py` already exists (224's
    dataset availability capture, `DatasetAvailability`). The new module
    holds `TickLineState` (`ok`, `not_configured`, `unreachable`,
    `migrations_pending`) and `TickUnavailable(state, detail)`, which are
    TD8's `TickAvailability` and `TickUnavailable` renamed so they cannot be
    mistaken for dataset availability. Task 8.4 (part 2) updates the design.
  - **The three output writers live in `cli/commands/tick_get_writers.py`**,
    so `tick_get_cmds.py` stays near 300 lines.
  - `constants.py` (tick) is 336 lines already. TD10's constants go there
    anyway: it is the single home for comparison values (229 made the same
    call).
- **Test environment.** Export `MT_TIMESCALE_TEST_URL` from `.env`, quotes
  stripped, in the same shell call. Run with
  `uv run python scripts/run_tests.py unit|integration -- <paths> -q`. Run
  tiers separately. The integration tier has 6 known pre-existing failures
  (priority1 list ×2, migration 051/052 ×2, policy-advances-head unaided ×2);
  re-run any other failure in isolation before investigating.
- **Type check and lint.** mypy in one invocation:
  `MYPYPATH=scripts uv run --extra dev mypy <src paths> <test paths>`. Scope
  `ruff format` to touched files and check `git diff main` for swept lines
  before each commit.
- **Production safety.** `trading_tick` and the production calendar are only
  read. Tests never read `MT_TICK_DB_URL`. Walkthrough runs against
  production are `SELECT`-only by construction (`READ ONLY` transactions).
- Delivers to 231 (API over `get_plan`, `tick_reads`, the accounting and
  freshness reads), 232 (GC needs no code change) and 234 (health reuses
  `tick_composed.open`). Next slice: 231.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 1 - Preflight facts

- [ ] **1.1 Confirm the code facts the design relies on** (effort 2)
  - [ ] Read `data/tick/tick_status_build.py` `_product_status`: name the
        exact steps (wanted range, `_scope`, `session_verdicts`, `Counter`
        over verdicts) that task 7.3 will extract for accounting (TD7). Note
        that `buckets` counts every session in scope, not only the wanted
        range, and record which one accounting reports (it must equal what
        `status` prints, FR12).
  - [ ] Confirm `held_session_bounds`, `build_schedule`, `RollRule.parse`,
        `DEFAULT_ROLL_RULE`, `roll_conninfo`, `RollRangeTooLong` and
        `TICK_ROLL_MAX_SESSIONS` exist with the signatures the design uses,
        and where each lives.
  - [ ] How `active` (`cli/commands/tick_active_cmds.py`) parses
        `--start/--end`, applies TD4's roll-day default, and marks a default
        rule `(default)`. `get` reuses these, not copies.
  - [ ] Whether the existing `time_bucket` helpers or any `Granularity` →
        duration map exist (`grep` `src/` for both). TD10: reuse if one
        exists, else one new map in tick `constants.py`.
  - [ ] Whether `roll_seed.py` / `roll_synthetic.py` / `rows.py` can insert
        `tick_trade` rows (with chosen `ts_event`, `sequence`,
        `sequence_ordinal`, price and BBO columns). If not, name the
        smallest helper to add in task 3.1.
  - [ ] How `mt data accounting` and `mt data overview` get their settings,
        connect, and choose exit codes (`cli/commands/accounting.py`,
        `cli/commands/overview.py` `gather` / `data_overview`), and which
        tests cover them (`test/unit/cli/commands/test_overview*.py`,
        `test/integration/test_overview_gather.py`,
        `test/unit/cli/commands/test_health_accounting_pass_runs.py`).
  - [ ] How `/api/v1/overview` maps `SourceFreshness` into `SourceRecord`
        (`api_server`), to confirm adding a field and a CLI-only source
        leaves the API response unchanged (FR13).
  - [ ] How `mt-run data overview` runs in production: the checkout or an
        installed release, and whether `/etc/manta-trading.env` is readable
        without sudo (decides walkthrough step 8, task 7.9 in part 2).
  - [ ] Write a `Findings (1.1)` block under this task. If the bucket step
        cannot be shared without changing `status`'s output, stop and ask
        the Project Manager.
  - [ ] Success: findings block present; no code changes.

---

## Section 2 - Constants and formatters

- [ ] **2.1 Add TD10 constants to `data/tick/constants.py`** (effort 1)
  - [ ] `TICK_GET_TICKS = "ticks"`; `TICK_BAR_GRANULARITIES` as a tuple of
        `Granularity` members (`M1 M5 M15 H1 H4 D1`), never re-spelled
        strings; `TICK_GET_CHOICES = (TICK_GET_TICKS,
        *TICK_BAR_GRANULARITIES)`.
  - [ ] `TICK_PRICE_SCALE = 10**9`, commented as DBN's
        `FIXED_PRICE_SCALE`.
  - [ ] `TICK_GET_FETCH_ROWS` (server-side cursor batch size) with a
        one-line reason for the value.
  - [ ] `TICK_COMPOSED_CONNECT_TIMEOUT_SECONDS = 5` and
        `TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS = 10`, each with TD10's
        reason.
  - [ ] The bar-width map (`Granularity` → width) per finding 1.1, with
        `D1` marked as "whole session", not 24 h.
  - [ ] Success: `test_constants.py` passes; ruff and mypy clean.

- [ ] **2.2 Implement `data/tick/tick_format.py`** (effort 2)
  - [ ] `price_text(raw: int) -> str | None`: `UNDEF_INT64` → `None`;
        otherwise `Decimal(raw) / TICK_PRICE_SCALE`, normalized to drop
        trailing zeros but never to exponent form (`5950.25`, `5950`,
        `-0.5`). No `float` anywhere.
  - [ ] `ts_text(ns: int) -> str`: ISO 8601 UTC with exactly nine fraction
        digits and `Z` (`2024-09-16T13:30:00.123456789Z`), built from the
        integer (seconds via `datetime`, fraction from `ns % 10**9`).
  - [ ] Module has no I/O and no CLI import.
  - [ ] Success: ruff and mypy clean.

- [ ] **2.3 Test the formatters** (effort 1)
  - [ ] `test/unit/data/tick/test_tick_format.py`: `price_text` for a
        quarter-tick price, a whole number, a negative price, a sub-cent
        value, `0`, and `UNDEF_INT64` (→ `None`, never
        `9223372036.854775807`, FR9).
  - [ ] `ts_text` for the epoch, a whole second, and a one-digit ns
        fraction (zero-padded to nine digits).
  - [ ] Success: tests pass. Commit: `feat: add tick get constants and exact
        price and time formatters`.

---

## Section 3 - Tick reads per session window

- [ ] **3.1 Test helper for seeded ticks** (effort 2)
  - [ ] Per finding 1.1, add (or reuse) a helper in `test/tick_support/`
        that inserts `tick_trade` rows for one instrument and unit with
        explicit `ts_event`, `sequence`, `sequence_ordinal`, `price`, `size`,
        `side` and optional BBO columns.
  - [ ] It can seed two sessions around a daily break and same-nanosecond
        ties (several rows sharing `ts_event`, differing in `sequence` /
        `sequence_ordinal`).
  - [ ] Success: helper imported by one smoke test that inserts and counts
        rows on `migrated_tick_db`.

- [ ] **3.2 Implement tick reads in `data/tick/tick_reads.py`** (effort 3)
  - [ ] A window type `(session_date, instrument_id, raw_symbol, open_ns,
        close_ns)` (or reuse the plan's segment type from task 4.1 if
        defined first; one definition only).
  - [ ] `read_ticks(conn, window, on_batch)`: one statement over a named
        (server-side) cursor, `WHERE instrument_id = … AND ts_event >=
        open_ns AND ts_event < close_ns ORDER BY ts_event, sequence,
        sequence_ordinal`, fetched `TICK_GET_FETCH_ROWS` at a time. Each
        batch is shaped into tick rows (TD5 column order) and handed to
        `on_batch` before the next fetch. Nothing is collected.
  - [ ] Raw integers are returned; formatting (`price_text`, `ts_text`)
        happens in the writer, so 231 can choose its own serialization.
  - [ ] The caller owns the connection and transaction; the module never
        commits, opens or closes a connection.
  - [ ] Success: ruff and mypy clean.

- [ ] **3.3 Implement bar reads in `tick_reads.py`** (effort 4)
  - [ ] `read_bars(conn, window, granularity, on_batch)`: bucket start
        `GREATEST(time_bucket(width, ts_event), open_ns)` on the ns integer
        (TD3); for `D1` the whole window is one bucket stamped `open_ns`.
  - [ ] Open and close are the first and last trade in
        `(ts_event, sequence, sequence_ordinal)` order (not `first()` /
        `last()` on `ts_event` alone). High, low by `max`/`min`; volume
        `sum(size)`. Empty buckets produce no row. Ordered by bucket start.
  - [ ] Same cursor and batching rules as 3.2.
  - [ ] If the module passes ~300 lines, move the SQL text to
        `tick_reads_sql.py`.
  - [ ] Success: ruff and mypy clean.

- [ ] **3.4 Test tick and bar reads** (effort 3)
  - [ ] `test/integration/data/test_tick_reads.py` on `migrated_tick_db`
        with 3.1's helper.
  - [ ] Ticks: key order including same-ns ties; rows outside the window
        (before open, at close) excluded; another instrument's rows
        excluded.
  - [ ] Bars: a `4h` bucket straddling the daily break yields two bars and
        the second starts at the session open (FR3); `1d` gives one bar per
        session whose OHLCV matches the seeded ticks, ties broken by
        `(sequence, sequence_ordinal)` (FR4); volume equals the sum of
        sizes (FR2).
  - [ ] Streaming: with a batch size smaller than the row count (patch
        `TICK_GET_FETCH_ROWS`), `on_batch` is called more than once and the
        first call happens before the last row is fetched.
  - [ ] Success: tests pass. Commit: `feat: add per-session tick and bar
        reads`.

---

## Section 4 - The read plan

- [ ] **4.1 Plan types and the contract plan in `data/tick/get_plan.py`**
      (effort 3)
  - [ ] Frozen dataclasses: a planned segment
        `(session_date, instrument_id, raw_symbol, open_ns, close_ns)`,
        `Incomplete(session_date, status)`, and `GetPlan(target, start,
        end, segments, unresolved, incomplete, definitions_as_of)`, where
        `unresolved` reuses 229's `Unresolved` shape.
  - [ ] `ContractNotFound` and `ContractAmbiguous` errors. Ambiguous carries
        every candidate `instrument_id` with its expiration (`ts_text`) and
        a message asking for a range that separates them. Both map to exit 1
        in `tick_exit.exit_code_for`.
  - [ ] `contract_plan(...)`: raw_symbol → `instrument_id` from
        `tick_definition`, candidates whose validity window overlaps the
        range (any window with no range). Default range = the contract's
        first to last ledger session under current units
        (`COVERAGE_PREDICATE`). With a range: calendar sessions in
        `[start, end)` inside the validity window. Bounds from the calendar.
  - [ ] `incomplete` from `session_verdicts` for the planned sessions
        (FR6). Contract reads have no schedule, so `unresolved` is empty.
  - [ ] Success: ruff and mypy clean; no CLI import.

- [ ] **4.2 Test the contract plan** (effort 2)
  - [ ] `test/integration/data/test_tick_get_plan.py`: a known contract
        with and without a range; an unknown symbol (`ContractNotFound`
        naming it); two definitions for one raw symbol a decade apart
        (`ContractAmbiguous` listing both ids and expirations, FR7); a range
        that separates them resolves to one; an incomplete session is
        planned and listed with its verdict.
  - [ ] Success: tests pass.

- [ ] **4.3 The product plan in `get_plan.py`** (effort 3)
  - [ ] `product_plan(...)`: resolve the range (TD2: none →
        `held_session_bounds`; `--start` alone → to the last held session;
        `--end` alone → from the first), call `build_schedule` with the
        parsed rule, then `session_verdicts` for the resolved sessions.
  - [ ] Segments are the resolved sessions in date order with their
        contract and calendar bounds. Unresolved sessions go to
        `unresolved` with 229's reason and are never filled from another
        rule or contract (FR5). Resolved but not `complete` sessions are
        planned and listed in `incomplete` (FR6).
  - [ ] `build_schedule`'s failures (`RollRangeTooLong`, calendar
        refusal, unreachable) propagate unchanged, so `tick_exit` maps them
        as it does for `active`.
  - [ ] A helper that groups consecutive segments with the same contract
        into display segments (`2024-09-16 → 2024-09-17  ESU4 (118)`).
        Reuse 229's `RollSchedule.segments()` if it fits; do not copy it.
  - [ ] Success: ruff and mypy clean.

- [ ] **4.4 Test the product plan** (effort 3)
  - [ ] Extend `test_tick_get_plan.py` with `roll_seed` data: a range
        across the seeded roll gives two display segments matching
        `active` for the same range (FR2); a range starting at the first
        held session lists the first two sessions as unresolved and plans
        the rest (FR5); an incomplete resolved session is planned and
        listed (FR6); a range over `TICK_ROLL_MAX_SESSIONS` (patched small)
        raises `RollRangeTooLong`; no range uses the held bounds (FR8).
  - [ ] Success: tests pass. Commit: `feat: add tick get read plan for
        contracts and products`.

---

## Section 5 - `mt data tick get`

- [ ] **5.1 Implement the writers in `cli/commands/tick_get_writers.py`**
      (effort 4)
  - [ ] One writer interface: `begin(plan)`, `rows(batch)`,
        `finish(exit_code)`, `partial(n_rows, error_first_line)`. Three
        implementations; formatting through `price_text` / `ts_text` only.
  - [ ] **Text:** header naming the target, the rule (with `(default)`),
        and each display segment; one fixed-width line per row as it
        arrives (no Rich table); `N row(s)` footer; then `unresolved` and
        `incomplete` lists.
  - [ ] **CSV:** header and rows only on stdout; segments and both lists
        to stderr. Undefined prices are empty fields.
  - [ ] **JSON:** written incrementally as one object in TD5's key order;
        every key except `rows`, `row_count`, `exit_code` (and `complete`,
        `error`) written in `begin`. Prices as strings, undefined as `null`.
        `target` is `{product, rule, is_default}` or
        `{contract, instrument_id}`.
  - [ ] Tick rows and bar rows use TD5's column sets; BBO columns always
        present on tick rows.
  - [ ] Success: ruff and mypy clean.

- [ ] **5.2 Test the writers** (effort 3)
  - [ ] `test/unit/cli/commands/test_tick_get_writers.py`, writing to
        string buffers: each format for ticks and for bars; JSON parses and
        has TD5's keys in order with `complete: true`, `error: null`; CSV
        stdout has no segment text; undefined BBO price is `null` / empty
        (FR9, FR10); an empty plan prints the header and `0 row(s)`.
  - [ ] Success: tests pass.

- [ ] **5.3 Implement the `get` verb in `cli/commands/tick_get_cmds.py`**
      (effort 3)
  - [ ] Options per TD1, using `tick_store_cmds`'s `JSON_OPTION`,
        `PRODUCT_OPTION`, `DATE_FORMAT` (no re-declared option). `WHAT` is a
        required positional validated against `TICK_GET_CHOICES`.
  - [ ] Exit 1 for: neither or both of `--contract`/`--product`; `--rule`
        without `--product`; `--csv` with `--json`; unknown product; bad
        rule (`RollRule.parse`'s message).
  - [ ] Help text states "end exclusive, in session dates, unlike
        `mt data get`" (TD2). An `epilog` with examples, as the other tick
        verbs have.
  - [ ] Flow: `connect_migrated` on the tick track (pending → exit 1), the
        plan (task 4), then one `REPEATABLE READ READ ONLY` transaction
        that runs 3.2 / 3.3 per segment in order, feeding the writer. Exit
        3 when `unresolved` or `incomplete` is non-empty, else 0. Runs
        through `run_mapped(main, json, storage=read_storage())`.
  - [ ] Register `get` in `cli/commands/tick.py`.
  - [ ] Success: `uv run mt data tick get --help` lists the options and the
        end-exclusive note.

- [ ] **5.4 Test the `get` verb** (effort 3)
  - [ ] `test/integration/data/test_tick_get_cli.py` with seeded ticks:
        exit 0 on a clean contract read with the row count equal to the
        seeded count (FR1); exit 3 with output when a list is non-empty
        (FR5, FR6); exit 1 for each bad option combination in 5.3, an
        unknown and an ambiguous contract (FR7), and a pending tick
        migration; exit 4 for an unreachable tick database.
  - [ ] Success: tests pass. Commit: `feat: add mt data tick get`.

- [ ] **5.5 Handle a read that stops partway (TD11)** (effort 3)
  - [ ] After the first row, an `OperationalError` (including
        `QueryCanceled`) stops the read: roll back, call the writer's
        `partial`, exit 4. JSON closes valid with `complete: false`,
        `error`, `exit_code: 4`; CSV writes `partial output: N rows
        written, then: <first line>` to stderr only; text prints
        `PARTIAL: N row(s), then: <first line>` in place of the footer and
        the error on stderr.
  - [ ] Before the first row, the same errors take the ordinary path
        (exit 4 via `run_mapped`).
  - [ ] `BrokenPipeError` on write: close the cursor, roll back, close the
        connection, point stdout at `/dev/null` (`os.dup2`), exit 0 with
        nothing on stderr.
  - [ ] Success: ruff and mypy clean.

- [ ] **5.6 Test the partial and closed-pipe paths** (effort 3)
  - [ ] Extend `test_tick_get_cli.py`: with a small fetch size, terminate
        the backend (`pg_terminate_backend` from a second connection) after
        the first batch, once per format, and check TD11's table (JSON
        parses, `complete` false; CSV stdout clean, stderr line; text
        `PARTIAL:` footer; exit 4) (FR10a).
  - [ ] Run `get` as a subprocess piped into a reader that closes after
        the first lines: exit 0, empty stderr, and `pg_stat_activity` shows
        no open transaction from the test's application name.
  - [ ] Success: tests pass. Commit: `feat: report partial tick get output
        explicitly`.

- [ ] **5.7 Walkthrough steps 1–4 on `trading_tick`** (effort 2)
  - [ ] Run the design's Verification Walkthrough steps 1–4 with `.env`
        loaded and compare each with its expected output (segments vs
        `active`, CSV count vs ledger, unresolved head with exit 3, unknown
        contract exit 1).
  - [ ] Record commands, outputs (trimmed) and exit codes in
        `user/notes/230-walkthrough.md` (with frontmatter). Any mismatch
        with an expected value: stop and report to the Project Manager.
  - [ ] Success: note committed: `docs: record slice 230 get walkthrough`.

Continue in `230-tasks.operator-surface-full-parity-2.md`.
