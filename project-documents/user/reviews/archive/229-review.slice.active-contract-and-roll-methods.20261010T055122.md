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
reviewedSha: 7dfbf7ee318bacecfe1aecefdf4b273163a309a4
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 4
durationSeconds: 60.7
runId: run-20261010-p4-4b514b73
squadronVersion: 0.21.2
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Roll vocabulary, default, and read-time view match the architecture"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md#Technical Decisions"
  - id: F002
    severity: pass
    category: scope
    summary: "Open interest was evaluated, as the architecture required, and the deferral is justified"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:309-334"
  - id: F003
    severity: pass
    category: dependencies
    summary: "Dependency directions and the realtime-paths check are correct"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:130-156, 420-430"
  - id: F004
    severity: concern
    category: integration-contract
    summary: "Rule text form is inconsistent between `parse` and `render`, and 231 and 230 consume it"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:206-215, 417-418, 493-495"
  - id: F005
    severity: concern
    category: correctness
    summary: "The \"missing ledger row is a broken invariant\" rule can fire on a legitimate newly-activated contract"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:224-229, 268-274"
  - id: F006
    severity: concern
    category: error-handling
    summary: "Failure modes for the new read paths are only partly enumerated"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:369-372, 503-506"
  - id: F007
    severity: concern
    category: architecture-alignment
    summary: "\"Next roll\" is only partly delivered for the default rule"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:352-356, 480-484"
  - id: F008
    severity: concern
    category: nfr
    summary: "No performance or boundedness target for read-time schedule computation, though 231 will serve it per request"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:156-188, 490-494"
  - id: F009
    severity: note
    category: dependencies
    summary: "The slice depends on a private symbol from 225 and names it inconsistently"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:109-112, 124-125"
  - id: F010
    severity: note
    category: reproducibility
    summary: "The reproducibility anchor `definitions_as_of` is only a date, and supersession can change answers silently"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:175-188, 458-460"
  - id: F011
    severity: note
    category: risk
    summary: "The provider-equivalence lag of 2 rests on two held rolls"
    location: "project-documents/user/slices/229-slice.active-contract-and-roll-methods.md:241-259, 644-655"
---

# Review: slice — slice 229

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Roll vocabulary, default, and read-time view match the architecture

- The slice uses Databento's `c`/`v` letters with a depth index, and defines one default (`DEFAULT_ROLL_RULE`) in `constants.py`.
- The schedule is computed at read time and never stored, which is what "a continuous series is a rule" requires.
- Volume comes from the ledger, not from the raw table or an aggregate. That follows the architecture's "Active contract and roll methods" paragraph and journal 20260725 rule 2.
- Every answer carries `basis_session`, which meets the requirement to name the latest session the inputs cover.
- Acquisition stays on `ES.FUT` parent requests, so this read-time concern does not leak into acquisition.

### [PASS] Open interest was evaluated, as the architecture required, and the deferral is justified

- The architecture says open interest "must be evaluated when roll methods are designed".
- TD6 records free `symbology.resolve` evidence: ES `n.0` matched `v.0` on all 11 rolls, and GC differs.
- It also costs out what shipping would take, and hands the question to 232.
- That satisfies the requirement. The deferral does not silently drop it, and the cost guardrail (statistics purchases go through the archive path) is preserved.

### [PASS] Dependency directions and the realtime-paths check are correct

- The only cross-database edge is the existing tick → production calendar read. The architecture lists it as the fourth edge, and the slice adds no new one.
- `roll_resolve.py` is pure and has no I/O. The `data/kalshi` source is not imported.
- The slice carries the mandatory "Realtime paths" section and names no choice that forecloses Path A or Path B.

### [CONCERN] Rule text form is inconsistent between `parse` and `render`, and 231 and 230 consume it

- TD1 renders an offset rule as `c.0+5s`. The parse grammar is defined only as `{method}.{rank}`, and the offset arrives through a separate `--offset` flag.
- The test list requires "a rule's text form surviving parse and render unchanged". That cannot hold for an offset rule unless `parse` accepts `+Ns`.
- The slice promises 231 "the `RollRule` text form for query parameters". If `parse` doesn't accept `+Ns`, an API client can't express an offset rule in a single parameter.
- The architecture says the API and CLI "accept the same method vocabulary". State the grammar once, covering whether `+Ns` is parseable and whether `--offset` and the suffix can both be given. Then pin it with a test.

### [CONCERN] The "missing ledger row is a broken invariant" rule can fire on a legitimate newly-activated contract

- Eligibility (TD2/TD3) is `activation_ns ≤ S.open`.
- The ledger invariant, as the architecture words it, writes a row for each instrument "whose definition is valid in a session the unit's range touches".
- A contract activated between session S−2 and session S is eligible at S but has no ledger row for S−2. TD3 would then raise instead of treating the volume as absent.
- The slice should state how the two predicates line up. One option is to rank on contracts that were valid at the lag session. The other is to say that a contract with no row because it wasn't yet valid is excluded, as distinct from a row missing from a valid window. The 21-row sample from `trading_tick` does not exercise this boundary.

### [CONCERN] Failure modes for the new read paths are only partly enumerated

- Exit 4 is defined for an "unreachable" tick database or calendar. The following are not covered:
  - A query hang or timeout. No statement or connect timeout is named, and the slice does not say whether it reuses the one `status` uses.
  - A peer disconnect mid-read, after the connection was established.
  - Whether the three reads (definitions, ledger volumes, session verdicts) share one transaction or snapshot. A concurrent ingest between reads could give an answer built from inconsistent inputs, and the slice says answers must be re-derivable.
- `status` gains roll lines, and `build_schedule` and `latest_active` can raise (missing ledger row, expiration outside the calendar). The slice does not say whether such an error fails the whole `status` command or degrades to a per-rule error line.
- The architecture requires each I/O path to carry explicit handling, not an implicit one. Name the timeout, the snapshot choice, and the `status` degradation behaviour.

### [CONCERN] "Next roll" is only partly delivered for the default rule

- The architecture says status reports "the active contract per configured product and rule, its expiration, and the next roll".
- For `v.0`, the default rule, the slice reports "not forecast" plus a no-later-than bound. This is honest and consistent with "never from data not yet held".
- It is still a narrowing of the stated requirement on the default surface, and the Revision Log entry (TD3–TD6) does not record it.
- Either add an explicit line to the Revision Log and the contract row, or state in this section that the no-later-than bound meets the requirement.

### [CONCERN] No performance or boundedness target for read-time schedule computation, though 231 will serve it per request

- The architecture sets no numeric latency target for this path, only that it is read-time and never cached. Both `build_schedule` and `latest_active` are exposed to 231's HTTP endpoints.
- The slice does not state a bound on range length, on cost against ledger and calendar sizes, or on how many sessions a request may span. It also says a schedule is never cached.
- A multi-year product-level request will re-run the definitions, ledger, verdict, and calendar reads every time. State an expected cost envelope or a cap, or hand 231 an explicit obligation to set one.

### [NOTE] The slice depends on a private symbol from 225 and names it inconsistently

- Prerequisites cite `_SHAPE_WHERE`, a private-by-name symbol. Interfaces Required cite `COVERAGE_PREDICATE`.
- Reconcile the names. If the predicate is meant to be shared, 225 should export it publicly.
- The frontmatter `dependencies: [226]` also omits 220, 221, 224, and 225, which the body lists as prerequisites.

### [NOTE] The reproducibility anchor `definitions_as_of` is only a date, and supersession can change answers silently

- The architecture requires that an answer "can be re-derived later from stored definitions and stored figures".
- The slice acknowledges that supersession changes the current-unit set and therefore the answer. It records only a date as the definitions anchor, not the unit set.
- This is acceptable for now. It is worth confirming that a date is enough to detect a changed answer when 231 exposes these values to clients.

### [NOTE] The provider-equivalence lag of 2 rests on two held rolls

- The architecture requires the vocabulary to "resolve to the same contract on the same day".
- The lag is one named constant, pinned by a recorded-fixture test with a documented re-record path. The risk is acknowledged and mitigated.
- TD4 correctly documents the open-to-midnight-UTC window where date-matching and a continuous-symbol stream could diverge, and why stored data is not affected.

### Run Digest

- Response length: 8833 chars
- Response is newline-free: no
- Tool calls made: 4
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 60.7 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 11
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 11
- Finding-shaped matches — surviving validation: 11
