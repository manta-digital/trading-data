---
docType: tasks
slice: cme-session-model-and-data-correctness-amendment
project: trading-data
lld: user/slices/221-slice.cme-session-model-and-data-correctness-amendment.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: []
interfaces: [222, 225, 229, 232]
projectState: >
  Slice design committed; slice review PASS (993fab4). 220 (DBN reader) and
  923 (multi-database plumbing, minute track routed to primary) are released
  in 0.19.0. The calendar tables hold NYSE and NASDAQ only; the minute
  migration chain ends at 056. NYSE holiday defects are filed as issue #26
  and are out of scope.
dateCreated: 20260928
dateUpdated: 20260928
status: complete
part: 2
partOf: 221-tasks.cme-session-model-and-data-correctness-amendment-1.md
---

# Tasks: CME Session Model and Data-Correctness Amendment (part 2)

Part 1 (`221-tasks.cme-session-model-and-data-correctness-amendment-1.md`) holds the Context Summary, the test environment, walkthrough-database and commit rules, and Sections 0–5. Read it first; every rule there applies here.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 6 — Session lookup on `TradingCalendar` (LLD D7, D8)

- [x] **6.1 Add `sessions_between`, `session_containing` and
      `OutOfPopulatedRangeError`**
  - [x] In `data/base/trading_calendar.py`, add
        `OutOfPopulatedRangeError(calendar_id, ts, first_open, last_close)`,
        with a message naming all four values and `mt data extend`.
  - [x] `sessions_between(start_utc, end_utc) -> list[Session]`:
    - one query reads the populated span (MIN of `session_open_utc`, MAX of
      `session_close_utc`), cached per instance;
    - it raises when `start_utc` is before the first open or `end_utc` is
      after the last close;
    - it returns the sessions intersecting `[start_utc, end_utc)`, in order;
    - naive inputs raise `ValueError`.
  - [x] `session_containing(ts)`: check `ts.tzinfo` first and raise
        `ValueError` for a naive `ts` before any comparison. (Comparing a
        naive and an aware datetime raises `TypeError`, which would violate
        FR5.) Then run the same range check and
        `SessionIndex(...).locate(ts)` over the sessions around `ts`.
  - [x] Success: mypy clean
  - [x] Effort: 2

- [x] **6.2 Route the ETH and ALL paths through `session_interval`**
  - [x] In `_build_trading_hours`, replace the two `datetime.combine` calls
        with `session_interval`, so an inverted calendar cannot produce an
        inverted session (D3).
  - [x] The existing unit tests in `test/unit/data/base/test_trading_calendar.py`
        must still pass unchanged.
  - [x] Success: that test file passes
  - [x] Effort: 1

- [x] **6.3 Add the product → calendar mapping**
  - [x] In `data/tick/constants.py`, add
        `FUTURES_PRODUCT_CALENDAR: Final[Mapping[str, str]] = {"ES": CME_EQUITY_CALENDAR_ID}`,
        imported from `seed_cme_calendar`, and
        `calendar_for_product(product) -> str`. It raises `KeyError` naming
        the product and the known products (D8).
  - [x] Add a unit test for the known product and for an unknown one.
  - [x] Success: the test passes; mypy clean
  - [x] Effort: 1

- [x] **6.4 Integration tests for the lookup**
  - [x] Extend `test/integration/test_trading_calendar_integration.py`,
        against a throwaway database migrated through 058. Cover
        `session_containing` for:
    - inside a session;
    - the daily break (`None`);
    - 2024-12-25 (`None`);
    - before 2020-01-01 17:00 CT, and after the last close (both raise);
    - a naive datetime (`pytest.raises(ValueError)`, not a broader class).
  - [x] Cover `sessions_between` over Thanksgiving week 2024: the expected
        dates, in order.
  - [x] Success: the file passes. **Commit Section 6.**
  - [x] Effort: 2

---

## Section 7 — Calendar CLI (LLD API Contracts)

- [x] **7.1 Fix `mt data calendars list`**
  - [x] Change the query in `calendars_list` to select `exchange_name AS
        calendar_name`, `market_open AS market_open_time` and `market_close
        AS market_close_time` (the same aliases `_ensure_loaded` uses), plus
        `holidays_seeded_through`. Show the bound in the table and in the JSON
        output.
  - [x] Add a unit test asserting the SQL references only real columns (the
        names from migrations 004 and 057), and an integration assertion in
        5.2's file that the command exits 0 and lists all three calendars.
  - [x] Success: both tests pass
  - [x] Effort: 1

- [x] **7.2 Add `mt data calendars sessions`**
  - [x] New module `cli/commands/calendar_sessions.py`, registered on
        `calendars_app` in `data.py` with a single added line.
  - [x] Options: `--calendar` (required), `--from`, `--to` and `--json`.
  - [x] Output is one row per session: date, open and close in the calendar's
        time zone, open and close in UTC, duration, and the exception name
        (joined from `trading_holidays`).
  - [x] It uses `TradingCalendar.sessions_between`. An out-of-range request
        prints the `OutOfPopulatedRangeError` message and exits 1.
  - [x] Add unit tests with a mocked calendar (table and JSON output), and an
        integration test on the throwaway database for Thanksgiving week 2024.
  - [x] Success: the tests pass, and `uv run mt data calendars sessions --help`
        shows the options. **Commit Section 7.**
  - [x] Effort: 2

---

## Section 8 — Real-data proof (LLD Success Criteria 8)

- [x] **8.1 Create `scripts/verify_cme_sessions.py`**
  - [x] Arguments: `--calendar` and one or more job directories. It opens
        files with the 220 `ITickFileReader`, one file at a time, and assigns
        each batch's `ts_event` through `SessionIndex.locate_ns`. It gets the
        index from `TradingCalendar.sessions_between` over the files' span.
  - [x] It reports:
    - total records per job, compared with the job's `manifest.json`;
    - `outside any session: N`;
    - first and last trade per session against open and close.
  - [x] Exit codes, per the LLD Consumes section:
    - 0: clean;
    - 1: records outside a session, printing each timestamp and the nearest
      session;
    - 2: a missing path or manifest, a decode failure, or a count mismatch.
  - [x] Success: `--help` works, and a missing directory exits 2 naming it
  - [x] Effort: 3

- [x] **8.2 Run the script over both adopted jobs and record the evidence**
  - [x] Unzip `GLBX-20250123-XT4GD5UM6C.zip` into the scratchpad, and run
        the script against it and against `GLBX-20240930-USM7UXXJBA`, with
        the walkthrough database as `MT_TIMESCALE_DB_URL`.
  - [x] Required: exit 0, zero records outside a session, and totals equal
        each manifest.
  - [x] Add the first and last trade times for the Labor Day,
        Thanksgiving, Black Friday and Christmas Eve 2024 sessions to
        Implementation Findings, next to each session's close.
  - [x] A record outside a session is a seed defect. Fix the table (4.3),
        re-run 5.2, and re-run the script. Never widen a session to fit.
  - [x] Success: evidence recorded
  - [x] Effort: 2

- [x] **8.3 Commit a real-data boundary fixture**
  - [x] Extract the `ts_event` values of the first and last trades from
        8.2's sessions into `test/fixtures/calendar/cme_equity_2024_boundaries.json`,
        with a `provenance` key naming the job ids and files.
  - [x] Add a unit test that builds a `SessionIndex` from the seeded
        sessions for those dates and asserts each fixture timestamp lands in
        its expected session. The seeded sessions come from
        `populate_trading_sessions` with `CME_EQUITY_EXCEPTIONS`, so no
        database is needed.
  - [x] Success: the test passes. **Commit Section 8.**
  - [x] Effort: 2

---

## Section 9 — Contract amendment (LLD D10)

All edits go to `user/reference/data-correctness-architecture.md`. Update its
`dateUpdated`.

- [x] **9.1 Vocabulary terms and tick vocabulary rules**
  - [x] Add the D10 terms: Session (futures), Archive unit, Ingest ledger, and
        Instrument-session complete.
  - [x] Add the D10 rules: manifest-based completeness, `Granularity.TICK`
        versus `PassKind.TICK`, and the `granularity = 'tick'` enumeration
        list, empty as of 221.
  - [x] Success: the terms are present, and each rule names its source
        (architecture section)
  - [x] Effort: 1

- [x] **9.2 Invariants I11–I14**
  - [x] Add I11 (completeness from the manifest), I12 (provenance and
        supersession), I13 (session-assigned ticks) and I14 (futures identity
        explicit) under Invariants, worded from D10.
  - [x] Success: four new invariant sections, numbered after I10
  - [x] Effort: 1

- [x] **9.3 The I7 exception, the sequence-gap move, and the renumbering**
  - [x] Under I7, add the tick exception and its reason (D10).
  - [x] Rewrite the Notes paragraph on "Initiative 200": it is initiative
        220 (renumbered from the concept's 200), it inherits I2–I10 except the
        I7 exception, it adds I11–I14, and sequence-gap detection is reserved
        for the realtime initiative, with the reason given.
  - [x] Update the Purpose line and the out-of-scope lines that say "200"
        for futures tick.
  - [x] Success: no remaining text promises sequence-gap detection for
        initiative 220 (grep `sequence`)
  - [x] Effort: 1

- [x] **9.4 Mapping table and the required CLI line**
  - [x] The I4 row gains the 221 entry. The I7 row gains the tick
        exception. Add rows for I11–I14 with planned closing slices and status
        "Framed (221)" (D10).
  - [x] Update the tick line under Required CLI commands to 220's
        vocabulary, matching the I10 row.
  - [x] Success: the table renders (check the column count per row).
        **Commit Section 9.**
  - [x] Effort: 1

---

## Section 10 — Final validation

- [x] **10.1 Full checks**
  - [x] Run `uv run ruff check` on the touched files, and `uv run mypy src
        test` in one invocation.
  - [x] Run `uv run pytest test/unit -q`, then the integration tier
        separately. Every new test passes. Confirm any failure also fails at
        the slice base before calling it pre-existing, and list it.
  - [x] Success: clean, or pre-existing failures listed with evidence
  - [x] Effort: 2

- [x] **10.2 Run the verification walkthrough and update the design**
  - [x] Run LLD Verification Walkthrough steps 1–7 against the walkthrough
        database (see Walkthrough database above) after `mt data migrate
        apply`, then drop it, and correct the walkthrough text to the
        actual commands and output.
  - [x] Set the slice design's `status` to `complete` and update
        `dateUpdated`.
  - [x] Success: every step behaves as written. **Commit.**
  - [x] Effort: 2
