---
docType: review
layer: project
reviewType: slice
slice: active-contract-and-roll-methods
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/229-slice.active-contract-and-roll-methods.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20261009
dateUpdated: 20261009
reviewedSha: 3acff4228d1c82be48b73360b62f8d6a622fbaaa
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 3
durationSeconds: 47.4
runId: run-20261010-p4-4b514b73
squadronVersion: 0.21.2
findings:
  - id: F001
    severity: concern
    category: dependency-direction
    summary: "Roll functions own per-call connections, which conflicts with the architecture's pooled-tick-database posture for the API"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:523-549"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Behavior when `MT_TICK_DB_URL` is unset is not enumerated for the new verb or the new status path"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:551-558"
  - id: F003
    severity: concern
    category: boundaries
    summary: "Module dependency direction between the roll path and the status modules is ambiguous and could form a cycle"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:148-170"
  - id: F004
    severity: note
    category: scope
    summary: "Open-interest deferral narrows a rule the architecture names, and is recorded with evidence"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:381-406"
  - id: F005
    severity: note
    category: documentation
    summary: "The architecture's body text is narrowed only through the Revision Log"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:802-813"
  - id: F006
    severity: pass
    category: architectural-alignment
    summary: "Reproducibility, explicit contract and rule on every surface, and provider-vocabulary alignment"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:205-231"
  - id: F007
    severity: pass
    category: error-handling
    summary: "I/O failure modes enumerated with explicit handling; performance target restated"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:551-583"
  - id: F008
    severity: pass
    category: architectural-alignment
    summary: "Realtime-paths check and tick → production coupling are stated"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:603-613"
---

# Review: slice — slice 229

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Roll functions own per-call connections, which conflicts with the architecture's pooled-tick-database posture for the API

The architecture says every composing surface, including the API, reaches the tick database through a second pool built from `MT_TICK_DB_URL`. It also says tick endpoints answer 503 while that pool is down and recover through the pool's own reconnect. TD9 makes `build_schedule` and `latest_active` take URLs and open and close their own connections, so every 231 request would open a fresh tick connection and a fresh calendar connection. The slice acknowledges this at lines 711-713 and hands a "pooled variant" to 231 as a design change. That leaves 231 choosing between the architecture's posture and the one-snapshot guarantee this slice mandates. The slice should state which one wins. It should also say how the API's `tick_db_url` seam supplies the URL to these functions, and how the 503 mapping applies to the `OperationalError` they raise. As written, the interface 231 consumes quietly contradicts the architecture's serving-layer failure posture.

### [CONCERN] Behavior when `MT_TICK_DB_URL` is unset is not enumerated for the new verb or the new status path

The architecture separates two failures: the URL absent (a misconfiguration that fails tick-duty processes) and the database unreachable (an outage). The TD9 failure table covers unreachable, hang, peer loss, calendar range and broken invariant. It does not say what `active` does when the URL is absent. It also does not say how the `status` roll lines interact with the existing "tick: not configured" line. The slice should state that `active` fails explicitly with a configuration error and an exit code from `tick_exit.py`, and that `status` produces no roll lines in that case. The same gap applies to a request for a rank with no eligible candidate, such as `c.5`. Whether that is `Unresolved` or an error is implied but not stated.

### [CONCERN] Module dependency direction between the roll path and the status modules is ambiguous and could form a cycle

`tick_status_build.py` imports `ActiveLine` from `active_contract.py`, because `ProductStatus` gains `rolls`. `active_contract.py` and `roll_inputs.py` in turn depend on `SHAPE_WHERE` in `status_reads.py` and on `session_verdicts`. The slice never says which module defines `session_verdicts`. If it lives in `tick_status_build.py`, status and roll import each other. The slice should fix the direction, for example `status_reads` (verdicts and shape) → `roll_*` → `tick_status_build`, and name the module that holds `session_verdicts`. It should also say where `roll_conninfo` lives. It reuses the ingest worker's `TICK_DB_KEEPALIVES_*`, and a read-side module should not import ingest-worker internals. Those constants belong in `constants.py` or another shared module.

### [NOTE] Open-interest deferral narrows a rule the architecture names, and is recorded with evidence

The architecture lists front-by-open-interest among the continuous rules. It also directs that open interest "must be evaluated when roll methods are designed." The slice evaluated it, deferred it on ES-only evidence (`ES.n.0` equals `ES.v.0` on 11 of 11 rolls), and left `n` unassigned in the vocabulary. The Revision Log entry covers this, and GC is handed to 232 as a named obligation. The architecture's Technical Considerations text still says open interest is "no longer blocked on a purchase". The slice says the purchase would fall outside the plan because the plan is not subscribed. Both statements are true at different times, so no change is needed. The PM should acknowledge the deferral explicitly.

### [NOTE] The architecture's body text is narrowed only through the Revision Log

The architecture's "Active contract and roll methods" paragraph still reads "offset of days before expiry" and "status reports … the next roll". The 2026-10-09 Revision Log entry corrects both (session-counted offset; volume next-roll bound). The slice schedules a gating task before close to re-check the architecture's Revision Log entry and the slice plan's note against the shipped behaviour. That follows the precedent of earlier slices.

### [PASS] Reproducibility, explicit contract and rule on every surface, and provider-vocabulary alignment

The architecture requires that an answer naming an active contract also names the session its inputs cover, and that it can be re-derived from stored data. The slice meets this with `basis_session`, `definition_unit_id` and `basis_unit_ids`, and it never stores or caches a schedule. Rule parsing is a single grammar shared by the CLI and the API. Equivalence with Databento's `.c` and `.v` symbols is pinned by a recorded-fixture test. The offset is the one deliberate departure from the provider's vocabulary, and the slice flags it. Spreads are never roll candidates, which is consistent with the architecture's spread exclusion.

### [PASS] I/O failure modes enumerated with explicit handling; performance target restated

The table covers connect failure, hang, peer loss mid-read, calendar out of range, concurrent ingest commit and broken invariant. Each row names the mechanism (`connect_timeout`, `statement_timeout`, keepalives, `REPEATABLE READ`) and the resulting exit code for both `active` and `status`. The slice also explains why `status` exits 3 on a roll-line failure instead of hiding it. The architecture states no latency or throughput target for this path. The slice sets its own bounds: a 30 s statement timeout, a cap of 1,300 sessions per schedule, and a held-range schedule finishing well inside 1 s, with a re-measurement task. It also keeps the architecture's rule that no read scans `tick_trade`.

### [PASS] Realtime-paths check and tick → production coupling are stated

The slice includes the realtime-paths check the architecture requires of every 220 slice design, and the lag-2 rule does not foreclose Path A or Path B. The calendar read stays on the existing tick → production edge, with no new edge and no source → source import. The fixture script reads production with `SELECT` only and uses only free metadata calls, which satisfies the cost-guard principle.

### Run Digest

- Response length: 7557 chars
- Response is newline-free: no
- Tool calls made: 3
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 47.4 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
