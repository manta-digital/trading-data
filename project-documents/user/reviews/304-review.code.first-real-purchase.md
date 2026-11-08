---
docType: review
layer: project
reviewType: code
slice: first-real-purchase
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, shell.md, sql.md, testing.md]
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/304-slice.first-real-purchase.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261108
dateUpdated: 20261108
reviewedSha: 85963904be675918c0a3afd8d981edcacddf4913
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 5
diffTruncated: false
turns: 6
promptTokens: 406852
cachedTokens: 246736
cacheWriteTokens: 160106
completionTokens: 3933
durationSeconds: 39.0
costUsd: 0.708354
costBasis: provider
squadronVersion: 0.24.24
categoriesNormalized: true
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "Reconciliation reads the checkout's `.env` database with no check that it is production's"
    location: "scripts/reconcile_tick_spend.py:204-213"
  - id: F002
    severity: concern
    category: error-handling-observability
    summary: "An unhandled crash in `reconcile_tick_spend.py` is reported as a judgement failure"
    location: "scripts/cutover_304_evidence.py:150-156"
  - id: F003
    severity: concern
    category: error-handling-observability
    summary: "JSON and shape errors from production commands escape the top-level handler"
    location: "scripts/cutover_304_first_purchase.py:132-140"
  - id: F004
    severity: concern
    category: error-handling-observability
    summary: "The ceilings script rewrites the secrets file in place without atomicity or an awk sample"
    location: "deploy/set-tick-spend-ceilings.sh:41-48"
  - id: F005
    severity: concern
    category: consistency-rot
    summary: "The ceiling values and checkout path are hard-coded in the failure message"
    location: "scripts/cutover_304_checks.py:31-34"
  - id: F006
    severity: note
    category: other
    summary: "Exception-as-flow for absent keys is justified by inline comments"
    location: "scripts/cutover_304_checks.py:71-90"
  - id: F007
    severity: note
    category: consistency-rot
    summary: "Test hook import ordering and module-level `sys.path` insertion"
    location: "scripts/cutover_304_first_purchase.py:27"
  - id: F008
    severity: pass
    category: test-debt
    summary: "Universe configuration and its tests"
    location: "src/manta_trading/data/tick/universe.py:69-110"
---

# Review: code — slice 304

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Reconciliation reads the checkout's `.env` database with no check that it is production's

`_run` connects with `database_url(Settings())`. That resolves the tick application URL from the dev checkout's `.env`. The cutover's other evidence goes through `sudo mt-run`, which reads production's env file. The docstring says the checkout `.env` holds dev ceilings, not production's. `cutover_common.py` already has a `_url_identity` comparison, built to confirm the checkout URL targets the same database as production, but the 304 scripts never call it. If the two differ, the manifest comes from the wrong database. Reconciliation would then compare the wrong manifest with Databento's job list, either failing with "provider job has no manifest row" or passing against the wrong data. The script is read-only, so nothing is destroyed. The risk is wrong evidence on the slice's one paid purchase. Verify the identity before reconciling, or take the URL from an explicit argument.

### [CONCERN] An unhandled crash in `reconcile_tick_spend.py` is reported as a judgement failure

`_reconcile` treats exit 1 as `RECONCILE_FAILED`, a collected finding, and only exit 2 stops the cutover. `reconcile_tick_spend._run` catches only `ProviderError`. A `TickPreflightError` from `database_url` (unset URL), a `psycopg` connection error, or a `Settings()` validation error escapes as a traceback. Python exits 1 on an uncaught exception, so the crash is recorded as "reconciliation failed (see the narration)". It is not a stop. Stderr is not logged on the exit-1 path either. Catch these in the script and return `EXIT_STOPPED`, or log stderr for any non-zero exit.

### [CONCERN] JSON and shape errors from production commands escape the top-level handler

`main` catches `CutoverError`, `CalledProcessError` and `OSError`. `mt_json` and `fetch_estimate` call `json.loads` on command output. That raises `json.JSONDecodeError`, a `ValueError`, if `mt-run` prints something other than JSON. `judge_estimate` and `ledger_failures` index dicts with `[]`, so a changed payload raises `KeyError`. Both escape as a raw traceback. The `FAILED:` line is not written to the narration log, even though the narration file is the evidence record. Wrap parse and shape errors in `CutoverError` at the boundary, naming the command.

### [CONCERN] The ceilings script rewrites the secrets file in place without atomicity or an awk sample

`printf ... > "$TARGET"` truncates `/etc/manta-trading.env` and then writes it. That file holds the database URLs and the Databento key. If the process is interrupted or the disk fills mid-write, the file is left truncated. Keeping mode and owner does not require a truncating write. `install` and `cp` onto a temp file made with `mktemp` in the same directory, then `cat > "$TARGET"` from that temp file, is a smaller window. Alternatively, keep a backup copy first. Separately, shell.md requires awk beyond a simple substitution to carry a sample input → output comment, and this awk block has none.

### [CONCERN] The ceiling values and checkout path are hard-coded in the failure message

`CEILING_COMMAND` embeds `--per-pass 7 --cap-30d 16` and the absolute `/home/manta/source/repos/manta/trading-data` path. The same numbers also appear in `test_cutover_304_checks.py`, which asserts on that exact string, and in the ceilings script's documentation. Changing a ceiling needs edits in several places. The path breaks when the checkout moves or the script runs from a worktree. CLAUDE.md requires a value used in several places to be defined once. Derive the command from one constant for the ceilings and from `Path(__file__)` for the path.

### [NOTE] Exception-as-flow for absent keys is justified by inline comments

`_ceiling` and `check_env_text` catch `CutoverError` and substitute `""`. Each has an inline comment saying absence is handled right after, so this meets rule (b). The cleaner design would be an `env_value` variant that returns `None` for a missing key, but this is not blocking.

### [NOTE] Test hook import ordering and module-level `sys.path` insertion

The scripts insert into `sys.path` and use `# noqa: E402` on imports. This follows the existing `cutover_common` pattern, so it is consistent. Making `scripts/` a package would remove the suppressions, but that is optional.

### [PASS] Universe configuration and its tests

The history wants use fixed end dates and a single shared tuple. The comment explains why the start is 2010-06-08. `test_universe.py` asserts D1's table exactly. The `UNTIERED` and `use_only` helpers in `test/tick_support/universe.py` strip `history` so existing mechanics tests stay isolated. The reconcile tests cover the pure core, the read-only session ordering, and the "unreadable list is not an empty list" behaviour.

### Run Digest

- Response length: 5470 chars
- Response is newline-free: no
- Tool calls made: 5
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 6
- Tokens — prompt / cached / cache write / completion / reasoning: 406852 / 246736 / 160106 / 3933 / not computed
- Duration: 39.0 s
- Cost: $0.71 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
