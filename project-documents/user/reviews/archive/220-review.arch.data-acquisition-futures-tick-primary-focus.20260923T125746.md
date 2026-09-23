---
docType: review
layer: project
reviewType: arch
slice: data-acquisition-futures-tick-primary-focus
targetKind: arch
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md
aiModel: z-ai/glm-5.2
status: complete
dateCreated: 20260922
dateUpdated: 20260922
reviewedSha: 7d3fdd9e8535eb718fa469c443d38b4b2fe749a1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 36
findings:
  - id: F001
    severity: concern
    category: extension-points
    summary: "Named 900-band multi-database migration slice has no home in the foundation plan"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#anticipated-slices"
  - id: F002
    severity: concern
    category: completeness
    summary: "Cross-source arbitration gap inherited from 916 is unaddressed despite the host-level isolation claim"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#design-goals"
  - id: F003
    severity: concern
    category: consistency
    summary: "New schema-tier model (`trades`/`tbbo` with nullable BBO fields) is unreconciled with the existing `TickEventType` enum"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#envisioned-state"
  - id: F004
    severity: concern
    category: completeness
    summary: "Retention-window deadline has no enforcement mechanism for jobs that rest across pass boundaries"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#architectural-principles"
  - id: F005
    severity: concern
    category: completeness
    summary: "`PassKind.TICK` addition requires updating `firing_schedule.py`, which the doc does not mention"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#architectural-principles"
  - id: F006
    severity: note
    category: antipattern
    summary: "\"Re-derive the Kalshi pass contract per slice\" discipline accepts a known maintenance cost"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#architectural-principles"
  - id: F007
    severity: note
    category: completeness
    summary: "Validation loop ordering against purchased OHLCV units is unspecified"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#envisioned-state"
---

# Review: arch — slice 0

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.2

## Findings

### [CONCERN] Named 900-band multi-database migration slice has no home in the foundation plan

The "Anticipated Slices" section sequences "Multi-database migration and credential plumbing (foundation, 900 band)" before the tick storage track, and "Technical Considerations → Database placement and roles" calls out the concrete work: track → URL routing in `migrate apply`, a second migration ledger, a second maintenance credential under 913, `mt data init` for two databases, and the 917 test-cluster equivalent. I checked `900-slices.foundation-cleanup.md`: the slice list runs 900–922 with no such slice, and Future Work item 4 only covers cross-source arbitration. The current `migrate apply` (`cli/commands/data.py:217`) and `data_init` both resolve a single maintenance URL via `_get_maintenance_url` and `_create_timescale_db`, and `TRACKS` (`market/schema/migrations/__init__.py`) maps every track to one database. The 220 doc assumes this foundation work is sequenced before its tick storage track, but the 900 plan has no slice that owns it. Either the 900 plan must be amended with a new slice, or 220's sequencing premise is unsupported. The document calls this "cross-cutting" and "foundation work, not tick work," which is the right call on ownership — but it leaves the dependency dangling with no concrete owner.

### [CONCERN] Cross-source arbitration gap inherited from 916 is unaddressed despite the host-level isolation claim

The "Full parity" design goal states "Host-level isolation — a filling tick volume, memory contention with the minute tier — is a property of the placement decision (Technical Considerations), which this goal constrains rather than assumes." The 260 (Kalshi) architecture explicitly inherited this gap from 916 and named it: "Cross-source arbitration between pass units does not exist ... two pass units run concurrently with no priority mechanism." `900-slices.foundation-cleanup.md` Future Work item 4 makes the gap concrete: it "becomes real when Kalshi and Databento tick land: few instruments but large volume, and the binding constraint moves from provider quota ... to host bandwidth, disk, and DB write throughput." The 220 doc's isolation claim rests on placement (separate volume, resource bound) but never references the arbitration gap, the `manta-acquisition.slice` weight question, or what happens when the tick ingest pass and the minute pass fire concurrently. 916's `manta-acquisition.slice` is installed empty precisely because "the numbers are speculative until there is measured contention." The 220 doc should at minimum cite this inherited gap and state whether the first-purchase measurements feed `IOWeight`/`CPUWeight`/`MemoryMax` decisions, as 916 anticipated.

### [CONCERN] New schema-tier model (`trades`/`tbbo` with nullable BBO fields) is unreconciled with the existing `TickEventType` enum

The Envisioned State says: "One hypertable for the trades tier — trade fields always present, best-bid/offer fields present when the instrument's tier includes them." This is a tier-per-instrument model with nullable BBO columns on a trade row, not the event-type-discriminator model from slice 105. I verified `src/manta_trading/data/base/tick_schema.py`: `TickEventType` has `TRADE = "trade"` and `QUOTE = "quote"`, and `test/unit/data/base/test_tick_schema.py` asserts both. The 105 outline (per the doc's own Current State) used an event-type discriminator for trade and quote. Under the 220 model, `mbp-1` (every top-of-book change) is out of scope, so `QUOTE` as a separate event type has no role; BBO fields are attributes of a trade row when the instrument's tier is `tbbo`. The doc is silent on what happens to `TickEventType` — is it replaced, narrowed to just `TRADE`, or extended with a tier concept? The "Tick storage track" anticipated slice says it creates the trades-tier hypertable and removes the stale 105 integration test, but does not name the `TickEventType` enum's fate. Slice design will hit this ambiguity.

### [CONCERN] Retention-window deadline has no enforcement mechanism for jobs that rest across pass boundaries

The state machine section states: "the next pass's first phase reconciles in-flight units against the provider's job status ... so the same range is never bought twice and a delivery is never missed inside its retention window (the deadline is a manifest field)." It also says "*Submitted → delivered* is polled, never waited for" and a job still running when the wait budget ends "simply rests at *submitted* until the next pass's reconcile phase." The doc does not describe what happens when the retention window is about to expire between passes. A daily pass cadence means a job submitted on day N that rests at `submitted` is next reconciled on day N+1. If the provider's retention window (which the doc marks "verify at slice design") is shorter than the inter-pass gap, or if a pass fails to run (host outage, timer disabled), the download window can close before any pass reconciles, forcing a repurchase. The manifest carries the deadline as a field, but no mechanism is described for an alarm, a forced download attempt, or a deadline-driven extra pass firing when a `submitted` unit approaches its deadline. The doc asserts "a delivery is never missed" but the guarantee is only as strong as "the next pass runs before the deadline," which is not enforced.

### [CONCERN] `PassKind.TICK` addition requires updating `firing_schedule.py`, which the doc does not mention

The doc correctly states that `PassKind.TICK` lands as a minute-track migration re-rendering the `pass_runs` CHECK constraint (verified in `src/manta_trading/data/acquisition/pass_runs.py` and `market/schema/migrations/minute.py:_pass_kind_check_sql`). However, `src/manta_trading/firing_schedule.py` contains an exhaustive `if kind is PassKind.MINUTE / DAILY / KALSHI / HEALTH / ACCOUNTING` ladder with a fallthrough comment: "Not reachable while the branches above are exhaustive; a new PassKind ...". Adding `PassKind.TICK` requires updating this ladder with the tick pass's firing calendar (the doc says the daily pass cadence is gated on the 24-hour embargo). The "Historical acquisition and ingest passes" anticipated slice lists the minute-track migration and `mt-run tick` but does not name `firing_schedule.py` updates. This is a concrete code path the doc omits, and it is load-bearing for `mt-run status`'s "next scheduled firing" line.

### [NOTE] "Re-derive the Kalshi pass contract per slice" discipline accepts a known maintenance cost

The "third source must not create a third pass shape" principle duplicates the Kalshi `PassPhase`/`PhaseReport`/`PassResult` contract into the tick package rather than importing from `data/kalshi`, and explicitly accepts "some duplication" per 120's precedent. The doc acknowledges the cost: "the Kalshi contract has already grown since its first slice (267 added a historical phase), so each 220 slice design diffs the tick copy against the Kalshi original and absorbs what applies." I verified the Kalshi contract in `src/manta_trading/data/kalshi/collection_pass.py` — it has grown to four phase implementations plus `classify_pass`, `CollectionPass`, and the `PASS_PHASES` registry. The "small contract" characterization is accurate for the abstract types but understates the surface that drifts. The discipline is deliberately chosen and the rationale (source → source dependency is forbidden; three copies justify hoisting) is sound, but the per-slice diff obligation is real recurring work that the slice designs must actually perform, not just promise.

### [NOTE] Validation loop ordering against purchased OHLCV units is unspecified

The Validation Loop states: "Minute bars derived from ingested ticks are compared with the provider's own inexpensive OHLCV schema for the same instrument and sessions ... cheap enough to run on every ingest." The doc also establishes that OHLCV/schema purchases are archive units with manifest provenance and must pass through the same estimate → guard → manifest path. This means the cross-check on a given session's tick ingest requires the OHLCV unit for that session to already be ingested. The doc does not specify the sequencing: if a tick unit for session S is ingested before the OHLCV unit for S, does the cross-check skip (and re-run later), block, or fail? Given that "figures derived from ticks exist only for sessions already bought," the inverse (OHLCV present, ticks not yet) is also possible. The cross-check's dependency on a second purchased data stream needs an explicit ordering or a deferred-recheck rule, or the "runs on every ingest" claim will silently no-op when OHLCV is not yet present.

### Run Digest

- Response length: 10241 chars
- Response is newline-free: no
- Tool calls made: 36
- Tool calls failed: 1
- Stop reason: stop
- Reasoning characters: 23294
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
