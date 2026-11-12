---
docType: review
layer: project
reviewType: code
slice: tick-bars-and-compact-tick-serving
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: CONCERNS
verdictSource: derived
sourceDocument: project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md
aiModel: deepseek/deepseek-v4.1-flash
aiProfile: openrouter
status: complete
dateCreated: 20261112
dateUpdated: 20261112
reviewedSha: 6f698533b09e9d17c3d84d7df4bf64940e240a7c
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 36
diffTruncated: false
turns: 20
promptTokens: 2585978
cachedTokens: 2208256
completionTokens: 95143
reasoningTokens: 90840
durationSeconds: 592.9
costUsd: 0.113078
costBasis: alias_pricing
runId: run-20261112-p6-3080c8dd
squadronVersion: 0.24.26
categoriesNormalized: true
findings:
  - id: F001
    severity: concern
    category: consistency-rot
    summary: "The generated note's frontmatter does not match the project's note conventions"
    location: "scripts/measure_tick_bars.py#report"
  - id: F002
    severity: note
    category: documentation-drift
    summary: "`docs/api/reference.md` still says \"Seventeen read-only HTTP routes\" for a 27-path surface"
    location: "docs/api/reference.md:12"
  - id: F003
    severity: note
    category: error-handling-observability
    summary: "The note is written to a cwd-relative path after the production request has been spent"
    location: "scripts/measure_tick_bars.py#main"
  - id: F004
    severity: pass
    category: other
    summary: "The rule lives in one importable unit, is pinned by an isolation test, and is checked against an independent oracle"
    location: "test/unit/data/tick/test_tick_bars.py"
---

# Review: code — slice 308

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash
**Evidence:** 35 successful tool call(s) of 36 made; 166165 of 166165 diff characters reached the model

## Findings

### [CONCERN] The generated note's frontmatter does not match the project's note conventions

`report()` writes the note's YAML frontmatter as `docType: note` / `project` / `slice` / `dateCreated: {today}` / `dateUpdated: {today}`, where `today` is `datetime.now(UTC).date().isoformat()` (ISO, with dashes). Every existing note in `project-documents/user/notes/` that I inspected differs on three of these: `306-bar-parity.md` and `305-manifest-scale-measurement.md` both use `docType: notes` (plural), compact `YYYYMMDD` dates (`dateCreated: 20261109`), and carry `layer: project` plus `status:`; 305 also orders the keys differently. Because this script's entire deliverable is that file, and AGENTS.md requires "YAML frontmatter as specified in `file-naming-conventions.md`" on every markdown file, the template is the one place to get this right. I could not read the conventions document (the path I checked does not exist at `project-documents/ai-project-guide/project-guides/file-naming-conventions.md`), so I cannot assert `docType: note` is invalid versus merely divergent — but it diverges from every sibling note, and a document index keyed on `docType` or a date parser expecting compact dates would treat the generated note differently from the committed ones. Match the sibling shape (`docType: notes`, compact dates, `layer`, `status`).

### [NOTE] `docs/api/reference.md` still says "Seventeen read-only HTTP routes" for a 27-path surface

The opening line reads "Seventeen read-only HTTP routes over the equity bar store, …". The document now carries 27 `<!-- endpoint: -->` markers (I counted them in the section headers), and `test/unit/api_server/test_openapi_artifact.py` pins `EXPECTED_ARTIFACT_PATH_COUNT = 27` after this slice's two additions. This drift predates the slice (the count was already 25 before §6.9/§6.10 were added), so it is not introduced here — but this slice edits §2.7, §6.1, §6.9 and §6.10 of the same file and could correct the sentence in passing. Drop the number, or count it as part of the gate.

### [NOTE] The note is written to a cwd-relative path after the production request has been spent

`NOTE = Path("project-documents/user/notes/308-tick-bar-measurement.md")` is resolved against the current working directory, and `main` calls `NOTE.write_text(note)` only after `_run` has read the ledger and issued the (expensive, production, up-to-2,000,000-trade) request. The module docstring says to run "from the dev checkout root", but nothing enforces it: run from anywhere else and the operator gets a `FileNotFoundError` traceback after the measurement has already been paid for, with the numbers only in the process's dead locals. Anchor the path to the script (`Path(__file__).resolve().parents[1] / NOTE`) or create the parent directory first, so the failure mode is a startup error rather than a post-request one.

### [PASS] The rule lives in one importable unit, is pinned by an isolation test, and is checked against an independent oracle

`tick_bars.py` keeps to the standard library plus the new `dbn_values.py` (which is why `UNDEF_INT64` moved out of `constants.py`), and `test_tick_bars_isolation.py` makes that mechanical: a fresh `python -I` subprocess asserts that neither `psycopg` nor `manta_trading.data.tick.constants` is in `sys.modules` after import. The rule itself is checked against `tick_bar_oracle.py`, a deliberately naive re-derivation (sort by key, `groupby` session, slice by N, summarise) over the committed 2024-12-03 `tbbo` cut and its derived `trades` twin, with the fixture's premise (`SESSION_COUNTS`, `BEFORE_CLOSE`/`AFTER_OPEN`) asserted so a changed fixture fails loudly instead of silently passing. The behavioural guarantees the docs promise are each covered by their own test: `n == session count` is one full bar and `n` above it one partial (`test_n_equal_to_a_session_count_is_one_full_bar`, `test_n_above_every_session_count_is_one_partial_bar_each`); repeated `(ts_event, sequence)` records stay separate trades (`test_repeated_ts_sequence_records_are_separate_trades`); `buy_volume + sell_volume + side-N volume == volume`; and `test_trades_twin_matches_tbbo_without_quotes` pins the trades-tier null-quote behaviour. Admission is tested where it can actually break — `test_admission_over_the_limit_refuses_before_any_read` uses a recording reader and asserts `reads == []` on refusal, and `test_the_cutoff`-style checks in `test_until_stops_the_scan_early` count `fetchmany` calls and then verify every pooled connection is back to `IDLE`, so the early-stop path is shown not to leak a cursor or a checkout.

### Run Digest

- Response length: 5845 chars
- Response is newline-free: no
- Tool calls made: 36
- Tool calls failed: 1
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 336761
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / cache write / completion / reasoning: 2585978 / 2208256 / not computed / 95143 / 90840
- Duration: 592.9 s
- Cost: $0.11 (alias_pricing)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 4
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 4
- Finding-shaped matches — surviving validation: 4

### Raw Response

## Summary

This slice is in good shape and the previous review's substantive findings are resolved in the code now under review: `read_tick_bars` enforces its "whole session" contract via `TickWindow.clipped` (`clip` sets `clipped=window.clipped or narrowed`), the D9 measurement harness is defined once in `scripts/measure_tick_bars.py` and imported by `test/load/test_308_tick_bars_nfr.py` (`SCRIPT = measure_script()`), the script validates its numeric flags with `_positive`, and `docs/api/reference.md` §2.7 now enumerates four remedies while §6.9 correctly says the `499` is not logged as an error. I verified the early-stop logic in `_WindowCut.push` is sound (`position % n == 0` means the previous bar has just closed, so stopping there never truncates an open bar and "a bar opened before `until` is completed with the trades after it" holds). One new consistency issue in the generated note's frontmatter, plus two smaller notes.

## Findings

### [CONCERN] The generated note's frontmatter does not match the project's note conventions
category: consistency-rot
location: scripts/measure_tick_bars.py#report
`report()` writes the note's YAML frontmatter as `docType: note` / `project` / `slice` / `dateCreated: {today}` / `dateUpdated: {today}`, where `today` is `datetime.now(UTC).date().isoformat()` (ISO, with dashes). Every existing note in `project-documents/user/notes/` that I inspected differs on three of these: `306-bar-parity.md` and `305-manifest-scale-measurement.md` both use `docType: notes` (plural), compact `YYYYMMDD` dates (`dateCreated: 20261109`), and carry `layer: project` plus `status:`; 305 also orders the keys differently. Because this script's entire deliverable is that file, and AGENTS.md requires "YAML frontmatter as specified in `file-naming-conventions.md`" on every markdown file, the template is the one place to get this right. I could not read the conventions document (the path I checked does not exist at `project-documents/ai-project-guide/project-guides/file-naming-conventions.md`), so I cannot assert `docType: note` is invalid versus merely divergent — but it diverges from every sibling note, and a document index keyed on `docType` or a date parser expecting compact dates would treat the generated note differently from the committed ones. Match the sibling shape (`docType: notes`, compact dates, `layer`, `status`).

### [NOTE] `docs/api/reference.md` still says "Seventeen read-only HTTP routes" for a 27-path surface
category: documentation-drift
location: docs/api/reference.md:12
The opening line reads "Seventeen read-only HTTP routes over the equity bar store, …". The document now carries 27 `<!-- endpoint: -->` markers (I counted them in the section headers), and `test/unit/api_server/test_openapi_artifact.py` pins `EXPECTED_ARTIFACT_PATH_COUNT = 27` after this slice's two additions. This drift predates the slice (the count was already 25 before §6.9/§6.10 were added), so it is not introduced here — but this slice edits §2.7, §6.1, §6.9 and §6.10 of the same file and could correct the sentence in passing. Drop the number, or count it as part of the gate.

### [NOTE] The note is written to a cwd-relative path after the production request has been spent
category: error-handling-observability
location: scripts/measure_tick_bars.py#main
`NOTE = Path("project-documents/user/notes/308-tick-bar-measurement.md")` is resolved against the current working directory, and `main` calls `NOTE.write_text(note)` only after `_run` has read the ledger and issued the (expensive, production, up-to-2,000,000-trade) request. The module docstring says to run "from the dev checkout root", but nothing enforces it: run from anywhere else and the operator gets a `FileNotFoundError` traceback after the measurement has already been paid for, with the numbers only in the process's dead locals. Anchor the path to the script (`Path(__file__).resolve().parents[1] / NOTE`) or create the parent directory first, so the failure mode is a startup error rather than a post-request one.

### [PASS] The rule lives in one importable unit, is pinned by an isolation test, and is checked against an independent oracle
category: other
location: test/unit/data/tick/test_tick_bars.py
`tick_bars.py` keeps to the standard library plus the new `dbn_values.py` (which is why `UNDEF_INT64` moved out of `constants.py`), and `test_tick_bars_isolation.py` makes that mechanical: a fresh `python -I` subprocess asserts that neither `psycopg` nor `manta_trading.data.tick.constants` is in `sys.modules` after import. The rule itself is checked against `tick_bar_oracle.py`, a deliberately naive re-derivation (sort by key, `groupby` session, slice by N, summarise) over the committed 2024-12-03 `tbbo` cut and its derived `trades` twin, with the fixture's premise (`SESSION_COUNTS`, `BEFORE_CLOSE`/`AFTER_OPEN`) asserted so a changed fixture fails loudly instead of silently passing. The behavioural guarantees the docs promise are each covered by their own test: `n == session count` is one full bar and `n` above it one partial (`test_n_equal_to_a_session_count_is_one_full_bar`, `test_n_above_every_session_count_is_one_partial_bar_each`); repeated `(ts_event, sequence)` records stay separate trades (`test_repeated_ts_sequence_records_are_separate_trades`); `buy_volume + sell_volume + side-N volume == volume`; and `test_trades_twin_matches_tbbo_without_quotes` pins the trades-tier null-quote behaviour. Admission is tested where it can actually break — `test_admission_over_the_limit_refuses_before_any_read` uses a recording reader and asserts `reads == []` on refusal, and `test_the_cutoff`-style checks in `test_until_stops_the_scan_early` count `fetchmany` calls and then verify every pooled connection is back to `IDLE`, so the early-stop path is shown not to leak a cursor or a checkout.
