---
docType: review
layer: project
reviewType: arch
slice: data-acquisition-futures-tick-primary-focus
targetKind: arch
rulesSource: project
project: trading-data
verdict: FAIL
verdictSource: stated
sourceDocument: project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md
aiModel: z-ai/glm-5.3
status: complete
dateCreated: 20260927
dateUpdated: 20260927
reviewedSha: facdc559b1a24de5f3df6aa4f3d8165d4ead7c6f
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 32
findings:
  - id: F001
    severity: fail
    category: consistency
    summary: "The ingest ledger's stated grain contradicts the two-unit session the document itself mandates"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:105-117"
  - id: F002
    severity: concern
    category: consistency
    summary: "\"The 24-hour embargo\" contradicts the document's own revised 8-hour lag"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:117"
  - id: F003
    severity: concern
    category: completeness
    summary: "The rolling 30-day spend cap has no configuration surface and no defined input value"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:53"
  - id: F004
    severity: concern
    category: consistency
    summary: "\"No spend\" for the proof contradicts the billable-definitions requirement and the no-exempt-requests rule"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:129"
  - id: F005
    severity: concern
    category: feasibility
    summary: "The placement decision is gated on measurements the document's own sequencing makes unreachable"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:142"
  - id: F006
    severity: concern
    category: other
    summary: "Fail-at-startup on `MT_TICK_DB_URL` absence couples tick configuration to all-source monitoring"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:75"
  - id: F007
    severity: concern
    category: dependencies
    summary: "The ingest pass has an unstated hard runtime dependency on the production database, contradicting the outage claim"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:75"
  - id: F008
    severity: concern
    category: consistency
    summary: "The CLI-shape decision is presented as open after slice 220 shipped the subgroup and recorded it in the normative contract"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:150"
  - id: F009
    severity: concern
    category: consistency
    summary: "The architecture retains design-fixed statements its own slice plan explicitly supersedes"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:61"
  - id: F010
    severity: concern
    category: consistency
    summary: "\"Backup is part of done... in the same slice cycle that creates them\" is contradicted by both sequencing plans"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:67"
  - id: F011
    severity: concern
    category: consistency
    summary: "A Design Goal hardcodes a timer that the document elsewhere makes an open PM decision"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:47"
  - id: F012
    severity: concern
    category: completeness
    summary: "The session↔day mapping for the availability edge is unspecified, and every session spans two condition-days"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:117"
  - id: F013
    severity: concern
    category: extension-points
    summary: "A tier upgrade over an already-loaded range silently no-ops, and the count check cannot detect it"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:107"
  - id: F014
    severity: note
    category: consistency
    summary: "The manifest state machine includes a state its own row-creation rule makes unreachable"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:51"
  - id: F015
    severity: note
    category: antipattern
    summary: "The Kalshi-contract duplication is a documented deviation from the project's DRY rule, mitigated only procedurally"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:73"
  - id: F016
    severity: note
    category: dependencies
    summary: "The initiative's critical path runs through an unscheduled external slice with no fallback"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md:171"
---

# Review: arch — initiative 220

**Verdict:** FAIL
**Model:** z-ai/glm-5.3

## Findings

### [FAIL] The ingest ledger's stated grain contradicts the two-unit session the document itself mandates

The Completeness definitions paragraph fixes the ledger as "**one row per *(instrument, session)*** — records loaded, first and last event time, **source unit**" (line 117; the slice plan's 222 repeats it: "one row per instrument and session, holding records loaded, first and last event time, traded volume, and **source unit**"). But the same document states, twice, that "a session that spans two calendar days is covered by two units" (line 101) and "which units cover which sessions is a ledger fact established at ingest" (plural, line 101). CME sessions open 17:00 CT on the prior date and close 16:00 CT, so **every** session intersects two calendar-day units — the earlier unit holds the opening ~1–2 hours, the later unit the remaining ~21. A single row per (instrument, session) with a singular `source unit`, committed atomically per unit ("a unit's ledger rows and its *ingested* transition commit together, after its last batch," line 105), cannot represent this. Worse, the interactions compound: (a) the instrument-session completeness check — "the raw table's count for that instrument-session equals the ledger's" — requires the ledger row to hold the *session total*, i.e. a merge across both units, which the write semantics never describe; (b) idempotent re-ingest ("re-ingesting an ingested unit is a no-op," line 51) is impossible with an incrementally merged row — a re-ingest after a mid-unit death would double-count unless the count is derived from the raw table, which the design elsewhere forbids depending on; (c) the zero-record rule ("for every configured instrument ... in a session the unit's range covers, ingest writes a row, including a row with zero records," line 117) means the later unit writes a zero-record row for the *next* session whose bulk it does not contain — which must be merged, not inserted, once the covering unit lands; (d) volume roll inputs read "the session's traded volume" from this same row (line 148). Either the ledger is keyed (instrument, session, unit) with read-time aggregation, or it is a merged upsert with stated idempotent semantics — the architecture must pick one before slice 222 creates the table, because every proof-parity status verb, the count check, and the volume roll method read this row.

### [CONCERN] "The 24-hour embargo" contradicts the document's own revised 8-hour lag

The Completeness definitions still say "The **24-hour embargo** is the expected lag behind that edge, used for the timer cadence and for flagging an edge that stops advancing" — while the Pass-form principle (line 71), the Availability-lag section (line 127), and the Revision Log's explicit "The availability lag is 8 hours, not 24" (line 192) all supersede it. This is not a typo in a passive sentence: the embargo figure is defined as the input to the edge-stopped-advancing flag and cadence reasoning, so a slice designer of 223/224 reading this paragraph tunes the flag to the wrong constant. The revision edited three of the four occurrences and missed the one in the section that defines the completeness semantics.

### [CONCERN] The rolling 30-day spend cap has no configuration surface and no defined input value

The cost principle introduces two ceilings. The per-pass ceiling is fully specified (`MT_TICK_SPEND_CEILING_USD`, no default, refuse-when-absent, verified present in `Settings` and `.env_sample`). The rolling 30-day cap — "the most the manifest may show committed over the trailing 30 days" — is never given a setting name, a config key, or an owner of its threshold value anywhere in the architecture or the slice plan (223 says only "a rolling 30-day cap summed from the manifest's recorded costs"). The follow-on sentences ("Both are checked before any submit. **It** has no default. If **it** is absent, the pass still estimates and reports but refuses to purchase") grammatically bind to the named per-pass ceiling, leaving the cap's absence behavior undefined. Additionally, the cap is "read from the manifest's recorded costs," but the manifest records the *estimate* at submit (`get_cost`); the job's actual cost arrives later on `get_job_details` (`BatchJob.cost_usd`, `None` until processed — see the shipped `data/tick/provider.py`). The document never says the manifest cost is updated with the actual at delivery, so a cap computed from estimates under-counts spend whenever actual exceeds estimate — which is exactly the failure a rolling cap exists to catch.

### [CONCERN] "No spend" for the proof contradicts the billable-definitions requirement and the no-exempt-requests rule

The sourcing sequence states the two free-credit jobs "prove storage, ingest, and the proof slice **with no spend**" (line 129). But the Envisioned State requires contract definitions "populated before any tick data" (line 97); the ingest Resolution check requires "every record resolves to a contract through the stored definitions" (line 115); the cost principle forbids exempt requests outright ("batch jobs, direct range requests, and the provider's statistics schema — all pass through the same estimate → guard → manifest path," line 53); and definitions are a billable schema (the slice plan's 223: "Contract definitions are acquired through this same path as `definition`-schema units. They are billable"; the 220 measurement priced a 5-day definition request at <$0.01, not $0). The two purchased jobs are `trades` and `tbbo` only — no definition files. So the proof *does* require a purchase (tiny, but a submit through the batch path), and since the pass refuses to purchase when `MT_TICK_SPEND_CEILING_USD` is absent, the proof also requires the PM to set the ceiling first — directly contradicting both "no spend" and the slice plan's "No purchase is needed" (226). One of the two claims must be corrected, or definitions need an explicit, argued exemption the cost principle currently forbids.

### [CONCERN] The placement decision is gated on measurements the document's own sequencing makes unreachable

"The decision is the PM's, informed by **the first purchase's measured size**" (line 142). But 923 must ship before the tick storage track (222), the storage track must exist before any data is ingested, and the 2026-09-27 revision moved all measurement to the proof on the *adopted* free-credit files (226) — which ingests into the already-placed, already-created database. Under the revised plan there may be no purchase at all before the go/no-go (the Standard plan is "ideally not [subscribed] before realtime work begins"), so "the first purchase's measured size" is a decision input that arrives after the decision is forced. The same stale framing recurs: the batch bound is "set... from those measurements" of "the first purchase" (line 61), the chunk-geometry and physical-grouping decisions come "from the first purchase's measurements" (line 107), contention is measured by "the first purchase" (line 144), and the Anticipated Slices still sketch a "First purchase and proof" slice that buys "a bounded ES range at the chosen tier" — which the revision's own sourcing sequence and the plan's 226 ("No purchase is needed") superseded. The document should re-anchor these decisions on the 220 preflight's measured *estimates* (which exist: 114.9 MiB/191.5 MiB per 5 days for trades/tbbo) rather than on a purchase the sequencing places after the decision.

### [CONCERN] Fail-at-startup on `MT_TICK_DB_URL` absence couples tick configuration to all-source monitoring

The operational-state principle requires that a missing `MT_TICK_DB_URL` "fails when a process with a tick duty starts — the tick passes, `mt-serve`, and every process that composes a tick line (overview, **health**, accounting, status)" (line 75). `mt data health` is the 919 health *pass* — its unit monitors minute freshness, cagg materialization, Kalshi phase recency, and quota, none of which is tick data. Under this rule, one missing tick environment variable takes down the entire alerting path for every non-tick source, which is the exact blast-radius shape the isolation goal claims to design out ("a tick pass failing or a provider outage cannot affect any other source," line 47 — honored only by definitional fiat, since health is assigned a "tick duty"). The document also specifies the opposite posture for the *unreachable* case ("a tick line that reads 'unreachable' is the correct output, not an omitted line," line 154) without weighing why a loud degraded line — the same mechanism it chose for outages — is insufficient for the misconfiguration case on shared multi-source surfaces. The asymmetry may be right, but as written it makes tick rollout a prerequisite for minute-tier alerting to function at all.

### [CONCERN] The ingest pass has an unstated hard runtime dependency on the production database, contradicting the outage claim

"A production-database outage therefore costs a tick run its `pass_runs` row, exactly as it does every other source, and **never its money or data**" and "the coupling that remains runs in one direction — tick **composing surfaces** read the production database" (line 75). But the ingest pass assigns every record a session "through the CME calendar's session lookup" (line 105), and the session model's home is explicitly "the production database's existing calendar tables... minute migration track" (line 140) — a placement the document argues for at length. So the tick *data path* (not a composing surface) reads the production database on every record-batch: a production outage does not merely cost the ingest run its bookkeeping row, it makes ingest impossible, and the one-directional-coupling inventory omits this second tick→production edge entirely. The placement argument (avoiding per-calendar routing across two pools for I4 consumers) may still win, but the failure-posture paragraph must reconcile it: either the calendar is cached/replicated for ingest, or the document states that a production outage halts tick ingest.

### [CONCERN] The CLI-shape decision is presented as open after slice 220 shipped the subgroup and recorded it in the normative contract

"Whether this is an `mt data tick` subgroup (the Kalshi shape) or `--granularity tick`... is a slice-design decision" (line 150; echoed at line 109). But the Current State itself records slice 220 as complete (line 81), the shipped code is `cli/commands/tick.py` — an `mt data tick` subgroup — and the 220 slice design wrote into the data-correctness contract's I10 row: "220 fixes the tick shape: `mt data tick` subgroup, verb vocabulary as I10." The plan's Notes add that "the CLI and API surface decision is made once, at 220 design" and that the two surfaces "make one decision together," yet slices 229/230 still say "applies the subgroup-or-switches decision" / "Applies the namespace decision" as if pending. Three artifacts give three different answers about whether this decision is open. A designer of 229/230 cannot tell what is settled; the architecture (updated the same day as the slice plan) should record the CLI decision as made and state what remains open for the API namespace.

### [CONCERN] The architecture retains design-fixed statements its own slice plan explicitly supersedes

The adapter principle states, as fixed ("Its execution model is fixed here because it shapes the protocol"): "The bound is **a record count, one named constant**" (line 61). The shipped code is the opposite — `TICK_DECODE_BATCH_BYTES`, a 32 MiB **byte** budget with the per-batch record count derived per schema (`data/tick/constants.py`, `dbn_file.py`) — and the slice plan's Notes list this as "Architecture statements superseded by 220's slice design... a count cannot bound memory across 48-byte trades and 520-byte definitions." Likewise line 99 claims "the delivered files **carry the symbol-mapping records**" and frames mapping completeness as a purchase-time success criterion, while the supersession note records that in-stream mapping records are a live-API feature only and mappings live in the DBN header — which the shipped `dbn_file.py` reads (`metadata.mappings`). The plan says "the slice design is authoritative for 222 onward," but the architecture is the document a 223/224 designer reads for the execution model and the universe-resolution premise, and its Revision Log (2026-09-27) records none of these supersessions. A reader of the architecture alone would bound batches by record count and look for in-stream mapping records.

### [CONCERN] "Backup is part of done... in the same slice cycle that creates them" is contradicted by both sequencing plans

The Design Goal states the tick archive and database "join the backup and restore regime **in the same slice cycle that creates them**" (line 67). The architecture's own Anticipated Slices order places "Backup coverage for the tick archive and database" six sketches after the "Tick storage track" that creates them (after roll methods, operator surface, and API surface), and the slice plan improves this only to 227 — still three slices after 222 creates the database and 224 ingests the proof into it. Between 224 and 227, the local archive is potentially the only copy of already-paid data (the free-credit jobs' 30-day provider retention will have lapsed long since), on a host whose most recent incident (920) was a disk-full event. The slice plan's justification ("no reason to leave real purchased data unprotected across three more slices") concedes the principle is not being met; either the Design Goal or the sequencing must change, and the exposure window should be stated.

### [CONCERN] A Design Goal hardcodes a timer that the document elsewhere makes an open PM decision

The parity Design Goal lists "Bounded pass **fired by a timer**" as a delivered property (line 47). The Pass-form principle says "Whether the acquisition pass gets a timer at all, and at what cadence, is an open PM decision" (line 71); the Cadence section says "There is no daily history pull; runs are on demand" and that a manual-only pass needs "an explicit 'no timer' answer" (line 136); the plan's 223 has the `schedule_for` branch declare the pass manual-only until decided, and 225 adds "a timer only if the cadence decision calls for one." Design Goals are the normative layer of this document ("Parity is a delivery of this initiative, not a follow-on") — a slice designer reading them would install a timer, directly contradicting the open decision three sections later.

### [CONCERN] The session↔day mapping for the availability edge is unspecified, and every session spans two condition-days

"each pass records the provider's free dataset metadata — the dataset's available range and its **per-day** condition (available, pending, degraded, missing) — and **a session the provider reports as pending is *pending***" (line 117). The provider reports calendar days; the completeness vocabulary is sessions; and the document's own request-grain section establishes that every session spans two calendar days. So which day's condition governs a session that straddles an available/pending boundary — the normal case at the edge, where the newer day is pending and the older is available? The rule ("a session is pending if any covering day is pending" would be the natural reading) is nowhere stated, and this is inside the region the document treats as settled rather than deferred to slice design (contrast the session model, where "the calendar's values... are slice-design verification items; the representation is not"). The proof-parity status verbs classify sessions against exactly this edge, so the classification rule must be specified.

### [CONCERN] A tier upgrade over an already-loaded range silently no-ops, and the count check cannot detect it

The storage model advertises per-instrument tier configuration ("The tier is a per-instrument configuration so a later slice can add a level without touching the acquisition path," line 29) and idempotent writes via natural-key conflict-ignore: "a pass that dies mid-unit leaves raw rows the next attempt's natural-key conflict-ignore skips" (line 105), with "a row whose BBO fields are null is a trades-tier row by construction" (line 107). A `tbbo` record for a trade carries the *same* natural key `(instrument, event time, sequence)` as the `trades` record for that trade — same underlying event, wider row. So if a range was ingested at `trades` and the instrument is later upgraded to `tbbo` (the go/no-go picks one tier per instrument; this initiative already owns both tiers for *different* ES months, making a later consolidation a plausible operator action), the tbbo unit's rows conflict-ignore against the existing trades rows, the BBO fields are never populated, and the Counts check still passes — the raw table holds exactly the provider's record count for the range, just without the BBO data the new unit was bought to add. The "rebuild from the archive" remedy exists but is never attached to a tier change; the document should state that a tier upgrade over loaded history requires a range rebuild (or a key/merge policy that distinguishes tiers), or the advertised extension path fails silently in the one way the validation loop is explicitly designed not to catch.

### [NOTE] The manifest state machine includes a state its own row-creation rule makes unreachable

The state machine is "*requested* → submitted → delivered → downloaded → verified → ingested," but "the row is written at *submitted*, the moment money is committed, not after download." Nothing ever persists a row in *requested*; adoption writes at *downloaded*. Slice 222 will render a CHECK constraint over these states, so the vocabulary should either drop *requested* or define when a row exists in it.

### [NOTE] The Kalshi-contract duplication is a documented deviation from the project's DRY rule, mitigated only procedurally

Duplicating `PassPhase`/`PhaseReport`/`PassResult` into the tick package conflicts with CLAUDE.md's "Do not duplicate logic. Respect DRY," and the document itself concedes the hazard ("A copy drifts: the Kalshi contract has already grown since its first slice"). The mitigation — a named per-slice diff task against the Kalshi original — is a checklist control, not a structural one, and it binds only "from 223 on," leaving 220's already-shipped protocol types (`data/tick/provider.py`) undiffed. The three-copies-then-extract argument is reasonable; the note is that the control's failure mode is silent divergence, which no test guards.

### [NOTE] The initiative's critical path runs through an unscheduled external slice with no fallback

923 (multi-database migration and credential plumbing) is "a hard gate with no fallback, by design," lives in another plan, and "Scheduling 923 is the PM's call." Everything from the storage track onward (222, 223, 224, 225, 226, and the go/no-go) is blocked on a foundation slice with no committed date. The document is honest about this, but the risk deserves a stated consequence — e.g., how long 220/221's output can sit idle before re-verification is needed — rather than only the sequencing fact.

### Run Digest

- Response length: 22061 chars
- Response is newline-free: no
- Tool calls made: 32
- Tool calls failed: 0
- Stop reason: stop
- Reasoning characters: 158816
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 16
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 16
- Finding-shaped matches — surviving validation: 16
