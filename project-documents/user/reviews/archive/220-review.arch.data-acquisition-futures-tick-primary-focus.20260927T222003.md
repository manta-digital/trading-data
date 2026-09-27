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
dateCreated: 20260923
dateUpdated: 20260923
reviewedSha: 746b91fcb24f1739c3665e06018f7b5e409a1756
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 30
findings:
  - id: F001
    severity: concern
    category: consistency
    summary: "\"Fails at startup in every process\" on an absent MT_TICK_DB_URL contradicts the isolation goal"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#architectural-principles"
  - id: F002
    severity: concern
    category: consistency
    summary: "\"A tick pass never runs unaccounted\" contradicts the shared pass_runs best-effort contract it inherits"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#architectural-principles"
  - id: F003
    severity: concern
    category: completeness
    summary: "Zero-record sessions fall through the completeness definition and never converge"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#envisioned-state"
  - id: F004
    severity: concern
    category: completeness
    summary: "The OHLCV validation companion has no storage target, an ambiguous grain, and an unverified existence"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#envisioned-state"
  - id: F005
    severity: concern
    category: consistency
    summary: "Roll inputs \"derived over ingested ticks\" reintroduce the exact failure the cited journal rule forbids"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#technical-considerations"
  - id: F006
    severity: concern
    category: completeness
    summary: "The futures exception calendar has no specified storage home, and either home has unacknowledged costs"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#technical-considerations"
  - id: F007
    severity: concern
    category: consistency
    summary: "Anticipated slice ordering places definitions capture before the storage track that creates its tables"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#anticipated-slices"
  - id: F008
    severity: concern
    category: technology
    summary: "The COPY write path's loop residency is unspecified, defeating the stated observability goal"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#technical-considerations"
  - id: F009
    severity: concern
    category: consistency
    summary: "The data-correctness contract amendment is scheduled after the slices that change what it governs"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#anticipated-slices"
  - id: F010
    severity: concern
    category: extension-points
    summary: "\"A second tick provider is an addition, not a redesign\" is unsupported by the Databento-shaped storage schema"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#overview"
  - id: F011
    severity: note
    category: completeness
    summary: "The spend ceiling's configuration surface is never named"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#architectural-principles"
  - id: F012
    severity: note
    category: consistency
    summary: "The isolation claim lists migration-track as an isolation axis one paragraph before crossing it"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#design-goals"
  - id: F013
    severity: pass
    category: consistency
    summary: "Current State claims verified accurate against the codebase"
    location: "project-documents/user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md#current-state"
---

# Review: arch — slice 0

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.3

## Findings

### [CONCERN] "Fails at startup in every process" on an absent MT_TICK_DB_URL contradicts the isolation goal

The "Operational state has one home" principle states the setting *absent* "is a misconfiguration and fails at startup in every process, the way a missing primary URL does today," and Technical Considerations ("Database placement and roles") reinforces it: "that variable must be present in every service environment file the passes and `mt-serve` run under." Read literally, the minute, daily, Kalshi, health, and accounting passes — which have no tick duty — all hard-fail when `MT_TICK_DB_URL` is unset. That is tick *misconfiguration* reaching production, which the same paragraph forbids: "the reverse — tick trouble reaching production — is what the isolation goal forbids." The Serving-layer failure posture shows the intended distinction is absent-vs-unreachable, suggesting the real intent is "every tick-consuming process," but as written the rule either breaks isolation or is too vague for slice designs to implement consistently. The document must scope which processes fail on absent versus merely omit tick lines. (I also could not verify the supporting analogy — that a missing primary URL fails at startup in every process today; `timescale_db_url` is `str | None = None` in `Settings`.)

### [CONCERN] "A tick pass never runs unaccounted" contradicts the shared pass_runs best-effort contract it inherits

The same principle mandates: the tick pass "opens its run row in the production database before it does anything else and exits non-zero, visible to systemd, if it cannot." The shared machinery this pass will necessarily use says the opposite: `PASS_RUN_DB_CONNECT_TIMEOUT_SECONDS` (`src/manta_trading/constants.py`) documents "Recording a pass is best-effort: a pass whose database is unreachable must still do its real work and report its own result, so the recorder gives up quickly rather than delaying the pass it is describing," and `PassRunRepository` (`src/manta_trading/data/acquisition/pass_runs.py`) is built around that contract. The document keeps run accounting in the shared minute-track table and never says whether the tick pass gets a strict recorder, whether `PassRunRepository` gains a strict mode, or whether the recorder is forked (a fourth copy of pass machinery, unacknowledged given the document's own three-copies argument). As specified, the tick pass either reuses best-effort machinery and can run unaccounted, violating its own rule, or diverges from the shared contract silently. The reconciliation is a load-bearing decision left implicit.

### [CONCERN] Zero-record sessions fall through the completeness definition and never converge

Under "Completeness definitions": "An *instrument-session is complete* when every unit covering it is complete and the raw table's count for that instrument-session equals the ledger's." Ledger rows are written per *(instrument, session)* from what "the decoder sees every record anyway" — i.e., from records observed. A session in which a configured instrument prints zero records (a thin deferred contract, a holiday inside a purchased calendar-day range) produces no ledger row and zero raw rows. The rule is then undefined: "every unit covering it" is a fact "established at ingest" from records, so no coverage fact exists; raw count 0 vs. an absent ledger row never equates. Money is safe (the wanted set is unit-grain, so nothing repurchases), but the session reports missing forever, "universe is caught up" is unreachable for any configured contract with a quiet session, and the proof-parity status/coverage surfaces ship with a phantom gap baked in. The definition needs an explicit zero-record outcome (e.g., ingest writes zero-count ledger rows for sessions a completed unit's range covers for a configured instrument).

### [CONCERN] The OHLCV validation companion has no storage target, an ambiguous grain, and an unverified existence

The Validation loop says its two inputs "arrive as separate archive units — the tick unit and the OHLCV unit for the same range" and the ingest pass compares "every instrument-session whose inputs are *both* ingested." Since "an archive unit is complete when its state is *ingested*," OHLCV units pass through the same state machine and must be ingested — into what? The Storage bullet names one hypertable for the trades tier plus "definitions, manifest, and ingest-ledger tables"; no OHLCV table or projection is specified anywhere, and "The tick database holds tick data and tick-data state only" does not obviously admit one (nor is any other database proposed). If "ingested" means something different for OHLCV units (decode-at-compare from the file), the single state machine carries two meanings and the unverified/mismatch ledger machinery has no stable semantics for the companion. Additionally: the text is inconsistent about the companion's grain ("minute bars derived from ingested ticks are compared with the provider's own inexpensive OHLCV" vs. the Session model's "the provider's daily OHLCV" — one companion unit or both `ohlcv-1m` and `ohlcv-1d`, doubling companion cost?), and the availability of OHLCV schemas for `GLBX.MDP3` — load-bearing for the initiative's primary correctness check — appears nowhere in the document's "verify at slice design" list, despite its own rule that unmarked figures were verified at architecture time.

### [CONCERN] Roll inputs "derived over ingested ticks" reintroduce the exact failure the cited journal rule forbids

The Architectural Principles cite journal 20260725 rule 2 — "a derived object that informs acquisition is a production input, and pausing its refresh silently changes acquisition behaviour" — and conclude "Aggregates over ticks may exist for reads; they never feed the acquisition decision." But Technical Considerations ("Active contract and roll methods") makes per-contract daily figures — "either from the provider's statistics or OHLCV schemas (cheap, but a purchase) or from counts derived over ingested ticks (free, but only for sessions already bought)" — an input to contract resolution, and the wanted set for backward universe expansion depends on it ("expanding a universe backward carries the cost of roll inputs for the old range"). The purchased path is handled (archive units with manifest provenance); the derived path is not: no derived object is named, no refresh mechanism, no statement of what happens when it stalls — the precise silent-change failure rule 2 describes. The doc also applies rule 1 (explicit materialization intervals) to read-side aggregates over the tick hypertable but leaves the acquisition-adjacent roll aggregate unshaped, and recomputing per-contract daily counts from raw ticks at request time is the expensive raw-scan shape the project routes through caggs everywhere else.

### [CONCERN] The futures exception calendar has no specified storage home, and either home has unacknowledged costs

The Session model fixes the *interface* ("the model is load-bearing enough that its shape is fixed here") but its **stored exception calendar** is "stored" nowhere specified. If it lives in the tick database, the "single session-query function I4 requires" — which today serves the equities calendar from the production database (`HEALTH_MINUTE_SESSION_CALENDAR`, the `trading_sessions` tables, minute migration track) — must route per calendar across two connection pools, touching every consumer of the I4 surface including the health check, and slice 923's scope grows to cover session-surface routing. If it lives in the production database, futures calendar data enters the minute migration track, which the isolation claim ("isolation ... holds by design at the schema, migration-track ... level") forbids. The choice also decides who maintains the calendar's forward horizon (the equities side has `TRADING_SESSIONS_EXTENSION_YEARS` and `mt data --extend`; no futures equivalent is mentioned). A load-bearing interface with an undecided, cost-carrying backing store should be decided here, since the document explicitly fixed everything else about this model at architecture level.

### [CONCERN] Anticipated slice ordering places definitions capture before the storage track that creates its tables

The first slice includes "definitions capture into the futures instrument model" and "the CME session model (boundary function, session templates, exception calendar seeded from CME's public schedule)." The tick storage track — third in the list, behind 923 — is what creates "definitions, manifest ... and ingest-ledger tables," and Envisioned State says "The definitions, manifest, and ingest-ledger tables live alongside it [the hypertable]" in the tick database. So the first slice has no tables to capture definitions into, unless it is reordered after 923 and the storage track, the storage track is split so definitions land first, or definitions are temporarily homed in the production database (contradicting the placement rules). The list is headed "Exploratory, not a commitment," but the dependency is structural — the document treats sequencing as load-bearing everywhere else (923 "sequenced before the tick storage track") — and the session calendar from the same slice inherits the unspecified-home problem above.

### [CONCERN] The COPY write path's loop residency is unspecified, defeating the stated observability goal

The adapter principle meticulously fixes the *decode* side's execution model (off-loop iterator, threads-vs-processes keyed to the GIL question) so that "progress stays observable during a multi-gigabyte ingest." The write side gets no such treatment: "Bulk load through `COPY` into a hypertable is the candidate path" (Ingest throughput) and the iterator feeds "the bulk-load path one bounded batch at a time" — but psycopg's COPY is synchronous, the project's pools are sync psycopg, and the Kalshi pattern the tick passes are told to copy holds one locked connection inside async phases (i.e., sync DB calls on the loop; `collection_pass.py` even routes its progress write through an awaitable precisely because "a synchronous write here would block the event loop"). A naive implementation issues each batch's COPY from async code on the loop, stalling heartbeats and the start-timeout budget for exactly the multi-gigabyte case the principle protects. The decode side's threads-vs-processes decision needs a companion decision for the write path (dedicated connection in a worker thread, or an explicit break from the Kalshi locked-connection shape); without it the observability claim is asserted, not designed.

### [CONCERN] The data-correctness contract amendment is scheduled after the slices that change what it governs

Technical Considerations ("Data-correctness contract amendment") says adding the tick invariants "is a deliverable here, not an afterthought," yet Anticipated Slices places the amendment in "Operator surface: full parity" — second to last, after the storage track, both passes, the first purchase, and proof parity. The referenced contract (`project-documents/user/reference/data-correctness-architecture.md`) is explicitly normative: "If a slice ships and a guarantee here is not yet covered by some slice, the guarantee is broken," and its mapping table is "updated as slices are added." Intermediate 220 slices will put `granularity = 'tick'` into minute-track enumerations, land a second calendar behind the I4 surface, and ship tick status/coverage — all before the amendment records the I7 exception, the vocabulary list, and the tick invariants (including sequence-gap detection, which the contract attributes to this initiative but 220 reserves for the realtime initiative — a divergence only the amendment resolves). During that window the normative document claims what the system does not do and does not claim what it does. The amendment should land with (or before) the first slice touching a governed surface, or the document should state the interim governance explicitly.

### [CONCERN] "A second tick provider is an addition, not a redesign" is unsupported by the Databento-shaped storage schema

The Overview claims the provider seam makes a second tick provider "an addition, not a redesign." But the storage the adapter feeds is Databento-specific by construction: the natural key is "the provider's (instrument, event time, sequence)"; the preserved columns are Databento's ("event timestamp, receive timestamp, sequence number, publisher, and instrument identifier"); the tier vocabulary is "the provider's own schema names" (`trades`/`tbbo`); and the null-BBO-means-trades-tier rule encodes Databento's schema semantics ("a quote is not an event of its own but an attribute of a trade"). A provider without per-record sequence numbers, or whose quote semantics differ, fits neither the key, the columns, nor the tier rule. The files-are-the-record/rebuild-the-projection principle is the genuine mitigation and would absorb a projection change — but the document never connects it to this claim, so as written the extension point does not hold without rearchitecting the hypertable. Either soften the claim (a second provider means a new projection and a rebuild) or state how the trades hypertable absorbs a different record shape.

### [NOTE] The spend ceiling's configuration surface is never named

"Cost is a first-class input" requires every acquisition to "refuse to proceed past a configured spend ceiling," but the ceiling's definition site — Settings field, per-run flag, per-pass budget in the manifest — is never specified, unlike `MT_TICK_DB_URL` and `MT_DATABENTO_API_KEY`, which are both named. Under the project's no-magic-defaults and single-definition-site rules, slice design will have to invent the setting and its operator-facing name; that decision belongs here.

### [NOTE] The isolation claim lists migration-track as an isolation axis one paragraph before crossing it

The full-parity goal states isolation "holds by design at the schema, migration-track, pass-unit, and credential level," while the Architectural Principles land `PassKind.TICK` as a *minute-track* migration. The reconciliation is sound (run accounting is shared operational state with one home), but the axis list overstates: it should carve out run accounting or name the exception, or readers will read the minute-track placement as a violation of the stated goal.

### [PASS] Current State claims verified accurate against the codebase

Every checkable factual claim in Current State holds: `Settings.tick_db_url` exists with default `None` and no non-test consumer (`src/manta_trading/config/__init__.py:284`; grep finds only the definition); `src/manta_trading/data/base/tick_schema.py` carries only the `TickEventType` enum; `test/integration/test_tick_schema_integration.py` reads `database/migrations/760_create_tick_events_hypertable.sql` while no `database/` directory exists, guarded only by the unset `MT_TICK_DB_URL` — so the "turns red the day the variable is set" claim is exactly right; `schedule_for` (`src/manta_trading/firing_schedule.py`) is a deliberately exhaustive ladder raising `AssertionError` on unknown kinds, matching "raises on a kind it does not know"; the `pass_runs` CHECK constraint is rendered from the `PassKind` enum per the enum's own docstring; `COVERAGE_SOURCE_TABLE` maps exactly `minute_ohlcv` and `daily_ohlcv`; the Databento profile with `MT_DATABENTO_API_KEY` exists in `providers/profiles.py` with no client and no `databento` dependency. The document's factual foundation is sound where verifiable.

### Run Digest

- Response length: 18339 chars
- Response is newline-free: no
- Tool calls made: 30
- Tool calls failed: 1
- Stop reason: stop
- Reasoning characters: 88960
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 13
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 13
- Finding-shaped matches — surviving validation: 13
