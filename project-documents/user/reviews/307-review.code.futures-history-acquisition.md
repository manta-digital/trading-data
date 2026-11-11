---
docType: review
layer: project
reviewType: code
slice: futures-history-acquisition
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: FAIL
verdictSource: stated
sourceDocument: project-documents/user/slices/307-slice.futures-history-acquisition.md
aiModel: xiaomi/mimo-v2.6-pro
aiProfile: openrouter
status: complete
dateCreated: 20261109
dateUpdated: 20261109
reviewedSha: 0eea0deb8e03312cb1350953c25929df3e1e6d71
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 36
diffTruncated: false
turns: 20
promptTokens: 1557495
cachedTokens: 1345792
completionTokens: 26893
reasoningTokens: 23839
durationSeconds: 539.0
costUsd: 0.120871
costBasis: alias_pricing
squadronVersion: 0.24.24
categoriesNormalized: true
findings:
  - id: F001
    severity: fail
    category: correctness
    summary: "`product_of_symbols` never matches real `debug requests --json` rows"
    location: "scripts/cutover_304_checks.py:281"
  - id: F002
    severity: concern
    category: architectural-decay
    summary: "`calendar_for` duplicates `constants.calendar_for_product`"
    location: "scripts/cutover_304_checks.py:270"
  - id: F003
    severity: concern
    category: other
    summary: "Unknown session buckets silently read as \"accounted for\""
    location: "scripts/purchase_verdicts.py#_unaccounted"
  - id: F004
    severity: note
    category: documentation-drift
    summary: "Comment counts \"four ranges\" where `_HISTORY_WANTS` defines three"
    location: "src/manta_trading/data/tick/universe.py#_HISTORY_WANTS"
  - id: F005
    severity: note
    category: consistency-rot
    summary: "`over_sessions` can narrate a `None` raw symbol"
    location: "scripts/purchase_accounting.py#over_sessions"
  - id: F006
    severity: pass
    category: other
    summary: "Ceiling derivation and gate ordering verified against the real estimate contract"
    location: "scripts/cutover_304_checks.py:83-99"
---

# Review: code — slice 307

**Verdict:** FAIL
**Model:** xiaomi/mimo-v2.6-pro

## Findings

### [FAIL] `product_of_symbols` never matches real `debug requests --json` rows

`tick_request.symbols` is `TEXT[]` (src/manta_trading/market/schema/migrations/tick.py:124), so psycopg returns a Python list — but `mt data tick debug requests --json` renders every value through `tick_debug_cmds._cell` (src/manta_trading/cli/commands/tick_debug_cmds.py:82–100), whose `isinstance(value, list)` branch turns a list into `",".join(...)`. The rows `evidence.collect()` reads from `mt_json(["data","tick","debug","requests"])["rows"]` therefore carry `"symbols": "ES.FUT"` — a **string**. `product_of_symbols` does `tuple(sorted(symbols)) == entry.symbols`; `sorted("ES.FUT")` iterates characters, so no universe entry can match and it raises `CutoverError: request symbols [...] match no universe product`. Both production consumers hit this on a normal run: `bar_job_dirs` (via `_session_placement`, called on every evidence pass — and 304's ES bars are `ohlcv-1m` requests with `provider_job_id`s) and `retry_exhausted_requests`. The cutover's step 5 would stop every real run with that error. Note also that `Sequence[str]` accepts `str` silently (`str` is a `Sequence[str]`), which is why type checking did not catch it. The new tests feed lists — `test/unit/scripts/test_cutover_304_checks.py::test_bar_job_dirs_*` and `test_purchase_verdicts._request` — i.e. the raw DB row shape, not the `_cell`-rendered JSON the code actually parses; per the project's parsing rule, the fixture must be built through the real `debug requests --json` output. Fix by normalizing in the consumer (split on `,`) and add a fixture that goes through `_cell`/the real CLI JSON.

### [CONCERN] `calendar_for` duplicates `constants.calendar_for_product`

`src/manta_trading/data/tick/constants.py:222` already defines `calendar_for_product(product)` — same `FUTURES_PRODUCT_CALENDAR` lookup, same "has no trading calendar (known: ...)" message (raising `KeyError`). The new `calendar_for` re-implements both. The script layer legitimately needs `CutoverError` instead of `KeyError`, but it should call `calendar_for_product` and translate the exception rather than duplicate the lookup and message (AGENTS.md: do not duplicate logic).

### [CONCERN] Unknown session buckets silently read as "accounted for"

`_unaccounted` covers exactly the nine current `TickSessionStatus` members across `_NEEDS_REASON`, `_NEVER_ACCOUNTED`, `_NEEDS_PUBLICATION` and `_LISTED_NEVER`, but its final `return bucket in _NEEDS_PUBLICATION and ...` returns `False` for any bucket outside all four sets — a future status would be listed in the report and never fail the exit rule, a silent fallback ("never use silent fallback values"). The sibling module `tick_status_render.py:49-52` guards exactly this class of drift with an assert over `set(TickSessionStatus)`; the same mechanical guard (or an explicit stop on an unknown bucket) belongs here.

### [NOTE] Comment counts "four ranges" where `_HISTORY_WANTS` defines three

The rewritten comment above `_HISTORY_WANTS` says "one shared tuple so the four ranges are defined once", but the tuple holds three wants (`definition`, `statistics`, `ohlcv-1m`) shared by the two entries. The stale count should read three (or "the ranges").

### [NOTE] `over_sessions` can narrate a `None` raw symbol

`volume_verdict_dict` emits `raw_symbol` possibly `None` (see `tick_status_render._volume_lines`, which falls back with `v.raw_symbol or v.instrument_id`), but `over_sessions` interpolates `v['raw_symbol']` directly, so an `over` finding can print "… None (traded 9, cleared 5)". Use the same `raw_symbol or instrument_id` fallback the renderer uses.

### [PASS] Ceiling derivation and gate ordering verified against the real estimate contract

`_round_up`/`ceiling_values` round up to the $5 step without raising an exact multiple (matches the parametrized tests), and `ceiling_command`'s `Decimal` math is exact. I verified the surrounding contracts outside the diff: `plan_summary` (src/manta_trading/data/tick/purchase_plan.py:277-304) always emits non-null `planned_usd`/`trailing_30d_usd`, so the unconditional `Decimal(summary["planned_usd"])` in `_estimate_only` cannot hit `None`; estimate `start`/`end` (`TickRequest.start: date.isoformat()`, provider.py:111-112) match `resubmit_origins`' keys (`_cell` renders `range_start`/`range_end` as date isoformat), so the resubmit matching keys align; and `run_gates` runs before `record_start_and_baselines` and `install_release` in both modes, with tests asserting no state writes and no install on refusal.

### Run Digest

- Response length: 5472 chars
- Response is newline-free: no
- Tool calls made: 36
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 131072 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 88868
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / cache write / completion / reasoning: 1557495 / 1345792 / not computed / 26893 / 23839
- Duration: 539.0 s
- Cost: $0.12 (alias_pricing)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
