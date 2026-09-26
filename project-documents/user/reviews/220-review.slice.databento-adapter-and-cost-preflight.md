---
docType: review
layer: project
reviewType: slice
slice: databento-adapter-and-cost-preflight
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md
aiModel: claude-opus-5-5
status: complete
dateCreated: 20260926
dateUpdated: 20260926
reviewedSha: 0af7439f3d4fa607aa7b199e45a008237b390319
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 11
findings:
  - id: F001
    severity: concern
    category: integration
    summary: "The day-condition endpoint takes an inclusive end, but the design passes it the exclusive end"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:172"
  - id: F002
    severity: concern
    category: error-handling
    summary: "`fetch_range` writes to the final filename, so a crash leaves an unverified file under it"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:188"
  - id: F003
    severity: concern
    category: error-handling
    summary: "The replacement batch download drops edge cases the SDK's own download handles"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:176"
  - id: F004
    severity: concern
    category: error-handling
    summary: "The design never says what an HTTP 429 on a paid call maps to"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:178-189"
  - id: F005
    severity: concern
    category: scope
    summary: "Per-mode size limits are reported as resolved, but their source could not be read"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:30"
  - id: F006
    severity: note
    category: architecture-alignment
    summary: "Two architecture statements are superseded here, but the architecture is not updated"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:170"
  - id: F007
    severity: note
    category: interface-design
    summary: "Exit code 2 means both \"provider error\" and a command-line usage error"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:243"
  - id: F008
    severity: note
    category: integration
    summary: "The bundle verdict decides part of 223's definition purchase, and gives mbp-1 a purchase verdict"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:166"
  - id: F009
    severity: note
    category: interface-design
    summary: "The injected `httpx.Client` has no stated owner or close path"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:234"
  - id: F010
    severity: pass
    category: nfr
    summary: "NFRs on the touched paths are restated with targets"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md:170"
  - id: F011
    severity: pass
    category: dependency-direction
    summary: "Dependency direction and layer boundaries match the architecture"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md#component-structure"
  - id: F012
    severity: pass
    category: scope
    summary: "Scope, sequencing, and the surface decision match the plan and the architecture"
    location: "project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md#technical-scope"
---

# Review: slice — slice 220

**Verdict:** CONCERNS
**Model:** claude-opus-5-5

## Findings

### [CONCERN] The day-condition endpoint takes an inclusive end, but the design passes it the exclusive end

Technical Decision 8 says `end` is exclusive "because `get_record_count`/`get_cost`/`submit_job` treat it so". The data flow (line 129) then calls `dataset_condition(start, end)` with that same exclusive end. The SDK does not follow that convention for this endpoint. In the installed `databento` package, the `metadata.get_dataset_condition` docstring reads "end_date: The **inclusive** UTC end date of the request range". The cost, size and record-count endpoints in the same file say "exclusive".

If the adapter passes the request's `end` straight through, the condition tally covers one extra day:
- Walkthrough step 6 would report six conditions, not "the five requested days".
- A request whose end is the day after the available edge would ask for one day past the edge. Depending on how the provider answers, that is an error (exit 2) or a spurious `pending`/`missing` entry.

This is the same class of off-by-one-day bug the decision itself warns about. Fix:
- Record the endpoint's inclusive end in the findings table.
- Have `DatabentoTickProvider.dataset_condition` convert internally (`end - 1 day`), so the protocol stays exclusive throughout.
- Add a unit test asserting the SDK receives the inclusive date.

### [CONCERN] `fetch_range` writes to the final filename, so a crash leaves an unverified file under it

The design gives 223 the rule "a file under its final name is always verified, and a `.partial` never is" (lines 176 and 256). That holds for `download_batch`. It does not hold for `fetch_range`, which streams through the SDK's `get_range(path=dest)` straight into `dest`.

The adapter deletes a partial `dest` when an exception reaches it. A process kill, an out-of-memory kill, or a host loss mid-stream raises nothing, so a truncated file stays under its final name. Two things follow:
1. 223 cannot tell that file from a good one by name.
2. The next `fetch_range` to the same path hits the SDK's `x+b` open and raises `FileExistsError`. The failure table calls that "a caller bug", but it is really crash residue.

The design also names no verification for a direct-range file. There is no provider checksum, and the architecture's state machine requires *downloaded → verified* for every unit.

Fix:
- Stream to `<dest>.partial` and rename only after the stream completes, so the name rule holds for both delivery modes.
- Say what "verified" means for a direct-range unit. One option: the decoded record count must equal the free `get_record_count` for the same request. The alternative is to state that 223's design owns it.
- Reclassify a leftover `.partial` as a resumable state, not a caller bug. A direct request cannot resume, so it is deleted, and 223's charge policy for unknown outcomes applies.

### [CONCERN] The replacement batch download drops edge cases the SDK's own download handles

Technical Decision 9 replaces the SDK's `batch.download` with the adapter's own, for good reasons: the SDK sets no timeout, and a checksum mismatch is only a warning. But the SDK's `_download_batch_file` (lines 505–516 of `batch.py`) handles three states of an existing file that the replacement's specification leaves out:

| Existing `.partial` | SDK behaviour | Replacement as specified |
|---|---|---|
| Size equals the expected size (crash after the last byte, before the rename) | Treats it as complete and skips the request | Sends `Range: bytes=N-`. The server's likely answer is 416, which the free-call mapping sends to "any other 4xx → Permanent". The unit is stuck, and 223 may mark it failed or buy it again. |
| Larger than expected | Raises | Unspecified |
| Server ignores `Range` and returns 200 with the full body | (appends) | Unspecified. Appending gives a corrupt file. The checksum catches it and it is re-downloaded, which is loud but wasteful. |

Fix:
- Before fetching, compare the `.partial` size with the size `list_files` reports. Equal: go straight to hash and rename. Larger: delete and restart.
- Treat a 200 answer to a ranged request as a full restart, truncating the file.
- Map 416 explicitly, never to Permanent.
- Add these three cases to the fake-transport tests in Success Criteria (line 276).

### [CONCERN] The design never says what an HTTP 429 on a paid call maps to

For free calls, a 429 (rate limited) is `ProviderTransientError`. For paid calls, Technical Decision 10 says a 4xx means "the provider refused and charged nothing → `ProviderAuthError`/`ProviderPermanentError` as above". The table rows for `submit_batch` and `fetch_range` say "Auth / Permanent".

"As above" could bring in the 429 → Transient rule. The two named classes and the table exclude it. Read literally, a rate-limited `submit_batch` becomes `ProviderPermanentError`, and 223 would mark a unit failed for a condition that is both free and safe to retry. A 429 is exactly the case where retrying a paid call is safe: the request was refused before any charge.

State that a 429 on a paid call is `ProviderTransientError`. Add it to the table's 4xx column and to the paid-call unit tests (line 275).

### [CONCERN] Per-mode size limits are reported as resolved, but their source could not be read

The architecture requires that "the retention window and any size limits per mode must be verified during slice design" (arch "Delivery mode and retention"). The findings table says "No hard size limit on either mode was found." But line 24 says Databento's documentation site could not be read, and that facts which come only from those pages are marked **unresolved** and carried into a task.

Published per-mode limits would be on exactly those pages. The retention window gets the right treatment: it is unresolved and a recorded task (line 289). Size limits get "none found", which reads as verified.

Mark size limits unresolved, like the retention figure. Add them to the Technical Requirements line that the task file records "as read from the account". 223's choice between batch and direct delivery depends on them.

### [NOTE] Two architecture statements are superseded here, but the architecture is not updated

The slice improves on the architecture in two places. Neither correction is sent back to the architecture:
- **The batch bound.** The architecture says "the bound is a record count, one named constant" (arch "One provider adapter owns the wire format"). Technical Decision 7 makes it a byte budget (`TICK_DECODE_BATCH_BYTES`) and derives the record count per schema. This is better, since it holds for the 520-byte `definition` records.
- **Where symbol mappings live.** The architecture's Envisioned State says "the delivered files carry the symbol-mapping records". The findings table (line 34) shows historical files carry no mapping records; the mappings sit in the file's metadata header.

Record both in the slice plan's "Differences from the architecture" list, or amend the architecture text. Otherwise 224 (ingest) and 226 (first purchase and proof) inherit a parent document that contradicts the one they build on.

### [NOTE] Exit code 2 means both "provider error" and a command-line usage error

The design says exit `1` covers "invalid arguments" and `2` means a provider error. Typer/Click exits `2` on its own for a usage error, such as a missing required `--symbols` or `--stype`, or a `--stype` value outside the allowed choices. That happens before the command body runs. So a missing required option exits 2, the same code as a provider outage.

`kalshi.py` has the same collision, so this is inherited. But line 271 states the "invalid arguments → 1" contract as a success criterion. Narrow the wording to "invalid arguments the command itself checks (e.g. `end ≤ start`)", and state that Click's usage errors exit 2.

### [NOTE] The bundle verdict decides part of 223's definition purchase, and gives mbp-1 a purchase verdict

Technical Decision 5 prices the `definition` part of each bundle with the same symbols and `stype_in` as the tier request. It calls that "the comparison 223's guard makes". The architecture scopes definitions per configured *product* over the wanted range (Envisioned State, "Contract definitions"). For a `continuous` request, the same-request definition cost can differ from what 223 actually buys, and 223 has not been designed yet.

Separately, `mbp-1` is outside `STORED_TIERS` and outside the architecture's scope. Yet its row shows a `within`/`over` ceiling verdict, as if it could be bought.

Either:
- word the verdict as "estimate for this request shape; 223 owns the definition scope", or
- show `not purchasable in this initiative` in the `mbp-1` verdict column.

### [NOTE] The injected `httpx.Client` has no stated owner or close path

`from_settings` builds an `httpx.Client` for the verified download. Neither protocol exposes a close or context-manager method. For the one-shot CLI this does not matter. For 223's long-running pass, with one provider per worker (State Management), unclosed clients hold connection pools open.

State who closes it. Either the caller constructs and closes the `httpx.Client`, or `DatabentoTickProvider` is a context manager.

### [PASS] NFRs on the touched paths are restated with targets

Both NFRs are carried with specific values:
- **Decode memory budget.** Technical Decision 7: 32 MiB per in-flight batch, with per-schema record counts and a test on `records.nbytes`.
- **Ingest throughput.** Technical Decision 12 restates the architecture's pass/fail target ("a day's sessions … must ingest well inside the daily pass interval"). It says the benchmark can only fail it, never pass it, and assigns the decision to 226.

### [PASS] Dependency direction and layer boundaries match the architecture

- Only `adapter.py` and `dbn_file.py` import `databento`, and a unit test enforces it.
- Everything above the protocols sees `I*` protocols and frozen dataclasses. The `databento/` subpackage is the whole surface a second provider replaces, as the architecture's "One provider adapter owns the wire format" requires.
- Nothing imports `data/kalshi`; the Kalshi code is used only as a pattern.
- The new error class lands in shared `providers/errors.py` as a sibling of `ProviderTransientError`, not a subclass, so no retry handler can catch it by accident.

### [PASS] Scope, sequencing, and the surface decision match the plan and the architecture

- No table, migration or billable request is added, so the rule "a slice never writes to a table a later slice creates" holds.
- The session model and contract amendment go to 221, as the plan records.
- The CLI/API choice (`mt data tick` subgroup and `/api/v1/futures/*`) is made once, for both surfaces, as the architecture's "Own subgroup or granularity switches" requires.
- The free/paid interface split puts the architecture's "no billable request outside estimate → guard → manifest" rule into the types.
- `MT_TICK_SPEND_CEILING_USD` as `Decimal | None` with `gt=0` matches the architecture's "no default; absent means refuse to purchase".

### Run Digest

- Response length: 13724 chars
- Response is newline-free: no
- Tool calls made: 11
- Tool calls failed: 0
- Stop reason: end_turn
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 12
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 12
- Finding-shaped matches — surviving validation: 12
