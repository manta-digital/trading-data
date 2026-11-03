---
docType: review
layer: project
reviewType: slice
slice: statistics-and-the-open-interest-roll-rule
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261103
dateUpdated: 20261103
reviewedSha: 788193b328198007abed63c34648b937cbaf9f58
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 106377
cachedTokens: 11162
cacheWriteTokens: 95211
completionTokens: 6946
durationSeconds: 64.6
costUsd: 0.452197
costBasis: provider
runId: run-20261103-p4-7756c473
squadronVersion: 0.24.16
findings:
  - id: F001
    severity: concern
    category: cost-accounting
    summary: "Purchase cost and plan-inclusion marking are inconsistent"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md:168"
  - id: F002
    severity: concern
    category: nfr-performance
    summary: "The resolved-read performance claim is not backed by the planned measurement"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md:233"
  - id: F003
    severity: note
    category: scope-boundary
    summary: "Completeness principle is met in two steps, with health and accounting deferred to 305"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md:76"
  - id: F004
    severity: note
    category: architecture-alignment
    summary: "The cross-tier check refines the architecture's \"agrees\" to under, over and no_trades"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md:269"
  - id: F005
    severity: note
    category: architecture-alignment
    summary: "The architecture's single-purchase-test spend rule is amended explicitly"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md:306"
  - id: F006
    severity: note
    category: proportionality
    summary: "Proportionality: one separable piece if the slice grows"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md:290"
  - id: F007
    severity: pass
    category: architecture-alignment
    summary: "Layering, dependency direction and integration points"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md#Architecture"
  - id: F008
    severity: pass
    category: error-handling
    summary: "Failure modes on new ingest and read paths are enumerated"
    location: "project-documents/user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md:213"
---

# Review: slice — slice 303

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Purchase cost and plan-inclusion marking are inconsistent

D1 sets `PLAN_INCLUDED_HISTORY[STANDARD][STATISTICS] = None` (included for all history). The architecture's cost-accounting rule says the guard counts only marginal spend for plan-included pulls. D11 and the walkthrough step 2 treat the same pull as $0.037 of real, usage-billed money "inside the existing $0.50 ceiling". Only one of these can hold for this purchase. The architecture says in-plan pulls cost the guard nothing, and that usage-billed purchases outside a subscription are where invoice reconciliation applies. If the throwaway pass marks these requests plan-included while Databento bills them by usage, the guard and the reconciliation will not see them. The design also does not say which plan state applies to the throwaway run.

The "inside the ceiling" figure also leaves out the October `definition` purchase (line 306). That cost is never quantified.

State the plan state under which the pass runs. State how the guard and the manifest record these requests. Give the combined statistics-plus-definition estimate against the ceiling.

### [CONCERN] The resolved-read performance claim is not backed by the planned measurement

D5 chooses read-time `DISTINCT ON` resolution. The justification is that a five-year schedule read "stays inside its statement timeout". The only measurement D11 plans is over the 2024-08-30 to 2025-01-01 window, about four months. The same claim is repeated at line 467 for `TICK_ROLL_MAX_SESSIONS`. The table reaches 19.4M rows over full history (line 199). Compressed chunks and the `(session_date, stat_type)` index behave differently at that size than on four months of data.

The design also does not say what a roll call does if the read hits the statement timeout. It should be a loud error naming the range, never a partial or empty schedule.

Either state the timeout behaviour and measure or extrapolate to `TICK_ROLL_MAX_SESSIONS` (for example by timing a synthetic or replicated table), or record the scale limit explicitly for 304 and 307 to re-check.

### [NOTE] Completeness principle is met in two steps, with health and accounting deferred to 305

The architecture principle says the slice that adds a schema also extends `status`, `coverage`, `health` and `accounting`. This slice delivers `status` and `coverage` and defers the rest to 305. That matches the architecture's "Futures completeness surfaces" anticipated slice, and `statistics_reads.volume_check` is the stated hand-off, so I don't flag it. The Integration Requirements make 305 depend on a raw-table volume check. Its cost is proportional to the range (D8), so 305 must choose that range deliberately for `health`.

### [NOTE] The cross-tier check refines the architecture's "agrees" to under, over and no_trades

The architecture says `ohlcv-1m` volume "agrees" with cleared volume. D8 refines this because off-Globex volume (blocks, EFP and EFR) makes V < C normal. The reasoning is sound, and only `over` and `no_trades` fail. The slice acknowledges the change. Fold it back into the architecture's wording when it is next revised.

### [NOTE] The architecture's single-purchase-test spend rule is amended explicitly

The $0.04 purchase is an exception to "only the purchase test spends money". The architecture's Anticipated Slices preamble already names it, so the two documents agree. Running it on throwaway databases, with production's universe and manifest untouched, keeps the change bounded.

### [NOTE] Proportionality: one separable piece if the slice grows

The slice carries 13 decisions at effort 4/5. Most of that is justified: the tier, the rule, and the evaluation that proves them on real data. The one piece that cannot be checked on real data here is the bar-basis path. It covers D10's bar-first fallback, the `basis` field on API segments, and the bar-family `volume_check` run. No provider bars are held until 304, so it is exercised only on the derived 2024-12-03 day. It is assigned to this slice by 302's hand-over and by the architecture's "never splice tiers silently", so I don't recommend cutting it. If the slice overruns, this is the part to defer to 304 or 307, together with the `RollSchedule.segments()` basis break.

### [PASS] Layering, dependency direction and integration points

- **Layering:** Statistics get their own family, table and projector, never `minute_ohlcv` or `tick_trade`.
- **Session assignment:** It comes from `ts_ref`, not event time, and revisions resolve by update action and receive time, as the architecture requires.
- **Status reads:** They use the manifest, the ledger and definitions only, never the raw table.
- **Dependencies:** Roll resolution stays pure and reads through `roll_inputs`, so dependencies run from the rule to the data layer, not the reverse.
- **Rule lag:** `RANK_LAG_SESSIONS` is shared by `v` and `n`, which avoids look-ahead and a second constant.
- **Hand-offs:** The ones to 304, 305, 306 and 307 are concrete and consistent with the architecture's slice list.

### [PASS] Failure modes on new ingest and read paths are enumerated

- **Ingest failures:** Each has a named check: `stat_reference` with three reasons, `unit_day`, `resolution`, `overlap`, and the provider = decoded = stored count check.
- **Pre-2010 references:** They are stored with `session_date` NULL and counted, not silently dropped or failed.
- **Missing values in the roll rule:** An absent open interest or an incomplete lag session gives `Unresolved` with a reason naming the contract and session. It never produces a zero.
- **Missing bar ledger rows:** They raise I13 rather than reading 0.
- **Spend guard refusals:** They are recorded, not forced through.
- **Revision edge case:** A `DELETE` winner means no value, and the status check admits it reads only presence.

### Run Digest

- Response length: 7329 chars
- Response is newline-free: no
- Tool calls made: 2
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 3
- Tokens — prompt / cached / cache write / completion / reasoning: 106377 / 11162 / 95211 / 6946 / not computed
- Duration: 64.6 s
- Cost: $0.45 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
