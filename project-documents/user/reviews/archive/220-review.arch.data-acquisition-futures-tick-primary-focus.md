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
aiModel: z-ai/glm-5.3
status: complete
dateCreated: 20260921
dateUpdated: 20260921
reviewedSha: b7f77d5d83e1455b8b3634ffdcf075b2565c46df
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 28
findings:
  - id: F001
    severity: concern
    category: completeness
    summary: "The \"full parity\" surfaces require cross-database composition that the document never designs"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#envisioned-state"
  - id: F002
    severity: concern
    category: consistency
    summary: "The data-correctness contract's inherited invariants are neither engaged nor amended"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md"
  - id: F003
    severity: concern
    category: completeness
    summary: "Roll-method inputs and auxiliary purchases sit outside the guarded, manifested acquisition path"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#technical-considerations"
  - id: F004
    severity: concern
    category: feasibility
    summary: "Ingest throughput is named as a must-measure but absent from the measured quantities and the go/no-go"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#design-goals"
  - id: F005
    severity: concern
    category: consistency
    summary: "The absolute isolation claim is contradicted by the placement options still under consideration"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#design-goals"
  - id: F006
    severity: concern
    category: dependencies
    summary: "\"Instantiate the Kalshi phase contract\" names a dependency the document simultaneously rules out"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#architectural-principles"
  - id: F007
    severity: concern
    category: extension-points
    summary: "The live-delivery forward-compatibility claim is unsupported by the file-shaped manifest"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#architectural-principles"
  - id: F008
    severity: note
    category: completeness
    summary: "The 24-hour embargo is asserted as fact while every adjacent figure is marked for verification"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#technical-considerations"
  - id: F009
    severity: note
    category: consistency
    summary: "The parent initiative plan still assigns the daemon-framework extraction to this initiative"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#related-work"
---

# Review: arch — slice 0

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.3

## Findings

### [CONCERN] The "full parity" surfaces require cross-database composition that the document never designs

All tick state lives on the separate tick database ("The definitions and manifest tables live alongside" the hypertable), while every parity surface promised is today single-database on the minute/TimescaleDB host:

- **`PassKind.TICK` in `pass_runs`** — the `pass_runs` table and its CHECK constraint live in the *minute* migration track (`migrations/minute.py`, migration `055_create_pass_runs`; `_pass_kind_check_sql()` renders from the `PassKind` enum in `data/acquisition/pass_runs.py`, which currently has no `TICK`). Two consequences the document misses: (a) adding `TICK` to the enum updates no existing database — the applied CHECK still rejects `'tick'` until a *new minute-track migration* re-renders it, the exact "a served enum grows under you" failure from journal 20260901 (Kalshi's `amended` status failed the rendered CHECK hourly until `kalshi_008`); (b) the Anticipated Slices section nevertheless places "`PassKind.TICK`" inside the **tick** storage-track slice — a track on a different database that cannot alter the minute database's constraint. Either the tick pass writes run rows to the minute DB (a second connection per pass, with the phantom-RUNNING failure mode 922's `close_superseded`/`close_abandoned` machinery exists to prevent — machinery that is per-database), or the tick DB grows its own `pass_runs` that `mt data overview`, built from the minute DB's run rows, cannot see.
- **`mt data overview` tick line and tick freshness in `/api/v1/status` and `/api/v1/overview`** — overview, health, and accounting are reads of the minute database. Kalshi joined them for free only because 260 deliberately placed its schema on the same TimescaleDB host ("Hosting the schema on the TimescaleDB host is what makes either choice a local one", 260-arch). The separate-instance decision removes that property, yet "Database placement and roles" is treated purely as a memory/backup/roles question.
- **The API server** is constructed around a single database URL (`create_app(db_url=…)`, per slice 187's design D9). Serving ticks or tick-derived bars — under either the `/api/v1/futures/*` namespace or the granularity-on-`/bars` option — requires a second pool in the serving process and `MT_TICK_DB_URL` plumbed into service environments; the `mt-run` env-forwarding defect (journal 20260825, only two `MT_*` variables forwarded) shows this class of omission fails silently in production.

Parity is stated as "a delivery of this initiative ... not a follow-on," but the mechanism that would deliver it is undefined, and each option has failure modes the document does not surface.

### [CONCERN] The data-correctness contract's inherited invariants are neither engaged nor amended

The contract (`reference/data-correctness-architecture.md`) is normative by its own terms ("If a slice ships that contradicts a guarantee here, the slice is wrong, not the document") and states the futures-tick initiative "inherits I2, I3, I4, I5, I6, I7, I8, I9, I10 ... It adds tick-specific invariants ... that will be added here when initiative 200 lands." This document cites the contract only for I10's command shape and the out-of-scope list:

- **I4** ("There is exactly one such function in the codebase ... Hard-coded session-hour assumptions anywhere in code are a defect"): the document creates a second session model ("the futures session model is its own"; form is "a slice decision") without stating the constraint that would keep I4 true — that both models be consumable through one session-query surface. The session model is load-bearing for the initiative's own cross-check, so this is not a deferrable detail.
- **I7** (independent cross-vendor audit) is unsatisfiable for tick by this document's own scope (one provider; cross-venue consolidation out). The validation loop — derived minute bars vs the provider's own OHLCV — is *verification* in the contract's own vocabulary ("the same vendor's later representation"), not audit. The conflict is not noted and the contract is not amended.
- **I10 quality verbs**: the Design Goals bullet promises the contract's surface "(I10: status, coverage, update/backfill, daemon-equivalent pass, quality, debug)" — but the Envisioned State operator-surface list and the "Operator surface parity" anticipated slice enumerate every verb *except* quality, 140 (complete, not in the dependency set) owns quality, and no slice delivers `mt data quality ... --granularity tick`. The quality subgroup does not exist in the CLI tree today either (`cli/commands/` has no quality module; `data.py` registers no quality app), so "minute parity" for quality is parity with an unbuilt surface.
- **Vocabulary**: the contract defines granularity as "used in ... acquisition state," reflected in the minute track's `data_gaps.granularity` CHECK (`IN ('daily','minute')`, migration 018) and `data_status`'s `symbols_x_granularity` VALUES. This document replaces acquisition state with the manifest without noting the divergence, and the contract update it itself anticipates is absent from 220's scope even though both API reference documents and the backup runbook are in scope.

### [CONCERN] Roll-method inputs and auxiliary purchases sit outside the guarded, manifested acquisition path

- **Bootstrap ordering.** The acquisition pass "Computes the wanted set (universe × range) minus the manifest," and the universe is configured "by product and contract selection rule" — a selection rule *is* a roll method. But open-interest and volume rules "need per-contract daily figures" from provider purchases or "from counts derived over ingested ticks (free, but only for sessions already bought)," and the roll-methods slice is the *fifth* anticipated slice, after the acquisition pass (third) and the first purchase (fourth). The document never states how the first historical acquisition resolves product → contracts without the roll machinery (provider-side continuous symbols at request time? an explicit contract list?), so the core flow as envisioned cannot be built as sequenced.
- **Guard universality.** "No request that costs money is issued without a preceding estimate" — yet the validation loop's OHLCV purchase, the roll-input statistics/OHLCV purchases, and any direct `get_range` "small probes" are all billable requests outside the described estimate → guard → batch → download → verify → manifest path. Probes additionally return data that never enters the archive — an unmanaged exception to "files are the record."
- **Reproducibility.** "A status line or API response that names the active contract can be re-derived later from stored definitions and stored figures" — if figures arrive by purchase they need a home with manifest-grade provenance or the claim fails; the document gives "stored figures" none. And because tick-derived figures exist only for bought sessions, expanding the universe *backward* later requires purchasing roll inputs for the old range first — a cost consequence the "expanding the universe is a configuration edit" completion claim omits.

### [CONCERN] Ingest throughput is named as a must-measure but absent from the measured quantities and the go/no-go

Design Goals enumerate what the first purchase measures — "bytes per record per schema, records per session, compressed bytes per row, query latency at the chosen chunk geometry" — and the first-purchase slice's go/no-go decides "tier and for GC" from "measured sizes per tier, bytes per row compressed, chunk geometry validated, query latency, derived-bar cross-check." Ingest rate appears in neither list, even though Technical Considerations names decode cost, batch sizing, and within-file resumability as things "to be measured on the first purchase, not designed in advance," and the initiative's own premise is that tick carries minute's failure modes at "one to three orders of magnitude more volume." The daily catch-up pass is feasible only if a day's sessions ingest well inside a day; a pass exceeding its interval silently breaks the cadence the 24-hour-embargo argument selects. Note also that the one throughput figure the document leans on — "13k+ rows/s measured on minute data ... the proven path" — is the pre-psycopg3 measurement carried forward from 100-arch's Current State, and the journal's standing rule (20260720) is that recorded numbers are "re-measured, never recalled." Ingest rate belongs in the go/no-go, with cadence feasibility (sessions/day ÷ measured rows/s vs the timer interval) as an explicit check.

### [CONCERN] The absolute isolation claim is contradicted by the placement options still under consideration

Design Goals: "Complete isolation ... is preserved: a tick pass failing, a tick database filling, or a provider outage cannot affect any other source." Technical Considerations: the tick instance "can mean a second database on the production cluster, a second cluster on the production host, or a second host; each has a different answer for memory contention with the minute tier." Two of the three options violate the claim: a second database on the production cluster shares postmaster, disk, and memory (a tick database filling the volume is a cluster-wide outage, and the journal records this host as a desktop-plus-production machine with documented commit-headroom and OOM contention, 20260813/20260820); a second cluster on the same host shares disk and RAM. The document correctly treats placement as undecided and PM-gated, then states the isolation property as if it were placement-independent. Either qualify the claim (process/schema-level isolation; host-level isolation depends on placement) or constrain the placement decision by the claim.

### [CONCERN] "Instantiate the Kalshi phase contract" names a dependency the document simultaneously rules out

"The tick passes instantiate one of the existing shapes (the Kalshi phase contract is the newer and less provider-specific of the two); whether extraction into a shared framework is warranted ... if so it is foundation (9xx) work, not a prerequisite here." The phase contract is verifiably Kalshi-owned code: `PassPhase`, `PhaseReport`, `PassResult`, and `PASS_PHASES` live in `data/kalshi/collection_pass.py` (journal 20260825). For tick to instantiate it, one of three things must happen and the document chooses none: (a) import from `manta_trading.data.kalshi` — a cross-source code dependency in which kalshi refactors break the tick pass, sitting oddly beside the isolation posture (260 "shares pass_runs, the provider error taxonomy, and the profile registry with 120, and nothing else"); (b) duplicate the small contract in the tick package — acceptable under 120's "accept some duplication" precedent, but then "instantiate one of the existing shapes" is a pattern statement, not reuse, and should say so; (c) hoist the dataclasses to a shared module — which *is* the extraction declared "not a prerequisite here." As written, the reader cannot tell whether the tick pass will import kalshi code, leaving the dependency direction between two data sources to implementation accident.

### [CONCERN] The live-delivery forward-compatibility claim is unsupported by the file-shaped manifest

"This initiative's only obligation to [realtime] is that the archive, manifest, and storage schema accept records from either delivery path identically." The storage-schema half holds — the natural key and preserved provider fields are delivery-agnostic. The archive and manifest halves do not, as described: the manifest records "what the provider returned (job identity, files, sizes, checksums)" and what it cost; live records arrive as a stream with no job, no files, no checksums, and no per-record cost. The completeness definitions are equally file-shaped ("the manifest shows its file acquired and ingested and the raw table's count for that session matches the manifest's record count"), and sequence-gap tracking is set aside ("if they are ever tracked") on the grounds that "within a purchased historical range the provider's delivery is complete by contract" — a contract that does not hold for a live capture with disconnects. The one concrete obligation claimed toward the realtime initiative has no described mechanism; meeting it later means either a manifest delivery-mode discriminator and segment-shaped archive units designed in now (a mode field costs nothing at creation) or a retrofit.

### [NOTE] The 24-hour embargo is asserted as fact while every adjacent figure is marked for verification

"Historical access is embargoed at 24 hours" (also "Databento embargoes historical access at 24 hours," and "it fixes the historical pass cadence at daily, one session behind") carries no "verify" marker, unlike the retention window, per-mode size limits, roll semantics, record sizes, and CME pricing. The cadence choice, the "pending, never missing" definition, and the "availability edge" the caught-up definition depends on all lean on this number. The document's own discipline — "every figure this document marks 'verify' is a slice-design obligation" — should be applied to it.

### [NOTE] The parent initiative plan still assigns the daemon-framework extraction to this initiative

001-initiative-plan entry 8 and its dependency notes state the deferred extraction "lands here as its first slice, since a third daemon now exists." This document consciously reassigns it ("whether extraction ... is warranted is decided from what the third instance actually needs, and if so it is foundation (9xx) work") with sound reasoning — the third source is a pass, not a daemon, and the shapes already exist — but the parent is not amended, leaving the planning source of truth in conflict with the architecture that supersedes it.

### Run Digest

- Response length: 15979 chars
- Response is newline-free: no
- Tool calls made: 28
- Tool calls failed: 0
- Stop reason: stop
- Reasoning characters: 117158
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
