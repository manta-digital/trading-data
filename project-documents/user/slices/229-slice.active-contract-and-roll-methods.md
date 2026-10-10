---
docType: slice-design
slice: active-contract-and-roll-methods
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [226]
interfaces: [230, 231, 232]
dateCreated: 20261009
dateUpdated: 20261009
status: not_started
---

# Slice Design: active-contract-and-roll-methods

## Overview

ES ticks are stored per contract (`ESU4`, `ESZ4`, …). A question about "ES"
needs a rule that says which contract counts on which session. This slice
adds those rules:

- **The vocabulary.** Two roll methods named with Databento's letters:
  calendar (`c`) and volume (`v`). Each takes a rank (`.0` is the front
  contract, `.1` the next), and calendar takes an optional offset of N
  sessions before expiry. One project default, `v.0`, is defined once.
- **The resolver.** Given a product, a rule and a session, it names the
  contract. It reads only stored state: the definitions, the ingest ledger's
  per-session volume, and the CME calendar. Each answer also names the
  session its inputs come from, so it can be re-derived later.
- **Roll-day handling.** A roll takes effect at a session boundary. One
  session never mixes two contracts.
- **The continuous view.** A per-session schedule (session → contract) over
  a range, computed at read time and never stored. 230's product-level
  `get` and 231's endpoints read through it.
- **Surfaces.** A new verb, `mt data tick active`, and roll lines in
  `mt data tick status`. Both name the contract, the rule that chose it, the
  contract's expiration, and the next roll.

**Open interest was evaluated and is deferred** (Technical Decision 6). On
all 11 ES rolls from 2023 to 2025, Databento's open-interest symbol
`ES.n.0` resolved to the same contract on the same day as its volume
symbol `ES.v.0`. Building it needs a new archived schema, a table, a
projection and a purchase. GC is where it differs, so the decision is
revisited at 232.

Nothing is bought, and there is no schema change.

## Value

- **Futures identity becomes explicit (I14).** Before this slice, nothing
  can answer "which ES contract is active?". After it, every answer names
  the contract and the rule. The data-correctness contract's I14 row gets
  its first shipped slice.
- **230 and 231 are unblocked.** Product-level reads (`get ES` and
  `/api/v1/futures/...?product=ES&roll=v.0`) need a schedule, and this slice
  provides it behind one interface.
- **The vocabulary agrees with the provider.** A test checks this slice's
  rules against Databento's own continuous symbols, on the two rolls already
  bought. A request phrased either way (`ES.v.0` sent to Databento, or
  `--rule v.0` here) gets the same contract on every session.
- **No new cost.** Volume comes from the ledger that ingest already writes,
  and calendar needs only definitions.

## Technical Scope

**Included**

- `RollMethod` enum (`CALENDAR = "c"`, `VOLUME = "v"`) and a `RollRule`
  value (method, rank, calendar offset). It parses and renders the
  Databento form `c.0` / `v.1`. `DEFAULT_ROLL_RULE` and
  `VOLUME_RANK_LAG_SESSIONS` are in `data/tick/constants.py`.
- `OUTRIGHT_CLASS` and `SPREAD_CLASS` are moved to `constants.py`. Today
  `SPREAD_CLASS` lives in `tick_status_build.py`, and resolution needs both.
- Reads: a product's outright definitions, and outright volume per
  (session, instrument) over current units of the product's shape.
- A pure resolver: given sessions, definitions, volumes and session
  completeness, return a resolution or an "unresolved" result, with its
  reason, for each session.
- The schedule: per-session answers collapsed into segments
  (first session, last session, contract), plus the next roll after a
  given session.
- `mt data tick active` (new verb), and roll lines added to
  `mt data tick status`, in both the text and `--json` output.
- Tests, including the provider-equivalence test over both held rolls,
  run from recorded fixtures.
- A script that records those fixtures from the production tick database
  and Databento's free `symbology.resolve`.
- Docs: rows added to the data-correctness contract (I10, I14), the
  architecture's Revision Log, and the slice plan's "superseded" notes.

**Excluded**

- The open-interest method `n` (Technical Decision 6; slice plan Future
  Work item 2).
- Back-adjustment of prices across rolls. The architecture allows it only
  as an opt-in transform at read time. Nothing asks for it yet.
- Reading ticks or bars through a rule (`get`): that is 230. API
  endpoints: that is 231.
- Requesting data from Databento with continuous symbols. Acquisition stays
  on `ES.FUT` parent requests.
- Spreads. They are never a roll candidate.

## Dependencies

### Prerequisites

- **226 (complete):** the production tick database `trading_tick` holds
  both adopted ES jobs, ingested and checked: trades for 2024-08-30 → 09-29
  and tbbo for 2024-11-01 → 12-31. Each range contains a quarterly roll.
- **225 (complete):** the ingest ledger, with one row per
  (unit, instrument, session) including zero-record rows; session
  verdicts (`session_verdicts`, `TickSessionStatus.COMPLETE`); and the
  shape predicate over current units (`_SHAPE_WHERE`).
- **224 (complete):** definitions are projected into `tick_definition`, with
  `asset`, `instrument_class`, `raw_symbol` and the validity window.
- **221 (complete):** the `CME_EQUITY` calendar on the production database,
  `TradingCalendar.sessions_between`, and `session_containing`.
- **220 (complete):** `ITickMetadataProvider.resolve_symbols`. It is used
  only by the fixture script, never at run time.

### Interfaces Required

- `tick_definition`: `instrument_id`, `raw_symbol`, `asset`,
  `instrument_class`, `activation_ns`, `expiration_ns`, `unit_id`.
- `tick_ingest_ledger`: `instrument_id`, `session_date`, `volume`, joined to
  current units of the shape (`COVERAGE_PREDICATE`).
- The production calendar's sessions for the product
  (`calendar_for_product`). This is the existing tick → production edge
  that `status` and `coverage` already use. No new edge is added.

## Architecture

### Component Structure

All new code goes in `src/manta_trading/data/tick/`, plus one CLI module.

| Module | Role | I/O |
|---|---|---|
| `roll_rules.py` | `RollMethod`, `RollRule` (validate, `parse`, `symbol(product)`) | none |
| `roll_inputs.py` | `outright_definitions(conn, product)`, `session_volumes(conn, shape, first, last)` | tick DB |
| `roll_resolve.py` | `resolve_sessions(rule, sessions, contracts, volumes, complete)` → `list[SessionAnswer]`; `segments(...)`; `next_roll(...)` | none (pure) |
| `active_contract.py` | `build_schedule(conn, calendar_url, entry, rule, start, end)` → `RollSchedule`; `latest_active(...)` for status | tick DB + calendar |
| `cli/commands/tick_active_render.py` | Rich and JSON rendering for `active` | — |

Changes to existing modules:

- `cli/commands/tick_store_cmds.py` registers `active`.
- `tick_status_build.py`: `ProductStatus` gains
  `rolls: tuple[ActiveLine, ...]`, and `SPREAD_CLASS` moves to `constants`.
- `tick_status_render.py` prints the roll lines.
- `tick_exit.py`: the comment on `EXIT_PARTIAL` widens to cover sessions
  `active` could not resolve.

`roll_resolve.py` is pure, so the provider-equivalence test, the rank and
offset tests, and the edge cases all run with no database.

### Data Flow

```
mt data tick active --product ES --rule v.0 --start S --end E
  └─ active_contract.build_schedule
       ├─ calendar (prod DB, to_thread): sessions in [S − lag sessions, E),
       │   plus the session of each candidate's expiration
       ├─ roll_inputs.outright_definitions(tick DB)   asset = product, class F
       ├─ roll_inputs.session_volumes(tick DB)         ledger, current units, outrights
       ├─ status_reads/session_verdicts(tick DB)       which sessions are COMPLETE
       └─ roll_resolve.resolve_sessions (to_thread)   → per-session answers
            → segments → render (text | JSON)
```

`status` calls `latest_active` once per rule in `STATUS_ROLL_RULES`. That
resolves the latest complete session and the next roll after it. It reuses
the verdicts `status` has already computed, so there is no second
classification pass.

### State Management

The slice adds no stored state. The answer for a session is a pure function
of three things:

- stored definitions;
- ledger volume over current units, which re-ingest can recompute from the
  raw table;
- the calendar.

That is the architecture's reproducibility rule. A schedule is never stored
or cached. Supersession changes which units are current, so it can change a
volume; because the answer is computed at read time, the change shows up
on the next read.

## Technical Decisions

### TD1. Vocabulary: Databento's letters, a rank, and an offset for calendar only

```python
class RollMethod(StrEnum):
    CALENDAR = "c"   # nearest expiration
    VOLUME = "v"     # highest traded volume (Databento ranks by published daily stats)

@dataclass(frozen=True)
class RollRule:
    method: RollMethod
    rank: int = 0             # 0 = front, 1 = next, … (Databento's depth index)
    offset_sessions: int = 0  # calendar only: roll N sessions before expiry
```

- Text form: `{method}.{rank}`, for example `v.0` and `c.1`. A rule
  applied to a product is written `ES.v.0`, which is exactly Databento's
  continuous symbol. Parsing is lenient about case and surrounding
  whitespace. An unknown method fails and lists the known ones
  (`unknown roll method 'n'; known: c, v`). A negative rank or offset
  fails, and so does an offset on `v`.
- The offset has no Databento symbol. A non-zero offset renders as
  `c.0+5s`, so a status line never looks like a provider symbol the
  provider would answer differently. `+5s` means five sessions before
  expiry.
- The offset is counted in sessions, not calendar days, because the whole
  model is keyed by session. A day count would land on weekends and
  holidays.
- Value-object validation follows `TickUniverseEntry`: an invalid rule
  raises `ValueError` naming the field.

### TD2. Calendar rule

Candidates for session S are outright definitions (`asset = product`,
`instrument_class = OUTRIGHT_CLASS`) whose window has started by S's open
(`activation_ns ≤ S.open`). Let E(c) be the contract's expiration session:
the last session that opens at or before `expiration_ns`. A contract is
eligible for S when `index(S) ≤ index(E(c)) − offset_sessions`. Eligible
contracts are ordered by `expiration_ns`, and rank r takes the r-th.

With offset 0 a contract stays front through its expiration session. ESU4
expired 2024-09-20 13:30 UTC, so it is front on the session dated 09-20,
and ESZ4 is front from the session dated 09-23. Databento's `ES.c.0`
switches at its date 2024-09-22, a Sunday with no session dated on it, so
the two agree on every session date.

Any candidate's E(c) that falls outside the populated calendar is an
explicit error naming the contract and the remedy (`mt data extend`). It is
never treated as "not expiring".

### TD3. Volume rule: rank by the session two before, as the provider does

For session S, eligible contracts are those of TD2 with offset 0 (listed,
and not past their expiration session). They are ranked by outright volume
in session S − `VOLUME_RANK_LAG_SESSIONS`, where the lag is 2 and counted
in calendar sessions. Ties are broken by earlier expiration.

**Why 2, measured 2026-10-09.** The ledger's ES volume crossed on the
session dated 2024-09-16 (ESZ4 645,285 against ESU4 461,273). It crossed
again on 2024-12-16 (ESH5 754,546 against ESZ4 451,208). Databento's
`ES.v.0` switches at 2024-09-18 and 2024-12-18, two sessions later in both
cases. A lag of 1 would roll a session early on both, which would break the
equivalence the architecture requires.

The likely cause is that the provider ranks by CME's published daily
statistics, and the figures for session D are published during D + 1. The
lag is one named constant. The equivalence test (TD8) is what holds it in
place, so a change of convention at the provider shows up as a failing
re-record, not as silent drift.

**Inputs must be complete, never guessed.** If session S − 2 is not
`COMPLETE` (225's verdict over the product's shape), S is unresolved, for
example:

`volume inputs: session 2024-11-01 is missing`

The resolver never falls back to a partial volume, to calendar, or to the
previous answer.

An eligible contract with no ledger row in a complete session is an error
naming the contract. The ledger writes a row for every defined instrument
in every session it touches, zero-record rows included (I13). It holds 21
outright rows in each sampled session on `trading_tick`, so a missing row
is a broken invariant and is never read as volume 0.

### TD4. Roll-day handling (the stated default)

- A rule answers for a whole session. The roll session is the first
  session on which the new contract is active. One session is never split
  between two contracts.
- A product-level read for a session serves only that session's active
  contract. Both contracts' ticks are never merged or interleaved across
  the roll. 230 inherits this as its default and states any opt-in.
- A contract-level read (`ESZ4`) skips resolution and serves every held
  session of that contract.
- Provider dates and sessions are matched by session date. Databento's
  mapping intervals are by date. Comparing on session dates agreed on every
  session of both rolls (TD8). Ticks between a session's open, about
  22:00 UTC, and the next midnight UTC are the one window where a stream
  requested with a continuous symbol could differ. Acquisition never
  requests by continuous symbol, so that window never reaches stored data.

### TD5. Default rule: `v.0`

`DEFAULT_ROLL_RULE = RollRule(RollMethod.VOLUME, 0)` is defined once in
`constants.py`. Every surface that accepts a rule (here `active` and
`status`; later 230's `get` and 231's endpoints) defaults through it.

- **Why volume.** It follows liquidity, which is what "the ES contract"
  means for research. Calendar keeps ESU4 front through 2024-09-20, when it
  traded 10,898 contracts against ESZ4's 1,431,283. For GC (232), calendar
  is not usable at all: `GC.c.0` steps through every monthly contract from
  2023 to 2025, most of them thinly traded, while `GC.v.0` holds the
  bi-monthly active contracts.
- **Its cost.** A session is resolvable only when the session two before it
  is held complete. The first two sessions of a held range, and the two
  after a gap, are unresolved. They are reported, never filled.

### TD6. Open interest: evaluated, deferred

**Evidence.** `symbology.resolve` (free) was run on 2026-10-09:

- For ES, `ES.n.0` matched `ES.v.0` on all 11 rolls from 2023-03 to
  2025-09.
- For GC, `GC.n.0` rolls 6–12 days before `GC.v.0`, for example
  2024-11-21 against 2024-11-29.

**What shipping it would take:**

- a `STATISTICS` member in `TickSchema` and `ARCHIVED_SCHEMAS`, with a
  tick-track migration to re-render the request CHECK;
- a statistics table;
- a projection;
- universe entries for purchasing statistics;
- a purchase through 224's path. The Standard plan is not subscribed, so
  this is a paid purchase outside the plan.

**Decision.** Defer. For ES the rule adds no distinct answer on any roll
the provider reports, and the work is a slice of its own. Slice plan Future
Work item 2 stays, with this evidence attached. 232 (GC) revisits it, since
GC is where open interest differs from volume.

The vocabulary leaves `n` unassigned, so adding it later is a new enum
member and a new input. No existing rule's meaning changes.

### TD7. Surfaces

**`mt data tick active`** is provider-free and needs no API key, no archive
directory and no lock (the same as `status`):

```
mt data tick active [--product P] [--rule c.0|v.0|…] [--offset N]
                    [--start D --end D] [--json]
```

- Without `--start`/`--end` it prints, for each product and each rule
  (`--rule`, or every rule in `STATUS_ROLL_RULES`), the answer for the
  latest complete session:

  ```
  ES  v.0 (default)  ESH5 (5002)  exp 2025-03-21 13:30 UTC   session 2024-12-31
      inputs: volume of session 2024-12-27; definitions as of 2024-12-31
      next roll: not forecast (volume); no later than session 2025-03-24
  ES  c.0            ESH5 (5002)  exp 2025-03-21 13:30 UTC   session 2024-12-31
      next roll: session 2025-03-24 → ESM5 (4916)
  ```

  The forecast in "next roll" comes from definitions and the calendar,
  never from data not yet held.
- With a range it prints the segments, where the end is an exclusive
  session date as in `coverage`:

  ```
  ES  v.0   2024-09-09 → 2024-09-17  ESU4 (118)
            2024-09-18 → 2024-09-27  ESZ4 (183748)
  ```

  Unresolved sessions are listed with their reasons.
- Exit codes come from `tick_exit.py`: 0 when every session resolved; 3
  when any session in the range is unresolved (output still printed); 1
  for a bad rule or an unknown product; 4 when the tick database or the
  calendar is unreachable.
- `--json` carries the same fields: `product`, `rule`, `is_default`, and
  `answers`/`segments` with `session_date`, `instrument_id`, `raw_symbol`,
  `expiration` (ISO UTC), `basis_session` and `definitions_as_of`; plus
  `unresolved` (each with `session_date` and `reason`), `next_roll` and
  `exit_code`.

**`mt data tick status`** gains one line per rule in
`STATUS_ROLL_RULES = (DEFAULT_ROLL_RULE, RollRule(RollMethod.CALENDAR))`,
in the same form as `active` without a range, under the existing contract
lines. Status still never scans `tick_trade`.

**Does this belong in the API?** Yes: the active contract per rule and the
schedule are part of 231's contract catalog. 231 serves them through
`build_schedule` and `latest_active`. This slice adds no route.

### TD8. Provider equivalence is a test on recorded real inputs

`scripts/record_229_roll_fixture.py` is a one-off that the developer runs.
It reads the production tick database (read-only `SELECT`) and calls the
free `symbology.resolve` endpoint. It writes three JSON fixtures under
`tests/fixtures/tick/roll/`:

- ES outright definitions;
- ES outright volume per session, over both held ranges;
- Databento's intervals for `ES.c.0`, `ES.c.1`, `ES.v.0` and `ES.v.1` over
  2024-08-30 → 2025-01-02.

The calendar's sessions for that span are recorded too. Tests never read
`MT_TICK_DB_URL` or call Databento.

The unit test runs `resolve_sessions` over the recorded inputs. For every
session it resolves in the held ranges, the result must equal the
contract Databento's interval maps on that date. Both rolls fall inside
those sessions. The sessions it cannot resolve are asserted exactly: they
are the sessions whose lag session is not complete in the recorded
verdicts. These are the first two sessions of each held range (for tbbo,
11-04 and 11-05) and the sessions in the gap between the ranges.

Synthetic unit tests cover:

- ties;
- an expired contract dropping out of rank 1;
- offsets 0, 1 and 5;
- a missing ledger row raising;
- an incomplete lag session being unresolved;
- a rule's text form surviving parse and render unchanged.

### Realtime paths

Neither path is ruled out. The volume rule reads only the ledger, so
whichever path writes ledger rows feeds it:

- **Path A** (assemble from realtime capture): a realtime writer that keeps
  the ledger's per-session volume feeds the rule.
- **Path B** (historical data after the lag): this is the shipped path.

The lag of 2 sessions means a live session's answer never depends on that
session's own trades.

### Patterns and Conventions

- Every comparison value is defined once in `data/tick/constants.py`: the
  method letters, the default rule, the lag, the status rule set, and the
  instrument classes.
- Reads live in `roll_inputs.py` and resolution in `roll_resolve.py`, which
  does no I/O. Calendar reads and resolution run through
  `asyncio.to_thread`, as `status` does.
- An unresolved session is a value with a reason, not an exception. A
  broken invariant (a missing ledger row, an expiration outside the
  calendar) raises.
- The tick pass contract is unchanged. A named task confirms the
  Kalshi-diff test still passes.

## Implementation Details

### API Contracts

The resolver interface that 230 and 231 consume:

```python
@dataclass(frozen=True)
class ActiveContract:
    session_date: date
    instrument_id: int
    raw_symbol: str
    expiration: datetime          # UTC
    basis_session: date           # volume: S − lag; calendar: S
    definitions_as_of: date       # latest ingested definition unit day

@dataclass(frozen=True)
class Unresolved:
    session_date: date
    reason: str

@dataclass(frozen=True)
class RollSchedule:
    product: str
    rule: RollRule
    answers: tuple[ActiveContract | Unresolved, ...]   # one per session, in order
    def segments(self) -> tuple[Segment, ...]: ...      # consecutive same-contract runs

async def build_schedule(conn, calendar_url, entry, rule, start: date, end: date) -> RollSchedule
async def latest_active(conn, calendar_url, entry, rule, verdicts) -> ActiveLine
```

`ActiveLine` is what `status` and `active` render for one rule:

- the latest complete session and its `ActiveContract`, or the reason it is
  unresolved;
- `next_roll`: for calendar, `(session_date, ActiveContract)`; for volume,
  `None`, with `roll_no_later_than` set to the first session after the
  contract's expiration session.

## Integration Points

### Provides to Other Slices

- **230:** `build_schedule` for `get` by product and rule, the roll-day
  default (TD4), `DEFAULT_ROLL_RULE`, and `RollRule.parse` for the CLI's
  `--rule`.
- **231:** the same interface for the contract catalog's
  "active contract per rule" and for product-level endpoints, plus the
  `RollRule` text form for query parameters.
- **232:** the rules work for GC with no code change once GC's definitions
  and ledger exist. The open-interest question is handed over with TD6's
  evidence.

### Consumes from Other Slices

- **225:** session verdicts and the ledger. If verdict semantics change,
  resolution follows them, because it calls the same function.
- **221:** the production calendar. When it is unreachable, `active` and
  `status` exit 4, as `status` already does. A session outside the
  populated span is an explicit refusal naming `mt data extend`.

## Success Criteria

### Functional Requirements

1. `RollRule.parse` accepts `c.N` and `v.N` (lenient case and whitespace)
   and refuses an unknown method (listing `c, v`), a negative rank, and an
   offset on `v`, each with a named message.
2. The calendar rule on `trading_tick` gives ESU4 through session
   2024-09-20 and ESZ4 from 2024-09-23. It gives ESZ4 through 2024-12-20
   and ESH5 from 2024-12-23.
3. The volume rule gives ESU4 through session 2024-09-17 and ESZ4 from
   2024-09-18. It gives ESZ4 through 2024-12-17 and ESH5 from 2024-12-18.
4. A session whose lag session is not complete is unresolved, with a reason
   naming that session and its status. It is never filled.
5. `--offset 5` with `c.0` rolls five sessions earlier than `c.0`.
6. `mt data tick active` without a range prints, for each rule, the
   contract, the rule, the expiration, the session it answers for, its
   inputs, and the next roll (calendar) or the no-later-than bound
   (volume).
7. `mt data tick status` shows the same lines for `v.0` (marked default)
   and `c.0`.
8. `active` exits 3 when a ranged query has an unresolved session, 1 on a
   bad rule, and 4 on an unreachable database or calendar.
9. The default rule is read from `DEFAULT_ROLL_RULE` everywhere. A grep
   finds no second spelling of `"v"`/`"c"` as a rule outside `constants.py`
   and the tests.

### Technical Requirements

- `roll_resolve.py` does no I/O, and its tests need no database.
- The provider-equivalence test (TD8) passes on recorded real fixtures for
  `c.0`, `c.1`, `v.0` and `v.1` over both rolls. The fixture script is
  committed and its run is recorded in `user/notes/`.
- Integration tests on a scratch tick database cover `roll_inputs`
  (current units only, outrights only, supersession honoured) and the
  missing-ledger-row error.
- ruff, mypy and pyright are clean on touched files. Files are about 300
  lines or fewer.
- The data-correctness contract is updated: the I14 row gets 229's
  delivery, and the I10 row gets the `active` verb. The architecture's
  Revision Log gets an entry for TD3, TD4, TD5 and TD6, and the slice plan
  gets a "superseded by 229" note: the lag, sessions-based offsets, and
  open interest deferred.
- The Kalshi pass-contract diff test still passes (the standing
  obligation; this slice does not touch the contract).

### Integration Requirements

- 230 can serve `get --product ES` using `build_schedule` with no further
  change to resolution.
- `status --json` keeps every existing field and adds `rolls`.

### Verification Walkthrough

These steps run on manta9000 against `trading_tick`. The expected values
come from the ledger and from Databento's resolve, both read on
2026-10-09.

1. **The default answer and the next roll.**
   ```bash
   uv run mt data tick active --product ES
   ```
   Expected:
   - `v.0 (default)` and `c.0` both name `ESH5 (5002)` for session
     2024-12-31, expiring 2025-03-21 13:30 UTC.
   - The `v.0` line gives its inputs as the volume of session 2024-12-27.
   - `c.0`'s next roll is session 2025-03-24 → ESM5 (4916).
   - `v.0` says "no later than session 2025-03-24".
   - Exit 0.

2. **The September volume roll.**
   ```bash
   uv run mt data tick active --product ES --rule v.0 --start 2024-09-09 --end 2024-09-28
   ```
   Expected: two segments, ESU4 through 2024-09-17 and ESZ4 from
   2024-09-18, matching Databento's `ES.v.0` (d0 2024-09-18). Exit 0.

3. **The same range under calendar, and with an offset.**
   ```bash
   uv run mt data tick active --product ES --rule c.0 --start 2024-09-09 --end 2024-09-28
   uv run mt data tick active --product ES --rule c.0 --offset 5 --start 2024-09-09 --end 2024-09-28
   ```
   Expected:
   - Plain `c.0`: ESU4 through 2024-09-20, ESZ4 from 2024-09-23
     (Databento d0 2024-09-22, a Sunday).
   - With the offset (rule rendered `c.0+5s`): ESU4 through 2024-09-13 and
     ESZ4 from 2024-09-16, five sessions before plain `c.0`'s roll.

4. **The December roll and rank 1.**
   ```bash
   uv run mt data tick active --product ES --rule v.1 --start 2024-12-13 --end 2024-12-24
   ```
   Expected:
   - ESH5 through 2024-12-17;
   - ESZ4 on 12-18 through 12-20 (ranked second by volume while still
     listed);
   - ESM5 from 12-23.

   This matches Databento's `ES.v.1`.

5. **Inputs that are not held are reported, not guessed.**
   ```bash
   uv run mt data tick active --product ES --start 2024-11-01 --end 2024-11-08; echo "exit $?"
   ```
   Expected: sessions 11-04 and 11-05 are unresolved, each naming its lag
   session (10-31, then 11-01) and that session's status. ESZ4 is the
   answer from 11-06 on. `exit 3`.

6. **Bad rules fail loudly.**
   ```bash
   uv run mt data tick active --rule n.0; echo "exit $?"
   uv run mt data tick active --rule v.0 --offset 2; echo "exit $?"
   ```
   Expected: `unknown roll method 'n'; known: c, v`, then the offset
   refusal. Both `exit 1`.

7. **Status names the active contract.**
   ```bash
   uv run mt data tick status --product ES
   uv run mt data tick status --product ES --json | jq '.products[0].rolls'
   ```
   Expected: the two roll lines from step 1 under the contract list, and a
   JSON `rolls` array with `rule`, `is_default`, `instrument_id`,
   `raw_symbol`, `expiration`, `basis_session` and `next_roll`.

8. **Provider equivalence is tested.**
   ```bash
   uv run pytest tests/unit/data/tick -k roll -q
   ```
   Expected: everything passes, including the recorded-fixture equivalence
   test for `c.0`, `c.1`, `v.0` and `v.1`.

## Risk Assessment

### Technical Risks

- **The provider's volume convention is inferred from two rolls of held
  data.** A lag of 2 matches both. The 11 ES rolls Databento reports from
  2023 to 2025 all switch on a Wednesday or Thursday of expiration week,
  which is consistent with it, but only the two held rolls can be checked
  against our own volume.

### Mitigation Strategies

- The lag is one constant, and the recorded-fixture test pins it. When 232
  or a plan-year purchase brings more rolls into the ledger, the fixture
  script is re-run over them. A disagreement fails the test, and the
  convention is revisited, not patched per roll.

## Implementation Notes

### Development Approach

1. Constants (`RollMethod`, the default, the lag, the status rule set, the
   instrument classes) and `roll_rules.py`, with parse and render tests.
2. `roll_resolve.py`, pure, with synthetic tests: ranks, ties, expiry
   drop-out, offsets, unresolved lag sessions, and the missing-row error.
3. The fixture script, run once on manta9000. Commit its fixtures and the
   provider-equivalence test.
4. `roll_inputs.py` and `active_contract.py`, with integration tests on a
   scratch tick database.
5. The `active` verb and its renderer, then the status roll lines (text and
   JSON).
6. The walkthrough on `trading_tick`, recorded in `user/notes/`.
7. Doc rows: the contract (I10, I14), the architecture's Revision Log, and
   the slice plan notes. Then the Kalshi-diff test check.

Commit at each numbered step's checkpoint.

### Special Considerations

- `asset` equals the universe product key for ES (`ES`). A product with no
  outright definitions under `asset = product` is an explicit error naming
  the product, never an empty answer. 232 confirms this holds for `GC`.
- Production data is read-only here. The fixture script issues `SELECT`
  statements and free metadata calls only.
