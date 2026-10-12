---
docType: review
layer: project
reviewType: slice
slice: operator-surface-full-parity
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/230-slice.operator-surface-full-parity.md
aiModel: claude-opus-5-5
aiProfile: sdk
status: complete
dateCreated: 20261012
dateUpdated: 20261012
reviewedSha: a36fa7a034c8b0e18af902866e9cc1f881878ba2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 7
durationSeconds: 78.6
squadronVersion: 0.21.5
findings:
  - id: F001
    severity: concern
    category: dependency-direction
    summary: "`tick_composed` lives in the CLI layer but is offered to 231's API"
    location: "project-documents/user/slices/230-slice.operator-surface-full-parity.md:140"
  - id: F002
    severity: concern
    category: error-handling
    summary: "No handling defined for a streamed `get` that fails or loses its reader partway through"
    location: "project-documents/user/slices/230-slice.operator-surface-full-parity.md:309-328"
  - id: F003
    severity: concern
    category: alignment
    summary: "End-exclusive dates break the architecture's \"same defaults\" parity rule, and the Revision Log entry is only a \"candidate\""
    location: "project-documents/user/slices/230-slice.operator-surface-full-parity.md:234-237"
  - id: F004
    severity: concern
    category: under-specification
    summary: "The composed-surface statement timeout has no defined value or constant"
    location: "project-documents/user/slices/230-slice.operator-surface-full-parity.md:196-198"
  - id: F005
    severity: note
    category: integration
    summary: "This slice, not 234, now introduces the first tick composed-surface opener"
    location: "project-documents/user/slices/230-slice.operator-surface-full-parity.md:424-426"
  - id: F006
    severity: note
    category: proportionality
    summary: "The debug filter set is larger than the slice's requirement"
    location: "project-documents/user/slices/230-slice.operator-surface-full-parity.md:333-339"
  - id: F007
    severity: pass
    category: boundaries
    summary: "Layer boundaries and API isolation for the overview line"
    location: "project-documents/user/slices/230-slice.operator-surface-full-parity.md#td7-composed-lines-what-each-says"
  - id: F008
    severity: pass
    category: alignment
    summary: "Not configured / unreachable posture follows the architecture"
    location: "project-documents/user/slices/230-slice.operator-surface-full-parity.md#td8-one-function-opens-the-tick-database-for-composed-surfaces"
  - id: F009
    severity: pass
    category: alignment
    summary: "Roll and active-contract explicitness on `get`"
    location: "project-documents/user/slices/230-slice.operator-surface-full-parity.md#td5-output-shapes"
  - id: F010
    severity: pass
    category: process
    summary: "Standing 220 obligations"
    location: "project-documents/user/slices/230-slice.operator-surface-full-parity.md#realtime-paths"
---

# Review: slice — slice 230

**Verdict:** CONCERNS
**Model:** claude-opus-5-5

## Findings

### [CONCERN] `tick_composed` lives in the CLI layer but is offered to 231's API

The table at line 140 puts `cli/tick_composed.py` in the CLI package. Line 505 then says "`tick_composed`'s three states map to the API's 503 / health field" for 231. But line 611 says 231 builds its endpoints "with no CLI import". The API cannot use `TickUnavailable` and the three-state vocabulary without importing from `cli/`, which is the wrong dependency direction (serving layer → CLI).

There is a second mismatch. The architecture ("Serving-layer failure posture", "Operational state has one home") gives the API a pool that is opened at startup and reconnects on its own. `open(settings)` returns a single connection, so the API can only reuse the state names, not the opener.

Fix: put the state enum and `TickUnavailable` in `data/tick/`, keep only the connect-and-classify wrapper in `cli/`, and change the 231 bullet so 231 reuses the vocabulary rather than the opener.

### [CONCERN] No handling defined for a streamed `get` that fails or loses its reader partway through

`get` is a new streaming output path: rows go to stdout as they arrive, and JSON is "written incrementally". The exit table maps a dropped tick connection to exit 4, but the design never says what the output looks like when that happens after rows have already been written. Two cases are open:
- **JSON:** `rows`, `row_count` and `exit_code` are written last, so a mid-stream failure leaves invalid JSON on stdout. The design does not say whether the writer closes the object with an error field or leaves it broken. If it is left broken, whether stderr says the output is partial is also unstated.
- **CSV / text:** a partial file with exit 4 looks the same as a complete one unless something marks it. The runbook (line 740) sends bulk reads to `--csv > file`, which makes this the main way the command will be used.

The design also skips the case where the reader goes away: `get … | head` or a closed terminal raises `BrokenPipeError` / SIGPIPE in the middle of a server-side cursor fetch. Line 475 says `run_mapped` maps "unreachable" to exit 4, but it does not say what a broken pipe maps to.

Fix: add one decision covering:
- the exit code and output for a mid-stream database drop in each of the three formats, including a stderr line saying the output is partial;
- broken-pipe handling (exit quietly with a stated code, and release the cursor or transaction);
- a test that kills the connection after the first batch.

### [CONCERN] End-exclusive dates break the architecture's "same defaults" parity rule, and the Revision Log entry is only a "candidate"

The architecture states the parity constraint as "the same questions are answerable with the same defaults" (Envisioned State, "Operator surface"), and the slice plan repeats it for 230. TD2 makes `tick get --end` exclusive in session dates, while `mt data get --end` is inclusive in UTC days. An operator who runs both commands with `--end 2024-09-20` gets different ranges.

The reason given is sound: one convention across the tick subgroup. But this is a deliberate departure from a stated architectural constraint. Lines 727-729 only call the Revision Log entry "the candidate". Make the architecture Revision Log entry a required task in this slice, not an "if any". The runbook and the I10 contract row should also say that the two tiers use different `--end` conventions.

### [CONCERN] The composed-surface statement timeout has no defined value or constant

Lines 196-198 say accounting and overview use "a read-only connection with a statement timeout, so a hung tick cluster cannot stall the screen". The value is never given, and TD10 (lines 441-451) defines no constant for it. Two problems follow:
- It conflicts with the project rule against undefined magic defaults (CLAUDE.md: "Do not hard-code magic defaults … centralized at the config level").
- Nothing confirms that the guarantee holds. The accounting line evaluates session verdicts over each product's whole wanted range, and it has to finish inside this timeout or it degrades to `unreachable`.

Accounting also reuses `OVERVIEW_DB_CONNECT_TIMEOUT_SECONDS`, which ties accounting to an overview-named constant. Fix: name one constant, for example `TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS`, in TD10 with its value. Either rename the connect timeout as a shared composed-surface constant, or say explicitly that accounting borrows the overview's.

### [NOTE] This slice, not 234, now introduces the first tick composed-surface opener

The slice plan's 234 entry says 234 adds the second connection to the health check "as the first composing surface". 230 now builds the opener (`tick_composed.open`) and two composing surfaces first, and 234 reuses it. The order is correct, since 234 is deferred indefinitely. The slice plan's 234 text should be updated when this slice closes so the two documents do not disagree.

### [NOTE] The debug filter set is larger than the slice's requirement

The requirement (I8, slice plan) is "debug reads of the manifest and definitions" without ad-hoc SQL. `debug units` has seven filters, including repeatable `--unit-id`, `--request-id` and `--fetch-status`, over a manifest of "a few thousand rows" (line 359) that also has `--json` for piping into `jq`. Each filter is cheap, but `--unit-id ...` and `--request-id` could be dropped until an investigation needs them, since `--json | jq` already covers them. This is optional trimming, not a defect.

### [PASS] Layer boundaries and API isolation for the overview line

The tick fact is gathered in the CLI's `gather`, not in `gather_db_facts`. That keeps `/api/v1/overview` byte-identical (success criterion 13) and leaves the API's tick freshness to 231. The read modules (`tick_reads`, `get_plan`, `tick_accounting`, `tick_format`) sit in `data/tick/` with no CLI dependency, which is the right direction for 231 to use them. `pass_runs` and the `PassKind.ACCOUNTING` detail are unchanged, as "Operational state has one home" requires.

### [PASS] Not configured / unreachable posture follows the architecture

TD8 follows "Operational state has one home" and "Serving-layer failure posture" closely:
- An unset URL gives a loud line on multi-source screens but exit 1 on tick-only verbs.
- An unreachable tick database degrades only the tick line.
- The tick line is never left out.
- Minute exit codes are unchanged.

The test that the URL's password is never printed covers the credential concern.

### [PASS] Roll and active-contract explicitness on `get`

Product reads name the rule (with `(default)`) and the contract for each segment, never merge two contracts, list unresolved and incomplete sessions, and exit 3 when either list is non-empty. This meets the architecture's principle that the active contract and roll method are explicit on every surface, and the rule that no stitched or adjusted series is produced. Rejecting an ambiguous contract instead of guessing correctly handles the instrument-id reuse the architecture warns about. Exact fixed-point prices and nanosecond timestamps keep the "provider records are preserved" principle.

### [PASS] Standing 220 obligations

The realtime-paths check is present and correctly finds that neither path is ruled out. The "does this belong in the API?" question is answered for each surface. The Kalshi pass-contract diff is a named task. The I8, I10 and I14 contract rows are in scope.

### Run Digest

- Response length: 9738 chars
- Response is newline-free: no
- Tool calls made: 7
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 78.6 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
