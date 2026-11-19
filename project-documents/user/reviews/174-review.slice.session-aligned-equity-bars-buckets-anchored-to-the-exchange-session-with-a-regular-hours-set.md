---
docType: review
layer: project
reviewType: slice
slice: session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md
aiModel: claude-haiku-5-5
aiProfile: sdk
status: complete
dateCreated: 20261119
dateUpdated: 20261119
reviewedSha: 27bdd58cb8f6603427be64775307aee1f8178f20
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 29
effort: xhigh
turns: 30
promptTokens: 2641696
cachedTokens: 2399838
cacheWriteTokens: 241826
completionTokens: 105408
durationSeconds: 480.6
costUsd: 0.611608
costBasis: provider
squadronVersion: 0.24.34
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "Dates the calendar does not cover return empty bars without an error"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:219"
  - id: F002
    severity: concern
    category: architecture
    summary: "Session reads bypass the DB read layer and apply adjustment in the route"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:82"
  - id: F003
    severity: concern
    category: correctness
    summary: "The intraday half of #28 is delivered for session reads only"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:24"
  - id: F004
    severity: concern
    category: architecture
    summary: "The 140 amendment covers the caggs section only, but four 140 contracts change"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:44"
  - id: F005
    severity: concern
    category: correctness
    summary: "Session admission undercounts extended bars and leaves the minute ceiling undefined"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:148"
  - id: F006
    severity: concern
    category: correctness
    summary: "The calendar migration does not reconcile existing gap rows"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:205"
  - id: F007
    severity: concern
    category: architecture
    summary: "The session aggregate is a new OHLCV definition with no standing parity check"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:167"
  - id: F008
    severity: concern
    category: scope
    summary: "D4 assumes the #78 answer that the plan says must come first"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:283"
  - id: F009
    severity: concern
    category: testing
    summary: "The load-test latency bound is unstated and not tied to the serving timeout"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:241"
  - id: F010
    severity: concern
    category: proportionality
    summary: "Extended hours are included without the cost argument the plan requires"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:183"
  - id: F011
    severity: concern
    category: proportionality
    summary: "Every default bar gains two null keys"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:192"
  - id: F012
    severity: note
    category: dependencies
    summary: "Dependencies omit slices 221 and 301, and some response contracts are cited without a source"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:6"
  - id: F013
    severity: note
    category: dependencies
    summary: "Migration 067 is hard-coded"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:61"
  - id: F014
    severity: pass
    category: correctness
    summary: "Phase and bucket arithmetic agree with the walkthrough"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:118"
  - id: F015
    severity: pass
    category: architecture
    summary: "Read-time aggregation and explicit calendar rows follow the architecture"
    location: "project-documents/user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md:39"
---

# Review: slice — slice 174

**Verdict:** CONCERNS
**Model:** claude-haiku-5-5

## Findings

### [CONCERN] Dates the calendar does not cover return empty bars without an error

The Consumes section says "A session date with no row returns no bars. That is correct for a holiday." An absent row means either a holiday or a date past the calendar horizon, and the design does not separate them. Slice 144 makes out-of-horizon lookups raise `OutOfHorizonError` ("fail loud, not a silent fallback", 144 lines 296–297). The same layer's precedent in slice 301 raises for dates outside the calendar's coverage (301 line 278). The write side is already guarded: slice 221 clamps `extend_calendar_sessions` to `holidays_seeded_through` (221 lines 53, 197, 345), so 2029 sessions will not be written. The read side is not guarded. The new reader joins `trading_sessions` directly, so a 2029 request returns `count: 0`. Extended reads have a second silent path: line 200 allows NULL extended bounds, and the join on line 93 then drops those minutes. The only failure path the design lists for the new read is the 504 timeout. The fix is to resolve sessions through the calendar layer's session index, return a structured 422 for a range past the bound, treat a NULL extended bound on an extended read as an error, and keep "closed holiday" distinct from "out of horizon".

### [CONCERN] Session reads bypass the DB read layer and apply adjustment in the route

The component table puts the reader in `market/session_bars.py` and the dispatch in the route's `_fetcher` (line 82). The data flow then applies `adjusted()` in the route (line 95). The architecture says the DB read methods call `adjusted()` before returning (140 lines 1746–1749). The serving architecture says route handlers are thin wrappers over the existing `TimescaleMinuteDataDB` methods (180 lines 39, 262, 283). The result is two adjustment call sites, one inside `get_minute_data` and one in the route for session mode. A caller using the DB layer for session bars must repeat the adjustment step itself. The fix is a session read method on `TimescaleMinuteDataDB`, beside `get_minute_data`, with the same `adjusted` argument and the adjustment applied inside it.

### [CONCERN] The intraday half of #28 is delivered for session reads only

The Overview says the slice applies split and dividend adjustment by session date, "the intraday half of #28". D5 keeps the UTC-date rule for existing callers ("Existing callers pass nothing and are unchanged", line 143). A 1m or 1h `session=utc` bar on the evening before a winter ex-date therefore still takes the next day's factor. The same minute in `session=extended` takes the correct one. Plan item (4) asks for split adjustment by session date without limiting it to a new mode, and criterion 7 (line 231) tests session bars only. Either apply the session-date rule to every intraday read in this slice and call out the default-path change, or narrow the Overview claim, name the follow-up issue, and add a criterion that `utc` and `extended` give the same factor for the same minute.

### [CONCERN] The 140 amendment covers the caggs section only, but four 140 contracts change

Included item 6 limits the 140 note to the equity caggs section. The design also changes these contracts. The `adjusted()` date rule (140 line 1739, "per bar's date"). The `prints=all` rule (140 lines 814–816, "Raw prints (`prints=all`) are served at 1m only"; 1785–1790), which D6 (line 151) relaxes in session mode. The calendar contract (140 line 1802, "trading_calendar (consumed unchanged)"), through new `trading_sessions` columns and explicit holiday rows. The `/api/v1/bars` contract, through `session`, `session_date` and `phase`. The bars route is governed by the data-serving architecture (180), which the design does not cite. Without these changes recorded, the architecture will contradict the code after release.

### [CONCERN] Session admission undercounts extended bars and leaves the minute ceiling undefined

D6 says a 4h extended request over five years returns "about 5,000 bars". Under D2's phase rule, each extended 4h session has five bars: pre at 04:00 and 08:00–09:30, regular at 09:30 and 13:30–16:00, and post at 16:00. Five years is about 1,260 sessions, so the count is about 6,300. The 5,000 figure matches slice 186's row estimator (960 minutes ÷ 240 = 4 per day, 186 line 225). D6 does not say which estimator applies to the row ceiling in session mode. The 186 estimator also over-counts regular 1h (16 per session against 7) and under-counts extended 1h (16 against 17). Separately, the minute ceiling's default is "set from a production measurement in this slice" (line 149), with no method and no acceptance target. Its literals (390, 960) contradict Patterns line 172 ("not from literals") and duplicate 186's `INTRADAY_MINUTES_PER_TRADING_DAY = 960`. Slice 186 D9 (lines 416–418) requires policy ceilings to live in `constants.py` with a `Settings` field, which the design does not say. Define the session row estimate per phase and per bucket, reuse 186's constant, and state the measurement target before a default is chosen. For example, the one-year regular read must finish under the 20 s serving timeout.

### [CONCERN] The calendar migration does not reconcile existing gap rows

The migration adds 2021-12-31 to the calendar, removes 2025-01-09, and removes the 2027–2028 holidays. The architecture says `data_gaps` never stores HOLIDAY ranges (140 lines 55–57), and gap rows change only when the daemon recomputes a range. The dry check and pinned list cover `trading_sessions` only (line 205). The mitigation at line 277 does not mention `data_gaps`. If a gap row already exists for 2025-01-09, it survives the migration. If the store lacks bars for 2021-12-31, no gap row exists for it until a recompute covers it. Specify the reconciliation step. Delete gap rows for dates that stop being sessions, recompute gaps over each date that becomes a session through the gap writer, and include the affected gap rows in the dry check.

### [CONCERN] The session aggregate is a new OHLCV definition with no standing parity check

"First and last values by `array_agg(... ORDER BY time)`, as `bar_reads` does" describes copying an existing aggregation. The architecture says definitional drift in a rollup is invisible to `verify` (140 lines 790–800). That is why it added the `value parity` health check (lines 817–821). The session reader is a fourth aggregation definition. Its correctness is checked only by tests (criterion 5 and the integration tests), not by a standing check. Build the session aggregate from the same rendered expressions the eligible rollups use, so there is one definition. Then either add the session read to value parity or record why it does not need it.

### [CONCERN] D4 assumes the #78 answer that the plan says must come first

Line 283 says "D4 does not depend on its answer, but the docs do." Plan item (5) says to find out what the close-stamp bar holds and which session it belongs to "before any session bucket counts its volume." D4 excludes the bar "whatever it turns out to be" (line 133). Line 48 defers the regular-hours daily bar for exactly this reason: its close "would be the last regular minute's close, not the official close, which is exactly what #78 has to settle first." The 15:30 regular 1h bar has the same open question, because its close and last-bar volume depend on what the close-stamp bar holds. The design defers one and ships the other. State which finding would change the 15:30 bar, or make the 1h bar's close and volume wait on the #78 note.

### [CONCERN] The load-test latency bound is unstated and not tied to the serving timeout

"Holding a latency bound" and "`/health` stays responsive" have no numbers. Slice 186 sets the API serving statement timeout to 20 s (186 line 116) and maps a cancelled query to `504` (186 D10). The session read's bound should be stated against that timeout, and the `/health` responsiveness bound needs a number too. The architecture states no bars latency requirement (140's NFRs cover `data_status`), so this slice has to state its own. For example, the one-year regular 1h read completes under N seconds at p95, with the 20 s timeout as the hard limit.

### [CONCERN] Extended hours are included without the cost argument the plan requires

Plan item (2) reads "a regular-hours set, plus extended hours if it's cheap." The design adds extended mode in D2, the API table and admission. D6 shows it is the costliest read: about 1.2M minutes for 4h over five years. The Value section (line 31) names extended bars for trading-engine, but the design gives no cost argument and no measurement. Success criteria 1, 2 and 4 test regular only. Deferring extended removes the pre and post phases, the 960-minute estimate and the second ceiling path. The regular phase logic and the minute ceiling stay. Unless issue #38 explicitly asks for extended hours, defer them or show the cost in the design.

### [CONCERN] Every default bar gains two null keys

Line 192 says both fields are `null` with `session=utc`, and criterion 8 (line 232) accepts the default response changing "apart from the two new `null` fields". Those keys appear on every bar of every default response. The serving architecture records a ceiling-sized bars response at 11.58 MB (180 line 325), about 154 bytes per bar. The two keys add about 33 bytes per bar, roughly 20% more payload at the ceiling. The architecture already treats payload size as a constraint (180 line 226). Return the two fields only on session responses, through a separate response model or by omitting them when `session=utc`, or record the cost.

### [NOTE] Dependencies omit slices 221 and 301, and some response contracts are cited without a source

The frontmatter lists dependencies [144, 171, 173]. Slice 221 (complete) owns `holidays_seeded_through` and `extend_calendar_sessions`, which D8 raises and the migration relies on. Slice 301 (complete) owns migration 066, which 067 follows. The design also cites `applied`, `AppliedEquityBarParams`, `bars_admission.check_request` and `latest_bars.read_latest` as existing. No slice or architecture doc in the plan describes them. Add 221 and 301 to the dependencies, and name the contract being extended: 182 for the bars response and 186 for admission.

### [NOTE] Migration 067 is hard-coded

"Migration 066 is the latest. 067 follows it." This holds today because slice 301 is complete and owns 066. Slice 301's own design says to read the last id in `MINUTE_MIGRATIONS` at implementation rather than trust a number in a plan (301 line 211). Apply the same rule here.

### [PASS] Phase and bucket arithmetic agree with the walkthrough

Half-open phases with a per-phase anchor give 7 regular 1h bars starting at 09:30 ET, 4 bars on the 13:00 early close (last one at 12:30), and 6 pre + 7 regular + 4 post = 17 for extended. The DST steps (14:30Z and 13:30Z) follow from the anchor. These match criteria 1–4 and walkthrough steps 1–4.

### [PASS] Read-time aggregation and explicit calendar rows follow the architecture

Not adding caggs (D1) keeps stored aggregates to the seven in 140's post-152 schema (140 line 1753), and adjustment stays on read. The explicit dated holiday rows follow the pattern slices 221 and 301 set: "nothing is generated from a holiday rule" (301 line 44). The 140 amendment gap is covered in the finding above.

### Run Digest

- Response length: 18382 chars
- Response is newline-free: no
- Tool calls made: 29
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: xhigh
- Turns: 30
- Tokens — prompt / cached / cache write / completion / reasoning: 2641696 / 2399838 / 241826 / 105408 / not computed
- Duration: 480.6 s
- Cost: $0.61 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 15
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 15
- Finding-shaped matches — surviving validation: 15

## Response (2026-11-19)

Verdict CONCERNS passes the gate. Each finding was addressed in the design, except where it says "kept".

- **F001 calendar horizon:** accepted. D6: a range past `holidays_seeded_through` is a `422` (`calendar_horizon`). A NULL extended bound on an extended read is a logged `500`.
- **F002 read layer:** accepted. `TimescaleMinuteDataDB.get_session_bars` with adjustment inside. The route stays a thin wrapper.
- **F003 #28 on the default path:** accepted, more simply than proposed. D5 takes the factor from the bar's exchange-local date on every intraday read. Every stored minute, the close stamp included, shares its session's ET date. Criterion 7 checks `utc` and `extended` agree.
- **F004 amendments:** accepted. Included item 6 lists the 140 caggs, `adjusted()` date rule, `prints=all` and calendar contract changes, plus the 180 bars contract.
- **F005 admission:** accepted. Per-phase bar estimate, minute estimate from `trading_sessions` bounds, ceiling as constant plus `Settings` (186 D9), and a measurement target: p95 within 5 s, a quarter of the 20 s timeout.
- **F006 gap rows:** accepted. D8 recomputes gaps through the existing gap writer over the changed dates. The dry check lists those gap rows.
- **F007 parity check:** kept, with the reason recorded in D1. Session bars are not stored, so nothing can drift after release. The SQL is rendered from `eligible_sql` and the rollups' first/last expressions.
- **F008 #78 before session volume:** kept, with the reason recorded in D4. Every session bar, 15:30 included, is the sum of the provider's own minutes, and the close stamp is in no phase. #78 can change client advice on regular-session volume, not bar values. The investigation runs first so the docs carry the answer.
- **F009 load bounds:** accepted. A one-year regular 1h read at p95 under 2 s (20 s hard limit), and `/health` at p95 under 0.5 s during it.
- **F010 extended cost:** kept. #38 asks for extended hours "if cheap", and trading-engine named them. The cost case is in D6: 2.5× the minutes, bounded by the same ceiling, no second path.
- **F011 null keys:** accepted. D9: the session fields are only on the session response model.
- **F012 dependencies:** accepted. 182, 186, 221 and 301 added to the dependencies.
- **F013 migration id:** accepted. It's "next free id" now. #46 is taking 067.
