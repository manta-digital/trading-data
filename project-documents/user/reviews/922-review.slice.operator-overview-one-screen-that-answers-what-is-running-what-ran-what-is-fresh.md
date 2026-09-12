---
docType: review
layer: project
reviewType: slice
slice: operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/922-slice.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md
aiModel: z-ai/glm-5.3
status: complete
dateCreated: 20260911
dateUpdated: 20260911
reviewedSha: b5bb40c153ec35add82f6ec38ac9652e989a43ee
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 30
findings:
  - id: F001
    severity: concern
    category: architectural-boundary
    summary: "Rewrites 140-owned `data_status` contracts with no architecture amendment or recorded escalation"
    location: "project-documents/user/slices/922-slice.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md#Technical Decisions"
  - id: F002
    severity: concern
    category: migrations
    summary: "Fresh-database migration chain breaks at 021 as specified"
    location: "project-documents/user/slices/922-slice.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md#Database / Storage Schema"
  - id: F003
    severity: concern
    category: factual-accuracy
    summary: "`_interval_literal` deletion claim is factually wrong"
    location: "project-documents/user/slices/922-slice.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md#Database / Storage Schema"
  - id: F004
    severity: concern
    category: error-handling
    summary: "Orphan-close rule cannot distinguish a dead process from a concurrently running pass; \"the open row per kind\" is undefined with two open rows"
    location: "project-documents/user/slices/922-slice.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md#Technical Decisions"
  - id: F005
    severity: concern
    category: nfr
    summary: "No restated latency target for the NFR-bearing paths the slice modifies"
    location: "project-documents/user/slices/922-slice.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md#Implementation Notes"
  - id: F006
    severity: note
    category: documentation
    summary: "Plan/design drift on the staleness rule's wording"
    location: "project-documents/user/slices/922-slice.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md#Technical Decisions"
  - id: F007
    severity: note
    category: scope
    summary: "Fifth pass kind and a new production timer go beyond the plan entry's enumeration"
    location: "project-documents/user/slices/922-slice.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md#systemd"
  - id: F008
    severity: note
    category: code-organization
    summary: "`provider/eodhd/account.py` creates a third home for EODHD HTTP code"
    location: "project-documents/user/slices/922-slice.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md#Component Structure"
  - id: F009
    severity: note
    category: specification-consistency
    summary: "Decision 6's prose and its sketched SQL/mechanism disagree in two places"
    location: "project-documents/user/slices/922-slice.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md#Technical Decisions"
  - id: F010
    severity: pass
    category: architectural-alignment
    summary: "Schedule constants, dependency direction, and named integration seams check out"
    location: "project-documents/user/slices/922-slice.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md#Architecture"
---

# Review: slice — slice 922

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.3

## Findings

### [CONCERN] Rewrites 140-owned `data_status` contracts with no architecture amendment or recorded escalation

The 900 architecture's maintenance-band constraint is explicit: "The originating initiative's contracts are honored, not rewritten… A fix that requires changing a contract is escalated to the owning initiative rather than absorbed here." Decision 6 replaces 140-arch's normative STALE rule (`last_attempt_ts < (today - STALENESS_THRESHOLD)`, with `DAILY_STALENESS_THRESHOLD = 2 days` / `MINUTE_STALENESS_THRESHOLD = 1 day` defined in 140-arch's Constants section) with the walk-anchor rule, and Decision 7 redefines `gap_count` — which 140-arch's "One status view" spec defines as `COUNT(*) from data_gaps within target window` — as open-gaps-only. The two staleness constants are deleted outright. The PM sanctioned a staleness-rule change at the plan level (entry 23, item 4), so the change itself is not the issue; the gap is governance: every prior contract-touching maintenance change in this project landed with an explicit amendment block in the owning architecture (140-arch carries amendment blocks for slices 152, 167, 169; 180-arch for 186, 187), and this design's Success Criteria include only CHANGELOG and README. 140-arch's own amendment also names `/api/v1/status` and `/api/v1/health` as `data_status` readers, and slice 185 exposed the staleness surface to API clients — the design acknowledges only "the API response model's `gap_count` docstring." Add the 140-arch amendment (health rules, constants, `gap_count` definition) to the slice's scope or record the PM escalation, per the band's own rule.

### [CONCERN] Fresh-database migration chain breaks at 021 as specified

The design states the four pre-rendered view variants "are rebuilt by the same builder, so 021's apply-time variant selection keeps working," and that the staleness constants "are deleted." In the current tree, migrations 021/024/028/048 (and 051/052's re-installs) execute module-level pre-rendered output of `_build_data_status_view_sql` at their position in the chain. Deleting the constants forces the builder to emit the walk-anchor rule, so every historical migration that re-executes a variant — starting with 021 on a fresh database — emits SQL referencing `pass_runs`, which does not exist until the new 055 (appended at the chain's end) creates it. Cold start and any full-ledger replay (slice 915's restore drill) then fail at 021 with an UndefinedTable error, contradicting the design's own "Every step leaves `main` deployable." The chain has an exact precedent for the fix — migration 038 was inserted positionally before 019 ("Position-critical front-of-list block… Idempotent on existing DBs") for precisely this hazard — but the design neither positions 055 ahead of 021 nor specifies a `to_regclass('pass_runs')` apply-time branch. The "keeps working" claim is true only of the daily_ohlcv branch 021 already had, not of the new relation dependency. Specify the positional placement (038 precedent) or the apply-time guard in the design.

### [CONCERN] `_interval_literal` deletion claim is factually wrong

The design says "`MINUTE_STALENESS_THRESHOLD` / `DAILY_STALENESS_THRESHOLD` and `_interval_literal` lose their only consumer and are deleted." That is true for the two constants (their only render is the view's STALE CASE and its pre-rendered literals) but false for `_interval_literal`: in the current tree it renders `_LATE_BAR_GRACE_LITERAL`, which feeds the `exchange_completed_close` CTE that computes `target_end_ts` — a CTE this slice does not change — and it is called repeatedly inside `_data_status_doc_comment`, which the design itself says is "re-rendered to document the anchor rule." Deleting it per the design's instruction breaks the migrations module at import. Scope the deletion to the two constants and their pre-rendered literals, and keep `_interval_literal`.

### [CONCERN] Orphan-close rule cannot distinguish a dead process from a concurrently running pass; "the open row per kind" is undefined with two open rows

Decision 4's intent is "an open row from a dead process is closed by the next writer of the same kind," but the mechanism — close any open row of the same kind at the next `open()` — fires on any open row, live or dead, and the schema carries no liveness signal (no pid/hostname, unlike `daemon_heartbeat`). The design itself blesses concurrent same-kind writers: ad-hoc `mt data health` runs against the hourly :50 timer (operators run it by hand routinely, per 921's own verification walkthroughs), on-demand `mt data accounting` (Decision 9; verification step 3), and `--forever` runners writing one row per cycle (Decision 3). A timer firing while a manual run's row is open mislabels a live run `FAILED / abandoned`, and the overview's read shape — "the open row per kind (running now)" — is undefined when two rows are open. Add a liveness discriminator (pid/hostname as `daemon_heartbeat` does, a minimum-age floor before orphan-closing, or a unit-activity check) and define the overview's rendering with multiple open rows per kind. Note this is also the design's enumerated failure mode for the *recorder* path (Decision 4 cites power loss and SIGKILL only); the overlapping-writer case is unhandled and not "TBD" — it is absent.

### [CONCERN] No restated latency target for the NFR-bearing paths the slice modifies

The overview's ten-second target (from the plan entry) is restated with specifics — good. But the same slice flips `mt data status` to summary-by-default and rewrites the `data_status` view, both governed by an NFR the owning architecture records: 140-arch's 2026-08-15 amendment sets the bound as "a no-regression margin against the 7.8 s raw scan slice 167 removed, with the absolute number recorded and tracked by `test/load/test_167_data_status_nfr.py`" (caller-issued pair measured at 2.636 s), and issue #16 already attributes ~37% of that cost to the unfiltered health-count aggregate — the exact aggregate the new default output prints as its first footer line. The design also adds a new always-on query (the second footer line reads per-granularity totals "from `data_gaps` directly" — a table the design's own universe line puts at ~1.6 M rows) with no stated bound, and Success Criteria 4/6 cover unit and integration agreement but no load-tier assertion. Restate the data_status no-regression margin and a target for the new default summary (and the footer query) in this document, and add the load gate to Success Criteria.

### [NOTE] Plan/design drift on the staleness rule's wording

Plan entry 23 item (4) says the view's staleness "follows the configured firing cadence instead of a one-day literal." Decision 6 implements walks-that-happened anchoring and explicitly rejects reading `MT_MINUTE_FIRING_DAYS` into the database. The rejection is reasoned and satisfies the plan's motivating complaint (the whole minute universe flagged STALE six days a week), but the plan entry now misdescribes the design — 921's round-2 review treated exactly this class of drift as a finding to fix (F005, "plan entry drift… reconciled to the design"). Item (3) has the same shape: the plan says the footer counts "UNKNOWN rows only," the design counts UNKNOWN and FAILED_RETRYABLE — a defensible refinement of "still asking," but drifted. Reconcile the plan entry.

### [NOTE] Fifth pass kind and a new production timer go beyond the plan entry's enumeration

The plan entry enumerates pass rows for "(minute, daily, kalshi, health)" and describes the universe line as "cached from its last run with its timestamp." The design adds kind `accounting` as a writer plus `mt-accounting.timer` (daily 16:30 UTC) — new supervised production surface the plan entry does not name. It is justified (without the timer the cache depends on a human running a 90-second command, which the plan itself notes) and it follows the 916 install pattern ("installed by `install-production.sh` like the health units"), but 916 also established a documented unit-naming pattern and add-a-source checklist that the design should reference for the new unit, and the plan entry should be extended so the timer is sanctioned where the pass kinds are enumerated.

### [NOTE] `provider/eodhd/account.py` creates a third home for EODHD HTTP code

The current tree keeps outbound provider clients in `api/` (`eodhd_sync.py`, whose `eodhd_get` carries retry/backoff, 429 handling, token redaction, and quota-bucket integration) and the provider registry in `providers/` (profiles/auth, no clients). The design places `fetch_credit_usage` in a new `provider/eodhd/` package and does not say whether it reuses the existing wrapper or opens a parallel HTTP path with its own five-second timeout. Name the layer it lives in and whether it goes through the existing EODHD wrapper; also name the missing-API-key case explicitly in the `unavailable (<reason>)` branch rather than leaving it implicit under "any failure."

### [NOTE] Decision 6's prose and its sketched SQL/mechanism disagree in two places

(a) The prose says "Until the first recorded walk the anchor is NULL and nothing reads STALE," but the sketched SQL keeps `WHEN ast.last_attempt_ts IS NULL … THEN 'STALE'` — never-attempted symbols still read STALE, which is what 140-arch intends ("A symbol that the daemon has never attempted shows as STALE"). Pick one and let the re-rendered view comment state it. (b) The anchor's stated meaning ("a run that attempts every active symbol of its granularity carries a `walk_anchor_at`") differs from the mechanism (set at `open()` whenever the firing requires the trailing phase, before any symbol is attempted). The quota-cut case is embraced ("including a walk cut short by quota"), but a `PROVIDER_UNAVAILABLE` pass that aborts after a handful of symbols still carries the anchor and will mark essentially the whole universe STALE; state that this is intended, since it is the one abort class the design does not mention.

### [PASS] Schedule constants, dependency direction, and named integration seams check out

The design's firing-schedule constants match the deployed unit files exactly (daily 00:35 and 12:35 UTC, Kalshi hourly :20, health hourly :50, minute 13:05), so "next firing without reading systemd" is grounded, and asserting them in the existing unit-file drift test is the right enforcement point. Dependency direction holds: the recorder is CLI-constructed and threaded as a callback so the cycle functions take no new dependency, consistent with how the runner already threads its callbacks; the overview reads only through the repository; writers write. `PassKind`/`PassRunOutcome` rendered into CHECK constraints mirrors the established enum-to-CHECK pattern, satisfying the architecture's no-magic-strings principle, and consuming 921's enums unchanged with an exhaustiveness assert keeps the 921 contract honored rather than rewritten. The named integration seams exist as described: the runner's `_loop` cycle call sites and the `report is None` path the outcome mapping cites, `data_health`/`data_accounting` as CLI commands, `MINUTE_SEED_PROGRESS_LOG_INTERVAL = 250` matching the stated progress cadence, `Runner`'s connection-factory injection, and migration ids 055/056 continuing a chain that currently ends at 054.
