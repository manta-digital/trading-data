---
docType: tasks
slice: operator-surface-full-parity
project: trading-data
lld: user/slices/230-slice.operator-surface-full-parity.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [221, 225, 229, 922]
projectState: >
  Part 1 complete: tick get constants, formatters, per-session reads, the
  read plan and `mt data tick get` are committed with their tests and
  walkthrough steps 1-4.
dateCreated: 20261012
dateUpdated: 20261012
status: in_progress
---

# Tasks: Operator Surface Full Parity (part 2 of 2)

Part 2: `debug`, the line states and composed lines (accounting, overview),
docs and closing checks. Part 1
(`230-tasks.operator-surface-full-parity-1.md`) holds the Context Summary;
read it first. Its placement decisions apply here, in particular: the line
states live in `data/tick/line_state.py` (`TickLineState`,
`TickUnavailable`), because `data/tick/availability.py` is already taken.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 6 - `mt data tick debug`

- [x] **6.1 Implement `data/tick/debug_reads.py`** (effort 3)
  - [x] `requests(conn, product, start, end)`: `tick_request` rows with
        TD6's columns. `--product` maps to the universe entry's request
        symbols; `--start/--end` filters on range overlap.
  - [x] `units(conn, product, start, end, state, fetch_status)`:
        `tick_archive_unit` joined to its request's schema, TD6's columns;
        `unit_date` in `[start, end)`; state and fetch status as
        `UnitState` / `FetchStatus` members.
  - [x] `definitions(conn, product, symbol, as_of_open_ns, spreads)`:
        `tick_definition` rows, TD6's columns. Outrights only
        (`OUTRIGHT_CLASS`) unless `spreads`. `as_of` keeps rows valid at
        that session's open (`activation_ns ≤ open ≤ expiration_ns`);
        the caller resolves the session open from the calendar.
  - [x] Rows returned raw (ints, enums, timestamps); formatting is the
        renderer's. Parameterized SQL only; no row cap.
  - [x] Success: ruff and mypy clean; no CLI import.

- [x] **6.2 Test `debug_reads`** (effort 2)
  - [x] `test/integration/data/test_tick_debug_reads.py` with
        `tick_support/rows.py` seeding: each filter selects and excludes
        the right rows (product, range overlap, unit-date window end
        exclusive, state, fetch status, symbol, as-of, spreads on/off);
        no filters returns everything; empty result is an empty list.
  - [x] Success: tests pass.

- [x] **6.3 Implement `cli/commands/tick_debug_cmds.py`** (effort 3)
  - [x] A `debug` sub-typer with `requests`, `units`, `definitions` and
        TD6's options. `--state` / `--fetch-status` choices rendered from
        the enums; `--product` from `PRODUCT_OPTION`; `--json` from
        `JSON_OPTION`. Unknown product → exit 1.
        Note: `--state`/`--fetch-status` take text validated against the enums (exit 1, choices listed in --help), not Click choices, whose usage error would exit 2. `--as-of` needs `--product` (its calendar dates the session); without it, exit 1.
  - [x] Text is a Rich table per verb; prices through `price_text`, ns
        times through `ts_text`. JSON is `{rows, exit_code}`.
  - [x] `connect_migrated` on the tick track, a `READ ONLY` transaction,
        `run_mapped` with `read_storage()`. Exits 0 (including no rows), 1,
        4; never 3.
  - [x] Register the sub-typer in `cli/commands/tick.py`, with an `epilog`
        per verb.
  - [x] Success: `uv run mt data tick debug units --help` lists the enum
        choices.

- [x] **6.4 Test the `debug` verbs** (effort 2)
  - [x] `test/integration/data/test_tick_debug_cli.py`: one text and one
        JSON run per verb on seeded rows (filter coverage stays in 6.2); a
        value outside the enums rejected by `--state` and `--fetch-status`;
        definitions hide spreads unless `--spreads` (FR11); exit 1 for an
        unknown product and a pending migration; exit 4 unreachable.
  - [x] Success: tests pass. Commit: `feat: add mt data tick debug reads`.

- [x] **6.5 Walkthrough step 5 on `trading_tick`** (effort 1)
  - [x] Run step 5 and compare: both adopted jobs with costs; the
        `verified` units count equals `status`'s awaiting-ingest bucket;
        definitions show ESU4, ESZ4, ESH5 … with tick size `0.25` and no
        spreads.
  - [x] Append to `user/notes/230-walkthrough.md`. A mismatch: stop and
        report to the Project Manager.
  - [x] Success: note committed: `docs: record slice 230 debug walkthrough`.

---

## Section 7 - Line states and composed lines

- [x] **7.1 Implement `data/tick/line_state.py` and
      `cli/tick_composed.py`** (effort 3)
  - [x] `line_state.py`: `TickLineState(StrEnum)` with `ok`,
        `not_configured`, `unreachable`, `migrations_pending`, and frozen
        `TickUnavailable(state, detail)`. No I/O, no CLI import (TD8).
  - [x] `tick_composed.open(settings)`: `MT_TICK_DB_URL` unset →
        `TickUnavailable(not_configured, "MT_TICK_DB_URL unset")`. Otherwise
        connect with `TICK_COMPOSED_CONNECT_TIMEOUT_SECONDS` and a session
        `statement_timeout` of `TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS`,
        read-only. Pending tick migrations → `migrations_pending`.
        Refused / timeout / `OperationalError` → `unreachable` with the
        error's first line. Returns the connection otherwise.
        Note: the composed read also classifies `TickCalendarError` and `TickPreflightError` (the calendar the accounting read uses) as `unreachable`, so a calendar refusal is a line, never a failed command (FR14). `unavailable_text` lives in `line_state.py`. The remedy text is now one constant, `TICK_MIGRATE_COMMAND`, used by `store_context` too; `_missing_migrations` became public `missing_migrations`.
  - [x] A helper that classifies an `OperationalError` /
        `QueryCanceled` raised later, during the composed read, into the
        same `unreachable` state (naming the timeout), so both callers use
        one mapping.
  - [x] One function, `unavailable_text(TickUnavailable) -> str`, owns
        TD8's wording (`not configured (MT_TICK_DB_URL unset)`,
        `unreachable: …`, `migrations pending (mt data migrate apply
        --track tick)`). Accounting and overview both call it; no other
        module spells these texts.
  - [x] Success: ruff and mypy clean.

- [x] **7.2 Test the line states** (effort 2)
  - [x] `test/unit/cli/test_tick_composed.py`: each state from `open`
        (unset URL, refused port, pending migrations faked at the
        migration check, a late `QueryCanceled`); the rendered line for
        each.
  - [x] Password never printed: a URL with a distinctive password at an
        unreachable host; the rendered line and `detail` do not contain it.
  - [x] Success: tests pass. Commit: `feat: add tick line states and the
        composed-surface opener`.

- [x] **7.3 Extract the status bucket step** (effort 3)
  - [x] Per finding 1.1, move the wanted-range / scope / verdicts / bucket
        count out of `_product_status` into one public function (in
        `tick_status_build.py`, or `status_reads.py` if that avoids an
        import of `tick_status_build` from accounting). `_product_status`
        calls it; its output is unchanged.
  - [x] Do not copy the logic; there is exactly one bucket computation
        afterwards.
  - [x] Success: `test_tick_status_build.py`, `test_tick_status_cli.py`,
        `test_tick_status_rolls.py` and `test_tick_status.py` pass
        unchanged. Commit: `refactor: extract tick status bucket step`.

- [x] **7.4 Implement `data/tick/tick_accounting.py`** (effort 2)
  - [x] `TickAccountingLine(product, sessions_wanted, sessions_complete,
        buckets, records)` per universe product, from 7.3's function and
        `ledger_totals` (records = ledger sum over current units).
        Note: the field is `sessions_in_scope`, not `sessions_wanted` (status's buckets cover the scope, not the wanted range; Findings 1.1). JSON `tick` also carries `detail` (the state's detail, null when ok).
  - [x] `accounting_lines(conn, calendar_url, universe)` returns the lines;
        no CLI import (231 reuses it for `/api/v1/status`).
  - [x] Success: ruff and mypy clean.

- [x] **7.5 Add the tick section to `mt data accounting`** (effort 3)
  - [x] After the minute section, `tick_composed.open`; on a connection,
        `accounting_lines` and print `futures tick universe:` with TD7's
        line per product; on `TickUnavailable` (or a late failure
        classified by 7.1's helper), print the state line.
  - [x] JSON gains `tick: {state, products: [...]}`; `products` empty
        unless `state` is `ok`. Existing keys unchanged.
  - [x] The exit code and the `PassKind.ACCOUNTING` detail are unchanged by
        the tick section (TD7, TD8).
  - [x] Success: ruff and mypy clean.

- [x] **7.6 Test accounting** (effort 3)
  - [x] `test/integration/data/test_tick_accounting.py`: the line's figures
        equal `build_status`'s buckets for the same seeded product (FR12);
        JSON `tick` object shape.
  - [x] Each state line with the minute section still printed and the exit
        code as without the tick line: URL unset, unreachable tick
        database, pending tick migration (FR14).
  - [x] Existing accounting tests pass; the recorded `PassKind.ACCOUNTING`
        detail is unchanged.
        Note: `test_health_accounting_pass_runs.py`'s harness fakes settings with a MagicMock; it now also stubs `_tick_lines` (not configured). No assertion changed.
  - [x] Success: tests pass. Commit: `feat: add futures tick line to data
        accounting`.

- [x] **7.7 Add the `futures ticks` line to `mt data overview`** (effort 3)
  - [x] First, per finding 1.1: if no test asserts the `/api/v1/overview`
        payload for fixed facts, add it now and see it pass on the
        unchanged code (the FR13 baseline 7.8 relies on).
  - [x] `cli/overview_types.py`: `SourceFreshness.unavailable: str | None =
        None`.
  - [x] A ledger freshness read (newest `last_event_ns` under current
        units, via `COVERAGE_PREDICATE`) as `newest_event_ns(conn)` in
        `data/tick/status_reads.py` (the ledger read helpers' home, not
        `cli/`), so 231 can reuse it.
        Note: `newest_event_ns(conn)` in `status_reads.py`.
  - [x] `cli/commands/overview.py` `gather`: after the credits call,
        `tick_composed.open`, the freshness read, and a fifth
        `SourceFreshness("futures ticks", …)` whose `unavailable` is
        exactly `unavailable_text(...)` (task 7.1). `gather_db_facts` is not
        touched.
  - [x] `cli/rendering/overview.py`: print `unavailable` as given, in
        place of the time; it never builds or re-spells state text. JSON
        sources carry `unavailable`.
  - [x] Overview's exit code unchanged by the tick line.
  - [x] Success: ruff and mypy clean.

- [ ] **7.8 Test overview** (effort 3)
  - [x] Unit (`test/unit/cli/commands/test_overview*.py` or a new
        `test_overview_tick.py`): each state renders TD8's line; JSON has
        `unavailable` on every source, `null` on the existing four.
  - [x] Integration: the line's time equals the seeded newest
        `last_event_ns` (FR13); each degraded state keeps the other lines
        and the exit code (FR14).
  - [x] `/api/v1/overview` unchanged (FR13): per finding 1.1, the existing
        test that asserts the API payload for fixed facts passes without
        edits. If none exists, add one in `test/unit/api_server/` that
        builds the payload from fixed `OverviewFacts` and asserts its exact
        keys and values (that test is 7.7's first step).
        Note: `test_operations_models.py` passes unedited; `test_overview_gather.py` and `test_operations_serving.py` now expect the fifth, CLI-only source (`gather` ≠ `gather_db_facts` by one source, by design) and their fake settings carry `tick_db_url = None`.
  - [ ] Existing overview, gather and load tests pass
        (`test/integration/test_overview_gather.py`,
        `test/load/test_922_overview_nfr.py`).
  - [ ] Success: tests pass. Commit: `feat: add futures ticks line to data
        overview`.

- [ ] **7.9 Walkthrough steps 6–8** (effort 2)
  - [ ] Step 6: `time mt data accounting` (record the time; it must be well
        inside `TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS`), counts match
        `mt data tick status --product ES`; overview's `futures ticks`
        near 2025-01-01 21:59 UTC.
  - [ ] Step 7: both degraded commands print their lines and exit 0.
  - [ ] Step 8: per finding 1.1. If `mt-run data overview` runs the
        checkout, run it and confirm `not configured`. If it runs an
        installed release, record that the production line is proven by
        step 7's unset case plus the absent key in
        `/etc/manta-trading.env` (checked with a read-only `grep -c`), and
        that the `mt-run` run follows deploy.
  - [ ] Append to `user/notes/230-walkthrough.md`. A mismatch: stop and
        report to the Project Manager.
  - [ ] Success: note committed: `docs: record slice 230 composed-line
        walkthrough`.

---

## Section 8 - Docs and closing checks

- [ ] **8.1 Runbook: "Operating the tick tier"** (effort 2)
  - [ ] In `user/runbooks/100-production-operations.md`, a new section:
        reading state (`status`, `coverage`, `active`, `accounting`,
        `overview`); reading data (`get`, bulk reads as `--csv > file`,
        end exclusive unlike `mt data get`); investigating (`debug`);
        running passes by hand (`estimate`, `pass`, `ingest`, `adopt`,
        `reset`; backfill = universe edit + `pass`); the exit-code table
        (0/1/3/4 for reads).
  - [ ] Quick reference gains the tick rows.
  - [ ] Success: commands in the section match `--help` output.

- [ ] **8.2 Data-correctness contract rows** (effort 1)
  - [ ] In `user/reference/data-correctness-architecture.md`: I8 gains
        `mt data tick debug`; I10 gains `get`, `debug`, `pass` as the tick
        backfill (TD9), and the end-exclusive vs inclusive difference
        (TD2); I14 gains the product-level `get` rule (one contract per
        session, lists, exit 3); the Required CLI commands list gains the
        new verbs.
  - [ ] Success: rows updated (committed with 8.3).

- [ ] **8.3 Architecture Revision Log entry for TD2 (required)** (effort 1)
  - [ ] In `user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md`
        Revision Log: tick `get --end` is exclusive in session dates,
        departing on purpose from "same questions, same defaults" with
        `mt data get`'s inclusive end; one line on why (one convention
        within the tick subgroup).
  - [ ] Success: entry present, dated 2026-10-12 or the commit date.
        Commit (8.1–8.3): `docs: add tick runbook section, contract rows and
        revision log for slice 230`.

- [ ] **8.4 Gating check: design and slice plan match what shipped**
      (effort 1)
  - [ ] Confirm the slice plan's 234 entry (already edited in 8c70036)
        still says 234 reuses 230's line states and `tick_composed.open`
        and that 230 is the first tick composing surface; fix the module
        name to `line_state.py` if it names `availability.py`.
  - [ ] Update the slice design for this file's placements:
        `line_state.py` / `TickLineState` (TD8, Component Structure,
        Provides to 231), `tick_get_writers.py`, `test/` paths, and any
        change found during implementation.
  - [ ] Success: both documents match. Commit: `docs: align slice 230
        design and 220 plan with shipped behaviour`.

- [ ] **8.5 Final validation** (effort 2)
  - [ ] Kalshi pass-contract diff: `test/unit/data/tick/test_pass_contract_parity.py`
        passes; no change expected (this slice does not touch the pass).
  - [ ] The design's "Realtime paths" and "Does this belong in the API?"
        sections still hold for what shipped; correct them in place if not.
  - [ ] FR coverage: every Functional Requirement 1–14 (and 10a) maps to a
        passing test or a walkthrough step; list the map in the
        walkthrough note.
  - [ ] ruff (check; format scoped to touched files) and mypy (one
        invocation, `MYPYPATH=scripts`) clean on touched files.
  - [ ] Unit tier and integration tier pass, except the known pre-existing
        failures (re-run each in isolation).
  - [ ] New and touched source files ≤ ~300 lines (`wc -l`), tick
        `constants.py` excepted per part 1's Context Summary; functions
        ≤ ~50 lines.
  - [ ] `grep` finds no tick `get` choice, bar token or price scale spelled
        as a literal outside `constants.py` and tests.
  - [ ] Success: all green. Commit any fixes: `fix: address slice 230 final
        validation`.
