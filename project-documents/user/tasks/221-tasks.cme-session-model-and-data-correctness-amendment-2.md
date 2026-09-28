---
docType: tasks
slice: cme-session-model-and-data-correctness-amendment
project: trading-data
lld: user/slices/221-slice.cme-session-model-and-data-correctness-amendment.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: []
interfaces: [222, 224, 228, 231]
projectState: >
  Slice design committed; slice review PASS (993fab4). 220 (DBN reader) and
  923 (multi-database plumbing, minute track routed to primary) are released
  in 0.19.0. The calendar tables hold NYSE and NASDAQ only; the minute
  migration chain ends at 056. NYSE holiday defects are filed as issue #26
  and are out of scope.
dateCreated: 20260928
dateUpdated: 20260928
status: not_started
part: 2
partOf: 221-tasks.cme-session-model-and-data-correctness-amendment-1.md
---

# Tasks: CME Session Model and Data-Correctness Amendment (part 2)

Part 1 (`221-tasks.cme-session-model-and-data-correctness-amendment-1.md`) holds the Context Summary, the test environment, walkthrough-database and commit rules, and Sections 0–5. Read it first; every rule there applies here.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 6 — Session lookup on `TradingCalendar` (LLD D7, D8)

- [ ] **6.1 Add `sessions_between`, `session_containing` and
      `OutOfPopulatedRangeError`**
  - [ ] In `data/base/trading_calendar.py`, add
        `OutOfPopulatedRangeError(calendar_id, ts, first_open, last_close)`,
        with a message naming all four values and `mt data extend`.
  - [ ] `sessions_between(start_utc, end_utc) -> list[Session]`:
    - one query reads the populated span (MIN of `session_open_utc`, MAX of
      `session_close_utc`), cached per instance;
    - it raises when `start_utc` is before the first open or `end_utc` is
      after the last close;
    - it returns the sessions intersecting `[start_utc, end_utc)`, in order;
    - naive inputs raise `ValueError`.
  - [ ] `session_containing(ts)`: check `ts.tzinfo` first and raise
        `ValueError` for a naive `ts` before any comparison. (Comparing a
        naive and an aware datetime raises `TypeError`, which would violate
        FR5.) Then run the same range check and
        `SessionIndex(...).locate(ts)` over the sessions around `ts`.
  - [ ] Success: mypy clean
  - [ ] Effort: 2

- [ ] **6.2 Route the ETH and ALL paths through `session_interval`**
  - [ ] In `_build_trading_hours`, replace the two `datetime.combine` calls
        with `session_interval`, so an inverted calendar cannot produce an
        inverted session (D3).
  - [ ] The existing unit tests in `test/unit/data/base/test_trading_calendar.py`
        must still pass unchanged.
  - [ ] Success: that test file passes
  - [ ] Effort: 1

- [ ] **6.3 Add the product → calendar mapping**
  - [ ] In `data/tick/constants.py`, add
        `FUTURES_PRODUCT_CALENDAR: Final[Mapping[str, str]] = {"ES": CME_EQUITY_CALENDAR_ID}`,
        imported from `seed_cme_calendar`, and
        `calendar_for_product(product) -> str`. It raises `KeyError` naming
        the product and the known products (D8).
  - [ ] Add a unit test for the known product and for an unknown one.
  - [ ] Success: the test passes; mypy clean
  - [ ] Effort: 1

- [ ] **6.4 Integration tests for the lookup**
  - [ ] Extend `test/integration/test_trading_calendar_integration.py`,
        against a throwaway database migrated through 058. Cover
        `session_containing` for:
    - inside a session;
    - the daily break (`None`);
    - 2024-12-25 (`None`);
    - before 2020-01-01 17:00 CT, and after the last close (both raise);
    - a naive datetime (`pytest.raises(ValueError)`, not a broader class).
  - [ ] Cover `sessions_between` over Thanksgiving week 2024: the expected
        dates, in order.
  - [ ] Success: the file passes. **Commit Section 6.**
  - [ ] Effort: 2

---

## Section 7 — Calendar CLI (LLD API Contracts)

- [ ] **7.1 Fix `mt data calendars list`**
  - [ ] Change the query in `calendars_list` to select `exchange_name AS
        calendar_name`, `market_open AS market_open_time` and `market_close
        AS market_close_time` (the same aliases `_ensure_loaded` uses), plus
        `holidays_seeded_through`. Show the bound in the table and in the JSON
        output.
  - [ ] Add a unit test asserting the SQL references only real columns (the
        names from migrations 004 and 057), and an integration assertion in
        5.2's file that the command exits 0 and lists all three calendars.
  - [ ] Success: both tests pass
  - [ ] Effort: 1

- [ ] **7.2 Add `mt data calendars sessions`**
  - [ ] New module `cli/commands/calendar_sessions.py`, registered on
        `calendars_app` in `data.py` with a single added line.
  - [ ] Options: `--calendar` (required), `--from`, `--to` and `--json`.
  - [ ] Output is one row per session: date, open and close in the calendar's
        time zone, open and close in UTC, duration, and the exception name
        (joined from `trading_holidays`).
  - [ ] It uses `TradingCalendar.sessions_between`. An out-of-range request
        prints the `OutOfPopulatedRangeError` message and exits 1.
  - [ ] Add unit tests with a mocked calendar (table and JSON output), and an
        integration test on the throwaway database for Thanksgiving week 2024.
  - [ ] Success: the tests pass, and `uv run mt data calendars sessions --help`
        shows the options. **Commit Section 7.**
  - [ ] Effort: 2

---

## Section 8 — Real-data proof (LLD Success Criteria 8)

- [ ] **8.1 Create `scripts/verify_cme_sessions.py`**
  - [ ] Arguments: `--calendar` and one or more job directories. It opens
        files with the 220 `ITickFileReader`, one file at a time, and assigns
        each batch's `ts_event` through `SessionIndex.locate_ns`. It gets the
        index from `TradingCalendar.sessions_between` over the files' span.
  - [ ] It reports:
    - total records per job, compared with the job's `manifest.json`;
    - `outside any session: N`;
    - first and last trade per session against open and close.
  - [ ] Exit codes, per the LLD Consumes section:
    - 0: clean;
    - 1: records outside a session, printing each timestamp and the nearest
      session;
    - 2: a missing path or manifest, a decode failure, or a count mismatch.
  - [ ] Success: `--help` works, and a missing directory exits 2 naming it
  - [ ] Effort: 3

- [ ] **8.2 Run the script over both adopted jobs and record the evidence**
  - [ ] Unzip `GLBX-20250123-XT4GD5UM6C.zip` into the scratchpad, and run
        the script against it and against `GLBX-20240930-USM7UXXJBA`, with
        the walkthrough database as `MT_TIMESCALE_DB_URL`.
  - [ ] Required: exit 0, zero records outside a session, and totals equal
        each manifest.
  - [ ] Add the first and last trade times for the Labor Day,
        Thanksgiving, Black Friday and Christmas Eve 2024 sessions to
        Implementation Findings, next to each session's close.
  - [ ] A record outside a session is a seed defect. Fix the table (4.3),
        re-run 5.2, and re-run the script. Never widen a session to fit.
  - [ ] Success: evidence recorded
  - [ ] Effort: 2

- [ ] **8.3 Commit a real-data boundary fixture**
  - [ ] Extract the `ts_event` values of the first and last trades from
        8.2's sessions into `test/fixtures/calendar/cme_equity_2024_boundaries.json`,
        with a `provenance` key naming the job ids and files.
  - [ ] Add a unit test that builds a `SessionIndex` from the seeded
        sessions for those dates and asserts each fixture timestamp lands in
        its expected session. The seeded sessions come from
        `populate_trading_sessions` with `CME_EQUITY_EXCEPTIONS`, so no
        database is needed.
  - [ ] Success: the test passes. **Commit Section 8.**
  - [ ] Effort: 2

---

## Section 9 — Contract amendment (LLD D10)

All edits go to `user/reference/data-correctness-architecture.md`. Update its
`dateUpdated`.

- [ ] **9.1 Vocabulary terms and tick vocabulary rules**
  - [ ] Add the D10 terms: Session (futures), Archive unit, Ingest ledger, and
        Instrument-session complete.
  - [ ] Add the D10 rules: manifest-based completeness, `Granularity.TICK`
        versus `PassKind.TICK`, and the `granularity = 'tick'` enumeration
        list, empty as of 221.
  - [ ] Success: the terms are present, and each rule names its source
        (architecture section)
  - [ ] Effort: 1

- [ ] **9.2 Invariants I11–I14**
  - [ ] Add I11 (completeness from the manifest), I12 (provenance and
        supersession), I13 (session-assigned ticks) and I14 (futures identity
        explicit) under Invariants, worded from D10.
  - [ ] Success: four new invariant sections, numbered after I10
  - [ ] Effort: 1

- [ ] **9.3 The I7 exception, the sequence-gap move, and the renumbering**
  - [ ] Under I7, add the tick exception and its reason (D10).
  - [ ] Rewrite the Notes paragraph on "Initiative 200": it is initiative
        220 (renumbered from the concept's 200), it inherits I2–I10 except the
        I7 exception, it adds I11–I14, and sequence-gap detection is reserved
        for the realtime initiative, with the reason given.
  - [ ] Update the Purpose line and the out-of-scope lines that say "200"
        for futures tick.
  - [ ] Success: no remaining text promises sequence-gap detection for
        initiative 220 (grep `sequence`)
  - [ ] Effort: 1

- [ ] **9.4 Mapping table and the required CLI line**
  - [ ] The I4 row gains the 221 entry. The I7 row gains the tick
        exception. Add rows for I11–I14 with planned closing slices and status
        "Framed (221)" (D10).
  - [ ] Update the tick line under Required CLI commands to 220's
        vocabulary, matching the I10 row.
  - [ ] Success: the table renders (check the column count per row).
        **Commit Section 9.**
  - [ ] Effort: 1

---

## Section 10 — Final validation

- [ ] **10.1 Full checks**
  - [ ] Run `uv run ruff check` on the touched files, and `uv run mypy src
        test` in one invocation.
  - [ ] Run `uv run pytest test/unit -q`, then the integration tier
        separately. Every new test passes. Confirm any failure also fails at
        the slice base before calling it pre-existing, and list it.
  - [ ] Success: clean, or pre-existing failures listed with evidence
  - [ ] Effort: 2

- [ ] **10.2 Run the verification walkthrough and update the design**
  - [ ] Run LLD Verification Walkthrough steps 1–7 against the walkthrough
        database (see Walkthrough database above) after `mt data migrate
        apply`, then drop it, and correct the walkthrough text to the
        actual commands and output.
  - [ ] Set the slice design's `status` to `complete` and update
        `dateUpdated`.
  - [ ] Success: every step behaves as written. **Commit.**
  - [ ] Effort: 2
