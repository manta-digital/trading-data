---
docType: review
layer: project
reviewType: code
slice: historical-acquisition-pass
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/224-slice.historical-acquisition-pass.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20260930
dateUpdated: 20260930
reviewedSha: f715edb039f3b9bc98d574f0b8bc9bd230396021
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 0
diffTruncated: true
squadronVersion: 0.16.0
findings:
  - id: F001
    severity: concern
    category: dry
    summary: "Data-file suffix constant defined twice"
    location: "src/manta_trading/data/tick/in_flight_files.py:44"
  - id: F002
    severity: concern
    category: dry
    summary: "Open-status list duplicated across three manifest modules"
    location: "src/manta_trading/data/tick/manifest_pass.py:33"
  - id: F003
    severity: concern
    category: maintainability
    summary: "`AdvanceTally.add` uses string field names with setattr/getattr"
    location: "src/manta_trading/data/tick/in_flight.py:AdvanceTally.add"
  - id: F004
    severity: concern
    category: correctness
    summary: "Edge date derived inconsistently between availability span and planning state"
    location: "src/manta_trading/data/tick/availability.py:79-83"
  - id: F005
    severity: concern
    category: error-handling
    summary: "Provider failure re-raised without ERROR logging"
    location: "src/manta_trading/data/tick/in_flight_files.py:_download"
  - id: F006
    severity: concern
    category: typing
    summary: "Type-ignore and untyped conversion in definition decoding"
    location: "src/manta_trading/data/tick/definitions.py:_value"
  - id: F007
    severity: note
    category: robustness
    summary: "Import-time `assert` for exhaustiveness"
    location: "src/manta_trading/cli/commands/tick_exit.py:34"
  - id: F008
    severity: note
    category: style
    summary: "Constant-only f-string SQL and an odd comparison order"
    location: "src/manta_trading/data/tick/definitions.py:_stored, _insert"
  - id: F009
    severity: note
    category: dry
    summary: "Deliberate duplication of the Kalshi pass contract"
    location: "src/manta_trading/data/tick/pass_contract.py"
  - id: F010
    severity: note
    category: testing
    summary: "Test and load-test coverage not verifiable"
    location: "unverified"
---

# Review: code — slice 224

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5
**Diff:** truncated: 100000 of 288711 characters reached the model

## Findings

### [CONCERN] Data-file suffix constant defined twice

`DATA_FILE_SUFFIXES = (".dbn.zst", ".dbn")` is redefined here with the same comment as in `adopt.py` (which the diff still keeps). Two definitions can drift, and CLAUDE.md requires one definition per comparison value. Import it from one place, or move it to `constants.py`.

### [CONCERN] Open-status list duplicated across three manifest modules

`_OPEN = [s.value for s in OPEN_FETCH_STATUSES]` here duplicates `_OPEN_STATUSES` in `manifest_reads.py`. `manifest_repo.py` already has its own `_OPEN`, which the diff references. Define it once and import it.

### [CONCERN] `AdvanceTally.add` uses string field names with setattr/getattr

The counter names are listed by string, and the same names are listed again in `to_dict` and in `_DELIVERY_LABELS`. Adding a counter without updating all of these fails silently: the value is not summed, or not reported. Iterate `dataclasses.fields` over int fields, or keep the counters in a `Counter`, so the field list has one source.

### [CONCERN] Edge date derived inconsistently between availability span and planning state

`availability_span` uses `edge.start.date()` and `edge.end.date()` with no timezone conversion. `planning_state` explicitly does `astimezone(UTC).date()`. If the provider's range is timezone-aware but not UTC, the two disagree about the edge day. Convert both the same way, ideally through one helper.

### [CONCERN] Provider failure re-raised without ERROR logging

The `except ProviderError` branch records failures and re-raises, but never calls `logger.exception`. CLAUDE.md option (a) requires ERROR-level logging on re-raise. The phase boundary may log it, but that is not visible in the reviewed portion. Please confirm, or log here.

### [CONCERN] Type-ignore and untyped conversion in definition decoding

`int(raw)  # type: ignore[call-overload]` suppresses strict pyright rather than narrowing `raw`. A type-ignore on a conversion of provider data also hides bad-input errors, such as a float or None. Narrow the type explicitly (`isinstance(raw, int | np.integer)`) and fail explicitly otherwise. In `_decode`, `len(rows)` is returned alongside `rows`, which is redundant.

### [NOTE] Import-time `assert` for exhaustiveness

The check disappears under `python -O`, and the same pattern is used in `tick_pass_render.py`. That is acceptable if a test also asserts it. Raising an explicit `RuntimeError` at import, or adding such a test, would make it robust.

### [NOTE] Constant-only f-string SQL and an odd comparison order

Column names are built by f-string from module constants, not user input, so there is no injection risk. Add a short comment saying so, since the SQL rule is "never f-string SQL". In `availability._bounds`, `CME_DATASET == dataset` is evaluated per universe entry inside the comprehension; hoist it to a single `if`.

### [NOTE] Deliberate duplication of the Kalshi pass contract

The copy is documented (TD1) and guarded by a parity test, which is an acceptable trade-off. The parity test itself was not in the reviewed diff, so I could not verify it exists.

### [NOTE] Test and load-test coverage not verifiable

No test files appeared in the diff. The pass involves network, polling and concurrency paths, so the Python rules call for a load test with latency or resource assertions. Please confirm one exists under `tests/load/`. Parsers such as `parse_conditions` also need real-world fixtures, including the Saturday-absent case, per CLAUDE.md.

### Run Digest

- Response length: 4718 chars
- Response is newline-free: no
- Tool calls made: 0
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
