---
docType: review
layer: project
reviewType: arch
slice: kalshi-event-contract-data
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/architecture/260-arch.kalshi-event-contract-data.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260823
dateUpdated: 20260823
reviewedSha: fa2f5ff99dcac43ef45498576884e0e433a67853
findings:
  - id: F001
    severity: concern
    category: completeness
    summary: "\"Low-priority, never competes\" design goal has no supporting mechanism, and the gap is one the codebase already knows about"
    location: "project-documents/user/architecture/260-arch.kalshi-event-contract-data.md#Design-Goals"
  - id: F002
    severity: concern
    category: consistency
    summary: "Envisioned \"single ... daemon\" form conflicts with the production convention 916 just established"
    location: "project-documents/user/architecture/260-arch.kalshi-event-contract-data.md#Envisioned-State"
  - id: F003
    severity: concern
    category: feasibility
    summary: "No decision on which database host/migration track the Kalshi schema joins, despite depending on a two-host split that constrains it"
    location: "project-documents/user/architecture/260-arch.kalshi-event-contract-data.md#Technical-Considerations"
  - id: F004
    severity: concern
    category: consistency
    summary: "Catalog-leads-time-series invariant isn't reconciled with the concurrency model implied by \"cycling three collection surfaces\""
    location: "project-documents/user/architecture/260-arch.kalshi-event-contract-data.md#Architectural-Principles"
  - id: F005
    severity: concern
    category: abstraction
    summary: "Single combined daemon departs from 120's \"service-per-concern\" principle without acknowledging the departure"
    location: "project-documents/user/architecture/260-arch.kalshi-event-contract-data.md#Design-Goals"
  - id: F006
    severity: note
    category: consistency
    summary: "Frontmatter `dependencies` omits 100-arch.data-storage.md despite substantive reliance on it"
    location: "project-documents/user/architecture/260-arch.kalshi-event-contract-data.md"
  - id: F007
    severity: note
    category: completeness
    summary: "\"Awaiting settlement\" stuck-market state has no defined threshold or operator-facing signal"
    location: "project-documents/user/architecture/260-arch.kalshi-event-contract-data.md#Technical-Considerations"
  - id: F008
    severity: note
    category: completeness
    summary: "Provider-registry integration (ProviderType/AuthType) for Kalshi isn't mentioned despite the project's \"no magic strings\" rule"
    location: "project-documents/user/architecture/260-arch.kalshi-event-contract-data.md#Current-State"
---

# Review: arch — slice 260

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] "Low-priority, never competes" design goal has no supporting mechanism, and the gap is one the codebase already knows about

The Isolation design goal states the Kalshi collector "never competes with the production OHLCV pipeline for operational priority," and the Architectural Principles restate this as "Unattended low-priority operation." But `916-slice.supervised-production-services...md` — the only completed architecture that actually governs how daemons/passes get supervised on the production host — explicitly defers this exact capability: its "Deferred — cross-source arbitration" section states "systemd prevents a unit from starting only while *that same unit* is still running; two different pass units run concurrently with no arbitration... Real arbitration is either a shared lock the passes take... or a scheduler with priorities inside `mt`," and names Kalshi by name as the reason this will need solving ("Recorded as a slice-plan entry so the gap is on the record rather than rediscovered when Kalshi lands"). 916 also ships `manta-acquisition.slice` with **no** `IOWeight`/`CPUWeight`/`MemoryMax` settings — the one place resource priority could be enforced is deliberately empty. The 260 document treats "low priority, doesn't compete" as an assured property of the design rather than a missing mechanism its own dependency chain has flagged as unbuilt. This should either name the arbitration gap as an open dependency on a future 916-follow-on slice, or explain what mechanism (outside systemd) will give Kalshi's collector lower priority than EODHD acquisition.

### [CONCERN] Envisioned "single ... daemon" form conflicts with the production convention 916 just established

Envisioned State describes "A single low-priority Kalshi collector daemon running as a supervised service" — a persistent, looping process, consistent with 120's original daemon framing. But 916 (dated one day before this document, `dateUpdated: 20260823`) explicitly overturned that model for production: "Form: oneshot passes fired by systemd timers — *not* the long-running looping daemon the slice-128 templates assumed... The looping `--forever` form stays available for manual/debug use but is not deployed." 916 also gives the exact naming pattern for adding a new source (`mt-{source}-pass.service` + `.timer`, a **bounded** pass) and separately calls out that "a streaming subscription... is a `Type=simple` unit like `mt-serve`" — implying that only genuinely continuous/streaming workloads get the long-running form. Kalshi's REST-polling catalog/candle/trade collection is not streaming; under 916's own taxonomy it should default to the oneshot-pass shape unless there's a stated reason it needs the daemon form. The 260 document never engages with this — it cites 916 in Related Work only as "systemd supervision and install path... deploys as another supervised service," without acknowledging that the shape 916 actually supervises (oneshot + timer) differs from the shape 260 envisions (persistent daemon).

### [CONCERN] No decision on which database host/migration track the Kalshi schema joins, despite depending on a two-host split that constrains it

100-arch.data-storage establishes two separate DB hosts with two separate migration tracks (`daily` → plain PostgreSQL 16 `<prototype-host>`, no TimescaleDB extension; `minute` → TimescaleDB-enabled PostgreSQL 17 `<db-host>`), and the repo's actual migration framework (`src/manta_trading/market/schema/migrations/`) only has `daily.py` and `minute.py` — no generic third track exists. The 260 document says only "new Kalshi tables join the existing migration discipline" (Current State) and separately says "a hypertable is adopted for trades or candles only if observed volume warrants it" (Volume and storage posture). Those two statements are in tension: hypertables are a TimescaleDB feature, available only on the `<db-host>`, not the plain-PG16 `<prototype-host>`. If Kalshi's initial plain tables are created on the wrong host, "promote to hypertable later" becomes a cross-host data migration, not a `ALTER TABLE` — a materially different (and much more expensive) operation than the document implies. The document never states which host Kalshi's schema targets, nor whether a third migration track is being introduced. Given "supervised service on the database host" (singular, Envisioned State) is itself ambiguous between the two hosts, this is a load-bearing decision left implicit.

### [CONCERN] Catalog-leads-time-series invariant isn't reconciled with the concurrency model implied by "cycling three collection surfaces"

"The catalog is the spine" principle states plainly: "a candle or trade for an unknown market indicates a sync gap, not an acceptable orphan" — i.e., catalog sync must complete (at least for a given market) before that market's candles/trades are fetched. But Envisioned State describes the daemon "cycling three collection surfaces" and Pattern Reuse explicitly imports 120's async-fetch model, which uses concurrent per-symbol fetching (`asyncio.gather`/semaphore pools) for throughput. Nothing in the document specifies whether the three surfaces run as strictly sequential phases within a cycle (catalog sync fully completes, then candles/trades run against the resulting market set) or as concurrent tasks that could race — e.g., a trade for a market discovered mid-cycle by the catalog syncer but not yet committed when the trade collector's concurrent pass reads the market list. Idempotent-write / FK-constraint handling for that race isn't mentioned either. This is exactly the kind of "implicit ordering assumption that contradicts stated parallelism" the review is meant to catch — the invariant is stated as absolute ("not an acceptable orphan") while the execution model that would make it hold isn't specified.

### [CONCERN] Single combined daemon departs from 120's "service-per-concern" principle without acknowledging the departure

The Pattern Reuse design goal says this initiative "should feel like a third instance of an established shape, not a new design," directly invoking 120. But 120's Architectural Principles state: "Service-per-concern, not service-per-provider — Separate daemon processes by workload characteristics... Each runs as an independent daemon process," specifically so "the tick service can be scaled, restarted, or debugged without affecting daily/minute acquisition." 260 does the opposite: catalog sync, candlestick collection, and public trades collection — three distinct workload characteristics with different pacing (catalog sync is lifecycle/status-driven, candles are per-market-watermarked, trades are cursor-driven) — are folded into "a single... daemon... cycling three collection surfaces." That may well be the right call given modest Kalshi volumes, but the document asserts pattern-fidelity to 120 while structurally contradicting 120's stated separation principle, and doesn't explain why the smaller-scale Kalshi case merits combining concerns 120 deliberately kept apart (which also undercuts the "restart one surface without affecting another" operability 120 argued for).

### [NOTE] Frontmatter `dependencies` omits 100-arch.data-storage.md despite substantive reliance on it

Frontmatter lists only `900-arch.foundation-cleanup.md` as a dependency. But Current State says "Initiative 100 storage layer (complete) — TimescaleDB database with an established migration chain; new Kalshi tables join the existing migration discipline," and Related Work lists `100-arch.data-storage.md` as providing "Database, migration chain, storage conventions the Kalshi schema joins." Unlike the explicit, deliberate note about 120 ("patterns from 120 are reused but 120 is not a blocking dependency"), there's no equivalent statement about 100 — it's simply missing from the declared dependency list even though the body treats it as a hard prerequisite (can't create Kalshi tables without 100's migration framework and host/DB conventions). This should be either added to `dependencies` or explicitly called out as non-blocking, the way 120 was.

### [NOTE] "Awaiting settlement" stuck-market state has no defined threshold or operator-facing signal

"Settlement is a first-class collection event" and the Settlement capture timing consideration both require the catalog loop to track an "awaiting settlement" set "with visibility into markets stuck in that state" — but "stuck" is undefined (no age threshold, no distinction between "still normal" and "abnormally delayed"), and "visibility" doesn't specify a mechanism (status CLI field? structured event? alert?). Given the document elsewhere is careful to defer genuinely open decisions explicitly ("deferred to slice design," "finalized at slice design"), this one reads as assumed-solved rather than flagged as open — worth the same explicit deferral treatment.

### [NOTE] Provider-registry integration (ProviderType/AuthType) for Kalshi isn't mentioned despite the project's "no magic strings" rule

`src/manta_trading/providers/types.py` currently defines `ProviderType` with `EODHD`, `DATABENTO`, `FLAT_FILE` (no Kalshi member yet) and `AuthType` with `API_KEY`/`NONE` (the `NONE` case already exists and fits Kalshi's no-credential requirement cleanly). The 260 document cites 900's "provider registry with enums" as something to build on but never states that a `ProviderType.KALSHI` entry and `ProviderProfile` (per 900's `ProviderProfile` dataclass shape) are needed — a small but load-bearing detail given 900's explicit "No magic strings: All dispatch, status values, provider names... use enums or typed constants defined in one place" principle, and given the client will need to plug into whatever rate-limiter/auth-strategy scaffolding the registry provides.
