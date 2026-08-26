---
docType: slice-design
slice: collection-pass-and-supervised-install
project: trading
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [262, 916]
interfaces: [264, 265]
effort: 2
dateCreated: 20260825
dateUpdated: 20260825
reviewVerdictsAddressed:
  - 263-review.slice (claude-sonnet-5, PASS, notes F005/F006)
status: in_progress
---

# Slice Design: Collection Pass and Supervised Install (263)

## Overview

This slice turns 262's hand-run catalog sync into unattended production collection. It delivers the bounded pass command — `mt data kalshi pass` — that composes the initiative's collection phases in order (catalog only today; 264 and 265 append candles and trades) and exits nonzero on failure, and it wires that command into production exactly the way slice 916 established and the 20260823 ADR made a standing rule: a `mt-kalshi-pass.service` oneshot unit under `manta-acquisition.slice`, fired by `mt-kalshi-pass.timer`, installed by `deploy/install-production.sh` into the pinned `/opt/manta-trading` checkout, driven by `mt-run` (`sudo mt-run kalshi`, `mt-run status`, `mt-run follow kalshi`), and documented in runbook 100.

From the cutover step of this slice's walkthrough on, catalog and settlement data accumulate in production with no operator action. That is the moment the initiative's time-sensitive value begins.

Design principle for the whole slice: **nothing is invented**. The pass is a thin composition over 262's `CatalogSync`; the unit pair is a copy of `mt-daily-pass` with a different `ExecStart` and schedule; `mt-run` gains one verb by making its kind list data instead of two hard-coded cases. Where this slice does touch existing pieces beyond adding a name, it is to close a real gap found while designing (the `mt-run` root branch drops every environment variable except two — see Decision 6).

## Value

- **Unattended accumulation.** The catalog lifecycle and the settlement record are captured every hour without a human at a terminal. Markets settled since the historical cutoff keep being drained ahead of the cutoff's advance.
- **Operational parity with EODHD.** Kalshi becomes the third row of `mt-run status`, the third pass timer in `systemctl list-timers 'mt-*'`, and the third entry in every runbook table — the same pause/resume/rollback levers, no new operator vocabulary.
- **Architectural enablement.** 264 and 265 add a phase by appending to one tuple; the unit, timer, install script, wrapper, and runbook do not change when they land.

## Technical Scope

**In scope**

- `mt data kalshi pass [--events-file PATH] [--json]` in `cli/commands/kalshi.py`, backed by a new `data/kalshi/collection_pass.py` (phase contract, phase sequencing, outcome aggregation).
- Extraction of the run preflight (`KalshiClient`, locked `AsyncConnection`, event sink) out of `run_sync` into one shared context so `sync` and `pass` are the same code path up to the phase list.
- Two pass-level structured events (`pass_started`, `pass_finished`) and a shared `run_id` across the pass and its phases.
- One INFO line per completed settled window in `sync_settled.py` (the 260 slice plan's note (a); Decision 8, PM-ratified 20260825).
- `deploy/systemd/mt-kalshi-pass.service` + `.timer`; `install-production.sh` `UNITS` and closing message; `mt-run` kind list + `kalshi` verb + the environment pass-through fix; `manta-trading.env.example` optional Kalshi variables.
- Runbook 100 additions (quick-reference rows, units table, env-file section, pass section, pausing semantics, rollback, the add-a-source checklist's missing `mt-run` step, and a Kalshi-specific first-run/status subsection). CHANGELOG entry.
- Unit and integration tests for the pass; a repository-consistency test tying the unit file to the CLI command and the install script.

**Out of scope**

- Candle and trade phases (264/265) — this slice defines the seam they plug into and nothing more.
- Cross-source arbitration, resource weights on `manta-acquisition.slice`, timer stagger-packing — the 916 gap, inherited unchanged (see *Timer interval* in Decision 4 for how this slice stays out of its way).
- A graceful-stop protocol for the pass (Decision 5 explains why 262's transaction and watermark semantics make one unnecessary).
- Authenticated-mode adoption in production. The env-file and key-placement conventions are documented so the PM can flip it later; the default stays public (Decision 7).
- Any change to `mt data kalshi sync` semantics or 262's schema. No migration in this slice.

## Dependencies

### Prerequisites

- **262 complete and merged** (`b91a55f`): `CatalogSync.run()`, `classify`, `SyncOutcome`, the exit-code constants, `open_sync_connection` (preflight + advisory lock), `SyncEventSink`/`JsonlSyncEventSink`, `mt data kalshi status`.
- **916 complete and cut over** (2026-08-23): `/opt/manta-trading` at a pinned tag, `manta-trading` service account, `/etc/manta-trading.env`, `manta-acquisition.slice`, `install-production.sh`, `/usr/local/bin/mt-run`, runbook 100.
- **kalshi migration track applied to production** through `kalshi_004` (PM action, done 2026-08-24/25). The pass preflight refuses to run against an unapplied track (262's `TRACK_NOT_APPLIED`, exit 1) — the unit would simply fail visibly until it is applied.
- **A warm catalog.** The first production `sync` (the ~4.5M-row drain from the historical cutoff) is being run from the dev checkout as this design is written. The timer must not be enabled while that run holds the advisory lock (the pass would exit 1 at every firing until it finishes — harmless but noisy); the walkthrough sequences cutover after it.
- Host: manta9000, no passwordless sudo — every root step is PM-executed, as in 916.

### Interfaces Required

- `KalshiClient.from_settings(settings)` (261/262): mode and budget selection from `MT_KALSHI_*`; `client.mode`, `client.rate_limit` for the start-of-pass INFO line.
- `open_sync_connection(url)` (262 `db.py`): one autocommit `AsyncConnection`, `sync_state` presence check, `pg_try_advisory_lock(SYNC_ADVISORY_LOCK_KEY)`; closing the connection releases the lock.
- `CatalogSync(source, repository, sink, clock)` — gains an optional `run_id` parameter (Decision 3) so the pass can correlate its events with the phase's; default behavior (fresh `uuid4()`) is unchanged for `sync`.
- `EXIT_BY_OUTCOME` and `SyncOutcome` (262 Decision 11): the pass reuses them verbatim — a pass and a sync that end the same way exit the same way.
- `Settings` (pydantic-settings): `timescale_db_url`, `kalshi_api_key_id`, `kalshi_private_key_path`, `kalshi_requests_per_minute`, `log_level` — all reach the unit through `EnvironmentFile=`.

## Architecture

### Component Structure

```
cli/commands/kalshi.py
  sync   ──┐                        (unchanged surface; operator levers: --settled-since)
  pass   ──┼─► run_context(settings, events_file)   # shared preflight → KalshiRun
  status   │        │
           │        ▼
           │   data/kalshi/collection_pass.py
           │     PassPhaseName (StrEnum)        CATALOG            ← 264 adds CANDLES, 265 TRADES
           │     PassPhase (Protocol)           name, async run(run: KalshiRun) -> PhaseReport
           │     PASS_PHASES: tuple[PassPhase]  (CatalogPhase(),)  ← the one registration point
           │     CollectionPass.run()           sequence, abort rule, aggregate → PassResult
           │     classify_pass(reports)         → SyncOutcome (pure)
           │
           └─► data/kalshi/sync.py  CatalogSync.run()   (the catalog phase's body; unchanged)

deploy/systemd/mt-kalshi-pass.service   ExecStart=/opt/manta-trading/.venv/bin/mt data kalshi pass
deploy/systemd/mt-kalshi-pass.timer     OnCalendar=*-*-* *:20:00 UTC, Persistent=true
deploy/install-production.sh            UNITS += the pair
deploy/mt-run                           KINDS=(daily minute kalshi); verb map derived from it
project-documents/user/runbooks/100-production-operations.md
```

`KalshiRun` is a small frozen dataclass — `settings`, `client`, `conn`, `sink`, `run_id`, `clock` — built by an async context manager that owns the lifetimes of the client, the connection (and therefore the lock), and the sink. `run_sync` becomes "open the context, run `CatalogSync`, print"; `run_pass` becomes "open the context, run `CollectionPass`, print". The preflight failure paths (`KalshiCredentialError`, `PreflightError` → exit 1) exist once.

### Data Flow — one pass

1. **Preflight** (shared): build the client from settings (credential pair validated at construction); open and lock the connection; open the sink (`JsonlSyncEventSink` if `--events-file`, else `NullSyncEventSink`); mint `run_id`. Log at INFO: `kalshi pass started run_id=… mode=public budget=300/min phases=catalog`. Emit `pass_started` (`counts` carries nothing; `phase` is `None`; the phase list is in the log line and the JSON summary).
2. **Phases in order.** For each `PassPhase` in `PASS_PHASES`: run it with the shared `KalshiRun`; collect its `PhaseReport(name, outcome, summary, duration_ms)`. The catalog phase constructs `CatalogSync(run.client, CatalogRepository(run.conn), run.sink, run_id=run.run_id)`, awaits `run()`, and classifies exactly as `run_sync` does today (`classify(sync.result, exc)`); its `summary` is `SyncResult.to_dict()`.
3. **Abort rule.** If a phase reports `PROVIDER_ABORT` or `STORAGE_ABORT`, the remaining phases are **not run** and are reported with outcome `skipped`; the architecture's "catalog sync completes before the time-series surfaces run" is enforced here, not by convention. A `PARTIAL` phase (item errors) does not stop the pass.
4. **Aggregate.** `classify_pass` is pure over the reports: `STORAGE_ABORT` if any phase storage-aborted, else `PROVIDER_ABORT` if any provider-aborted, else `PARTIAL` if any phase was partial, else `OK`. Exit code = `EXIT_BY_OUTCOME[outcome]` — unchanged constants.
5. **Finish.** Emit `pass_finished` (per-phase outcomes in `counts` as `{phase: exit_code}` would abuse the field; instead `error` carries the aborting phase's error text if any and `duration_ms` the pass duration — per-phase detail lives in the JSON summary and the phase's own `run_finished` event). Log at INFO: `kalshi pass finished outcome=ok exit=0 duration=… phases: catalog=ok`. Print the summary (Rich or `--json`), close the context (connection close releases the lock), exit.

Under the timer, stdout/stderr go to journald; `mt-run follow kalshi` shows the two pass lines, 262's per-phase logging, and the new per-window line.

### State Management

The pass owns no state of its own. All persisted progress is 262's `kalshi.sync_state` (and, later, 264/265's watermarks), written by the phases. `run_id` is per process and appears only in events and logs. This is deliberate: a pass that persisted "last pass outcome" would duplicate what systemd already records (`Result`, `ExecMainStatus`, `ExecMainExitTimestamp`) and what `mt data kalshi status` reads from the phases' state — the two status layers the ADR requires, with nothing in between.

## Technical Decisions

1. **The pass is a separate command, and `sync` stays.** `mt data kalshi pass` is what the timer runs: every phase, no phase-specific options. `mt data kalshi sync` remains the catalog phase alone with its operator levers (`--settled-since`) — the replay/repair tool. Both are the same code path up to the phase list (shared `KalshiRun` context), so there is no state divergence between automated and manual operation (ADR point 5). *Rejected:* folding `sync` into `pass --only catalog` — it would move an operator lever onto the timer's command and make the timer's invocation carry options that must never be set there.

2. **Phase contract now, one phase in it.** `PassPhase` (Protocol: `name: PassPhaseName`, `async run(run: KalshiRun) -> PhaseReport`) and `PASS_PHASES` are defined in this slice even though only `CatalogPhase` exists, because the alternative — 264 retrofitting a contract onto a pass that is just `run_sync` under another name — is exactly the "design conversation instead of a procedure" the 916 unit pattern was built to avoid. The contract is as small as it can be: a name, a coroutine, a report. Phase ordering is the tuple's order; the catalog phase is first by construction and a test asserts it. *Rejected:* a registry/plugin mechanism or per-phase enable flags — no caller needs them; `PASS_PHASES` is edited in place when a slice lands, as `TRACKS` is for migrations.

3. **One `run_id` for the pass and its phases.** `CatalogSync.__init__` gains `run_id: UUID | None = None` (default: `uuid4()`, so `sync` is untouched). The pass mints one id and hands it to every phase, so an `--events-file` for a pass reads as one run: `pass_started`, then the catalog phase's `run_started … run_finished`, then `pass_finished`, all with the same `run_id`. `SyncEventType` gains `PASS_STARTED` / `PASS_FINISHED`; `SyncEvent`'s shape is unchanged (`phase=None`, aggregates in `counts`/`error`/`duration_ms`). *Rejected:* a separate pass-event type — the sink Protocol and JSONL sink would need a second shape for two lines.

4. **Timer: hourly at `:20` past, UTC, `Persistent=true`.** Steady-state cost from 262's rehearsal and the first production run: full walk ≈ 181 requests (~45–90 s at the public budget, plus write time), settled stream ≈ 74k/day ≈ 3.1k per hour ≈ 4 pages in one clamped window, awaiting reconciliation a few `tickers` batches → a pass of roughly 300–400 requests, **2–3 minutes** at 300/min. Hourly is 24 passes ≈ 10k requests/day — small against any tier — and keeps the awaiting set within an hour of every close. A market that is created, closes, and settles between two firings (the 15-minute crypto ladders) never appears in a walk and is captured by the settled stream, which is why cadence does not bear on the "no market reaches settlement unobserved" guarantee — 262's design carries it. `:20` starts at none of the EODHD start times (00:35/12:35, 01:05/13:05); the kalshi pass *will* run concurrently with an EODHD pass that is still going (the daily runs ~35–47 min; the minute backfill runs hours) — that is the 916 arbitration gap, inherited on purpose, and Kalshi's 2–3-minute footprint is the "small relative to EODHD" mitigation the architecture named. A pass that overruns the hour absorbs the next firing (systemd never starts a unit against itself). The cadence lives only in the unit file — host configuration per 916, not a code constant. *Rejected:* 15–30-minute cadence — the ADR (journal 20260824 entry) reserves near-real-time for the streaming form, and a tight timer is precisely what forces the deferred arbitration work.

5. **No SIGTERM handler; the pass dies where it stands.** 262 made every unit of work safe to lose: each catalog page is its own transaction, the settled watermark advances only per fully walked window, `sync_state` is written last, the advisory lock is session-level and released when the server sees the socket close. Python's default SIGTERM disposition terminates the process immediately, and systemd treats termination by the signal *it* sent as a clean stop (`Result=success`; journal shows `code=killed, status=15/TERM` then `Deactivated successfully`). So `systemctl stop mt-kalshi-pass.service` costs at most one re-walked page or window on the next firing and needs no cooperation from the process. The unit therefore leaves `TimeoutStopSec` at the default (a comment says why it differs from the EODHD units' 300 s: there is no clean-stop protocol to wait for). *Rejected:* a runner-style flag checked between pages in every phase loop — code in three places to save one page of idempotent re-work. The runbook records what a normal stop looks like in the journal so `15/TERM` is not mistaken for a crash.

6. **`mt-run`: kinds become data; the pass-through forwards every `MT_*` variable.** `KINDS=(daily minute kalshi)` is defined once; `unit_for` maps `kind → mt-${kind}-pass.service` and validates against `KINDS`; `show_status` iterates `KINDS`; the usage block lists the new verb. Second, a real gap found while reading the wrapper: `run_production_mt`'s root branch (`runuser … env HOME=… MT_TIMESCALE_DB_URL=… MT_EODHD_API_KEY=…`) forwards exactly two variables, so `mt-run data kalshi status` run as root would silently lose `MT_KALSHI_*` and `MT_LOG_LEVEL` — a silent fallback by omission. The fix forwards every `MT_*` name present in the env file (the file is the single configuration source; enumerating names in the wrapper is the anti-pattern that produced the bug). The non-root branch (`set -a; . env; exec`) already forwards everything and is unchanged. *This is the one behavior change to an existing 916 artifact and is called out for the reviewer.*

7. **Production rate budget: public default, no code change; levers documented.** The first production run drew 429s on `/events` only, each recovered on retry 1 with ~1 s backoff — the client's bounded retry absorbing a per-endpoint limit, at a cost of seconds per pass. That is the designed behavior ("back off hard"), not a defect, and the PM's read ("we may not really need it yet") stands. The env-file example gains commented `MT_KALSHI_REQUESTS_PER_MINUTE=` (lower the budget) and the authenticated pair `MT_KALSHI_API_KEY_ID=` / `MT_KALSHI_PRIVATE_KEY_PATH=` with the placement rule the units impose: `ProtectHome=true` means the PEM must live outside `/home` — the convention is `/etc/manta-trading-kalshi.pem`, `0640 root:manta-trading`, sibling to the env file, installed by hand (never by the script, never in the repo). Evidence for a later budget decision is already in the journal: the transport logs a WARNING per retry, so `journalctl -u mt-kalshi-pass.service | grep -c retry` over a week answers whether the budget should move.

8. **One INFO line per completed settled window.** Under the timer the journal is the only sink, and `mt-run follow kalshi` during a multi-window catch-up (after a pause, or the first firing after a long gap) currently shows nothing between "markets phase finished" and the end of the drain. `sync_settled.py` logs, after each window's watermark write: `settled window {start}→{end} fetched N written M ({k} windows)`. One line per 6 h of settled history — a steady-state pass logs one; a week's catch-up logs 28. This is the slice plan's note (a) and the only change to 262 code beyond the `run_id` parameter. **PM-ratified 20260825** (see *Design review disposition*, F005).

9. **Exit semantics under systemd are 262's, unchanged.** Exit 1/2/3/4 → `Result=exit-code`, the unit shows `failed`, `mt-run status` prints `last run: exit-code, exit=N`, and the next firing retries — no `Restart=`, no `SuccessExitStatus=`. A `PARTIAL` pass (exit 3) is a failed unit **on purpose**: if the API starts serving a status outside `MarketStatus`, every pass fails visibly until the one-line enum fix ships, rather than succeeding with rows silently skipped. Preflight exit 1 while a hand-run `sync` holds the lock is the same visible failure and the intended mutual exclusion (262 Decision 11).

10. **Install script: add the pair; still enables nothing.** `UNITS` gains `mt-kalshi-pass.service` and `.timer`; the closing "Cutover" hint lists the kalshi timer. Enabling stays a separate, explicit PM step (`sudo systemctl enable --now mt-kalshi-pass.timer`) — the 916 rule that a re-run of the installer can never change what is running. On an already-cut-over host the script's "ALREADY CUT OVER" branch is correct as-is: the new timer is installed but not enabled, and `systemctl list-unit-files 'mt-*'` (already printed) shows it as `disabled`.

## Implementation Details

### `collection_pass.py`

```python
class PassPhaseName(StrEnum):
    CATALOG = "catalog"            # 264: CANDLES, 265: TRADES

class PhaseReport:                 # frozen dataclass
    name: PassPhaseName; outcome: SyncOutcome | Literal["skipped"]
    summary: dict[str, Any]; duration_ms: int; error: str | None

class PassPhase(Protocol):
    name: PassPhaseName
    async def run(self, run: KalshiRun) -> PhaseReport: ...

PASS_PHASES: tuple[PassPhase, ...] = (CatalogPhase(),)

class CollectionPass:              # run(): sequence + abort rule + events; result: PassResult
class PassResult:                  # run_id, started_at, reports, outcome, duration_ms; to_dict()
def classify_pass(reports) -> SyncOutcome
```

"skipped" is a distinct literal rather than a `SyncOutcome` member because it is not a way a phase can *end* — `classify` must never produce it and `EXIT_BY_OUTCOME` must never map it. A phase exception that is neither `ProviderError` nor `psycopg.OperationalError` propagates (the process boundary logs it and exits nonzero via Typer), exactly as `run_sync` behaves today — no new catch-all.

### CLI (`cli/commands/kalshi.py`)

```
mt data kalshi pass [--events-file PATH] [--json]
```
Preflight identical to `sync` (shared context). Summary: the Rich form prints one row per phase (`Phase · Outcome · Duration`) followed by the catalog phase's existing summary block; `--json` prints `PassResult.to_dict()` = `{run_id, started_at, phases: [{name, outcome, duration_ms, summary}], outcome, exit_code, duration_ms}`. `run_pass(settings, events_file, json_output) -> int` is the testable entry, mirroring `run_sync`. The module docstring's "Slice 263 reuses both" becomes true.

### Unit files

`mt-kalshi-pass.service` — a copy of `mt-daily-pass.service` with:
```ini
Description=Manta Trading Kalshi collection pass (one bounded pass, fired by mt-kalshi-pass.timer)
ExecStart=/opt/manta-trading/.venv/bin/mt data kalshi pass
TimeoutStartSec=infinity     # a catch-up pass after a pause legitimately runs long
# No TimeoutStopSec override: the pass has no clean-stop protocol to wait for —
# SIGTERM ends it immediately and 262's per-page transactions / per-window
# watermark make that lossless (design 263, Decision 5).
```
`Type=oneshot`, `User=/Group=manta-trading`, `Slice=manta-acquisition.slice`, `WorkingDirectory`, `EnvironmentFile`, journal outputs, and the hardening block are byte-identical to the EODHD pass units. No `Restart=`, no `[Install]`.

`mt-kalshi-pass.timer`:
```ini
[Timer]
# Hourly; :20 starts at none of the EODHD firings (00:35/12:35, 01:05/13:05).
# Steady-state pass ≈ 2–3 min at the public budget (design 263, Decision 4).
OnCalendar=*-*-* *:20:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
```

### `mt-run`

- `KINDS=(daily minute kalshi)`; `unit_for` returns `mt-${1}-pass.service` after checking membership; `show_status` loops over `KINDS`; the header comment/usage gains `sudo mt-run kalshi` and `mt-run follow kalshi`.
- `run_production_mt` root branch: source the env file with `set -a`, then forward every variable whose name matches `^MT_[A-Z0-9_]+=` in the file to `runuser … env …` (built from the file's names, not a list in the script). Behavior for the non-root branch is unchanged.
- `shellcheck deploy/mt-run deploy/install-production.sh` clean (no CI gate exists for shell; run by hand, recorded in the walkthrough).

### `manta-trading.env.example`

Appends, commented, under "Optional operator tuning":
```
#MT_KALSHI_REQUESTS_PER_MINUTE=300          # lower to ease 429s on /events (public tier)
#MT_KALSHI_API_KEY_ID=                      # authenticated mode: both or neither
#MT_KALSHI_PRIVATE_KEY_PATH=/etc/manta-trading-kalshi.pem   # 0640 root:manta-trading; never under /home (ProtectHome)
```
No value is a real credential; the file is installed only when absent (unchanged).

### Runbook 100 changes

- *What runs by itself*: row `mt-kalshi-pass.service · one bounded Kalshi collection pass · timer: hourly at :20 UTC · next firing resumes it`.
- *Commands*: `mt-run kalshi` / `mt-run follow kalshi` / `mt-run data kalshi status` rows; rollback row lists the kalshi timer.
- *The units*: two rows; the paragraph on stop semantics gains the Kalshi case (`15/TERM` is the normal stop; `9/KILL` is not).
- *Environment file*: the optional Kalshi variables and the PEM placement rule.
- *Running the passes*: `sudo mt-run kalshi`; the manual/rollback form `uv run mt data kalshi pass` from the dev checkout.
- *Pausing a source* and *Rollback*: kalshi units added to the examples.
- *Adding a source* checklist: a step between 5 and 6 — "add the kind to `KINDS` in `deploy/mt-run`" — the step 916's checklist omitted and this slice is the first to need.
- New subsection *Kalshi*: the two status layers (`mt-run status` vs `mt-run data kalshi status`), the first-run expectation (a cold catalog drains from the historical cutoff and runs long once — normally already done by hand before cutover), the lock interaction with a hand-run `sync`, and the migration-track apply step already documented under *Update procedure*.

### Tests

- **Unit — `test/unit/data/kalshi/test_collection_pass.py`:** fake phases returning scripted reports; asserts order of execution, abort skips the remainder with `skipped` reports, partial continues, `classify_pass` over every combination of two phases (table-driven), `pass_started`/`pass_finished` emitted once each with the shared `run_id`, `PASS_PHASES[0].name is CATALOG`, `CatalogPhase` maps `classify(result, exc)` faithfully (reusing `test/kalshi_support` fakes for a real `CatalogSync` run).
- **Unit — `test/unit/cli/commands/test_data_kalshi.py`:** `run_pass` with the fakes: exit codes per outcome, `--json` payload shape, preflight failures exit 1 through the shared context (asserted once, not per command).
- **Unit — `test/unit/deploy/test_units.py`:** parses `deploy/systemd/mt-kalshi-pass.service` and `.timer` (ini via `configparser`, `strict=False`); asserts `ExecStart` ends with `mt data kalshi pass`, `Type=oneshot`, `Slice=manta-acquisition.slice`, `EnvironmentFile=/etc/manta-trading.env`, `Persistent=true`, and that both filenames appear in `install-production.sh`'s `UNITS` block and `kalshi` in `mt-run`'s `KINDS`. This is the drift guard for "the unit runs a command that exists".
- **Integration — `test/integration/test_kalshi_pass.py`** on `kalshi_db`: `run_pass` end-to-end with the fake source → exit 0, `sync_state` set, a second pass writes nothing; a second `run_pass` while the first holds the lock → exit 1 (reusing 262's lock fixture). A pass and a sync are proven interchangeable: same fixtures, same final state.
- Type gate as 261/262: ruff, mypy, strict pyright on the kalshi package, the CLI module, and the tests.

## Integration Points

### Provides to Other Slices

- **264/265:** `PassPhase`, `PhaseReport`, `KalshiRun` (shared client + connection + sink + `run_id`), `PASS_PHASES` as the single registration point, and the abort rule (a phase after an aborted catalog never runs). Adding a phase is: implement `PassPhase`, append to `PASS_PHASES`, extend the JSON summary. No unit, timer, installer, wrapper, or runbook change — the next timer firing after the deploy runs the new phase.
- **Operations:** the third pass in `mt-run status` / `list-timers`; runbook 100 as the single procedure.

### Consumes from Other Slices

- 262's core and exit taxonomy unchanged; 916's install and wrapper extended per its own add-a-source procedure. If 262's preflight fails (track missing, DB down, lock held) the unit fails at exit 1 and retries at the next firing — visible in `mt-run status`, never a tight loop.

## Success Criteria

1. `mt data kalshi pass` on a throwaway database with the kalshi track applied exits 0, runs the catalog phase, and leaves `sync_state` exactly as a `mt data kalshi sync` of the same fixtures would (integration test asserts equal final state).
2. Exit codes: a pass whose catalog phase is partial exits 3; provider abort exits 2; storage abort exits 4; lock held or config missing exits 1 — each proven by a test, with the same constants `sync` uses.
3. Phase sequencing: with two fake phases, an aborting first phase leaves the second `skipped` and the pass outcome equal to the abort; a partial first phase lets the second run; `classify_pass` is covered for every pair of outcomes.
4. `--events-file` for a pass contains `pass_started`, `run_started`, five `phase_finished`, `run_finished`, `pass_finished` in that order, all sharing one `run_id`.
5. `deploy/systemd/mt-kalshi-pass.{service,timer}` exist, are listed in `install-production.sh` `UNITS`, and the consistency test passes; `shellcheck` is clean on both scripts.
6. `mt-run kalshi` starts the unit with live output and returns its exit code; `mt-run status` shows a `kalshi` row; `mt-run follow kalshi` attaches; as root, `mt-run data kalshi status` sees `MT_KALSHI_*`/`MT_LOG_LEVEL` from the env file (proven by setting `MT_LOG_LEVEL=DEBUG` there and observing debug output through the root path).
7. On manta9000: the install script re-run installs the pair and enables nothing (`list-unit-files` shows the timer `disabled`); one `sudo mt-run kalshi` completes as `manta-trading` from `/opt` (`_SYSTEMD_UNIT=mt-kalshi-pass.service`, `_SYSTEMD_SLICE=manta-acquisition.slice` in the journal) with exit 0; after `enable --now`, `list-timers` shows the next `:20` firing and the first autonomous pass completes with exit 0 and no human involved.
8. `systemctl stop` on a running pass ends it with `status=15/TERM`, `Result=success`, no `SIGKILL`; the next pass resumes (the watermark and `last_full_sync_at` advance normally).
9. `disable --now mt-kalshi-pass.timer` removes it from `list-timers`; `enable --now` restores it and fires the missed schedule (or the stamp-file escape hatch is used, as documented).
10. Runbook 100 has the Kalshi rows/sections listed above and the add-a-source checklist includes the `mt-run` step; CHANGELOG has the entry.
11. The per-window INFO line appears once per completed window in a multi-window run (unit test on the log record; live in the journal).
12. ruff, mypy, strict pyright clean; unit tier green; kalshi integration set green; no new dependency.

## Verification Walkthrough

Steps 1–2 carry **observed output** from the 20260825 implementation run; steps 4–9 are still drafts awaiting the host. Steps 5–9 are PM-executed root steps on manta9000, in the same order 916 used: inert install → one supervised run → explicit cutover → supervision proofs → rollback rehearsal.

```bash
# 1. Rehearsal on a throwaway database (test cluster, runbook 400) — RUN 20260825, exit 0 both passes.
#    Full observed output: user/notes/2026-08-25-263-rehearsal.md
#    apply TRACKS["kalshi"] to the throwaway URL; point MT_TIMESCALE_DB_URL at it for the shell only.
uv run mt data kalshi pass --events-file /tmp/kalshi-pass1.jsonl
#    OBSERVED (cold): "kalshi pass started run_id=9d62d64e-… mode=public budget=300/min phases=catalog",
#      one "settled window {start}→{end} fetched N written M (k windows)" per window (244 of them,
#      last clamped to the run start), then "kalshi pass finished outcome=ok duration=2740473 ms
#      phases: catalog=ok"; Rich shows the phase table (catalog · ok · 2,740,245 ms) then 262's block
#      (settled 3,512,111 written) then "pass  outcome ok (exit 0)". 45.7 min, 113 INFO lines,
#      zero WARNING/ERROR, zero 429 retries. A cold drain is long by nature — TimeoutStartSec=infinity.
uv run mt data kalshi pass --json | jq '.phases[].name, .outcome, .exit_code'
#    OBSERVED: "catalog", "ok", 0 — windows: 1 (not 244; the watermark held) in 97 s, writes are
#      live churn only (markets 38,172 over a 96-min gap). Keys: run_id started_at phases outcome
#      exit_code duration_ms.
jq -r .event_type /tmp/kalshi-pass1.jsonl | tr '\n' ' '
#    OBSERVED: pass_started run_started phase_finished ×5 (series markets events settled awaiting)
#      run_finished pass_finished — the three run/pass-level events carry phase=null; one run_id.
# Lock interaction: in a second shell while a pass is running —
uv run mt data kalshi sync            # OBSERVED: exit 1 "another kalshi sync holds the run lock"
uv run mt data kalshi pass            # OBSERVED: exit 1, same message (mutual in both directions)

# 2. Tests and gates — ALL CLEAN 20260825
uv run pytest test/unit -q
#    → 2302 passed, 5 skipped, 21 subtests passed. (40 errors are the pre-existing
#      "MT_TIMESCALE_TEST_URL is not set" DB fixtures in four files this slice does not touch —
#      identical count on main; they are a loud config failure by design, not a regression.)
uv run python scripts/run_tests.py integration -- -k kalshi -q
#    → 51 passed, 321 deselected
uv run ruff check src/manta_trading/data/kalshi src/manta_trading/cli/commands/kalshi.py src/manta_trading/cli/commands/kalshi_render.py test/kalshi_support test/unit/data/kalshi test/unit/deploy test/integration/test_kalshi_pass.py
#    → All checks passed!
uv run --extra dev mypy src/manta_trading/data/kalshi src/manta_trading/cli/commands/kalshi.py src/manta_trading/cli/commands/kalshi_render.py
#    → Success: no issues found in 19 source files
npx --yes pyright src/manta_trading/data/kalshi src/manta_trading/cli/commands/kalshi.py src/manta_trading/cli/commands/kalshi_render.py test/kalshi_support test/unit/data/kalshi test/unit/deploy test/integration/test_kalshi_pass.py
#    → 0 errors, 0 warnings, 0 informations
npx --yes shellcheck deploy/mt-run deploy/install-production.sh    # shellcheck is not installed on the dev host; npx fetches 0.11.0
#    → no output, exit 0
git diff main --stat -- pyproject.toml uv.lock    # → empty: no new dependency (Criterion 12)

# 3. Merge to main, bump/tag the release per runbook 100 (vX.Y.Z matching pyproject); push the tag.

# 4. PRECONDITION — the first production sync from the dev checkout has finished.
#    NOTE (found 20260825): `mt-run` execs /opt/manta-trading/.venv/bin/mt — production's
#    pinned binary. Before step 5 that is still v0.8.0, which has no `kalshi` command at all
#    ("No such command 'kalshi'"), so this check CANNOT be run through mt-run yet. Use the dev
#    checkout, which is where the hand-run sync is running anyway:
cd ~/source/repos/manta/trading-data && uv run mt data kalshi status
#    → last full sync set; settled watermark near now
#    (a pass fired while that run holds the lock exits 1 — harmless, but do not cut over under it)
#    After step 5, `mt-run data kalshi status` works and is the form the runbook uses.

# 5. Install (inert). Same script, new units land; nothing is enabled.
#    RUN IT TWICE (found 20260825). Bash parses the whole script — UNITS=( … ) included —
#    before executing, so the first run is the OLD installer already on the host: it moves the
#    checkout to the new ref and installs the new mt-run, but iterates the old unit list, so a
#    newly ADDED unit is never copied ("Unit mt-kalshi-pass.service not found" on mt-run kalshi).
#    The second run is the new installer and copies the pair. Applies to any release adding a unit.
sudo /opt/manta-trading/deploy/install-production.sh --ref vX.Y.Z      # or from the dev checkout
sudo /opt/manta-trading/deploy/install-production.sh --ref vX.Y.Z      # again: now the new UNITS list
systemctl list-unit-files 'mt-kalshi*'   # service: static · timer: disabled — if absent, STOP
sudoedit /etc/manta-trading.env          # optional: uncomment MT_KALSHI_REQUESTS_PER_MINUTE / auth pair

# 6. One supervised pass, no cutover yet
sudo mt-run kalshi                        # live output; Ctrl-C detaches
sudo systemctl start --no-block mt-kalshi-pass.service; mt-run follow kalshi   # attaches to the live journal; Ctrl-C exits the viewer, pass keeps running
#    expect: "kalshi pass started … mode=public budget=300/min phases=catalog",
#            262's phase lines, one "settled window …" line, "kalshi pass finished outcome=ok exit=0",
#            "Pass complete: mt-kalshi-pass.service exited 0 (success)"
journalctl -u mt-kalshi-pass.service -o verbose --grep 'kalshi pass finished' -n 1 | grep -E '_UID=|_SYSTEMD_UNIT=|_SYSTEMD_SLICE=|_CMDLINE='
#    NOTE: match a payload line. A bare -n 1 returns the LAST journal line, which is usually
#    systemd's own (_UID=0, _CMDLINE=/sbin/init) — that reports PID 1, not the pass (found 20260825).
#    OBSERVED 20260825: _UID=997 (manta-trading, not 0), _SYSTEMD_UNIT=mt-kalshi-pass.service,
#      _SYSTEMD_SLICE=manta-acquisition.slice,
#      _CMDLINE=/opt/manta-trading/.venv/bin/python3 /opt/manta-trading/.venv/bin/mt data kalshi pass
#      (the journal records the interpreter prefix for an exec'd console script; both paths are
#       under /opt/manta-trading/.venv, which is what the field is there to prove)
mt-run status                             # "== kalshi: not running (state=inactive); last run: success, exit=0 …"
sudo mt-run data kalshi status            # root path: same output as the non-root path (env forwarding, Decision 6)

# 7. Cutover — the one explicit step
sudo systemctl enable --now mt-kalshi-pass.timer
systemctl list-timers 'mt-*'              # kalshi NEXT at the coming :20 UTC
#    wait for it; then:
mt-run status                             # kalshi last run: success, exit=0, ended <that :20 + ~3 min>
journalctl -u mt-kalshi-pass.service --since "-1h" | grep 'kalshi pass finished'

# 8. Stop mid-run, pause, resume
sudo systemctl start --no-block mt-kalshi-pass.service; sleep 20; sudo systemctl stop mt-kalshi-pass.service
journalctl -u mt-kalshi-pass.service -n 5   # "code=killed, status=15/TERM" … "Deactivated successfully"; no SIGKILL
sudo mt-run kalshi                           # next pass resumes: exit 0, watermark advances
sudo systemctl disable --now mt-kalshi-pass.timer; systemctl list-timers 'mt-*'   # kalshi absent
sudo systemctl enable --now mt-kalshi-pass.timer                                  # catch-up fires at once if a firing was missed

# 9. Rollback rehearsal (kalshi only — EODHD units untouched)
sudo systemctl disable --now mt-kalshi-pass.timer
cd ~/source/repos/manta/trading-data && uv run mt data kalshi pass    # manual form works from the dev checkout
sudo systemctl enable --now mt-kalshi-pass.timer
```

### Success criteria — where each is proven

Status after Phase 6 implementation (20260825): criteria 1–5, 11 and 12 are
**proven**; 6–10 need the host and are the PM's steps 4–9, still outstanding.

| Criterion | Where | Status |
|---|---|---|
| 1 pass ≡ sync final state | `test_kalshi_pass.py::TestPassEqualsSync` (two fresh databases, identical `sync_state` + row counts) + step 1 | ✅ proven |
| 2 exit codes 0/1/2/3/4 | unit tests per outcome; **live** lock case in step 1 (both `sync` and `pass` exit 1 under a held lock) | ✅ proven |
| 3 phase sequencing + `classify_pass` | `test_collection_pass.py` — abort skips the remainder as `skipped`, partial continues, table-driven over every ordered pair of outcomes | ✅ proven |
| 4 event order, one `run_id` | step 1 live (`pass_started run_started phase_finished×5 run_finished pass_finished`, one id) + unit and integration tests | ✅ proven |
| 5 units exist, installed, listed | `test_units.py` (16 tests, drift-guarded against `UNITS` and `KINDS`), `shellcheck` clean, `systemd-analyze verify` clean | ✅ proven in repo; host half is step 5 |
| 6 `mt-run kalshi` / `follow` / root-path env | step 6 — **needs the host** | ⏳ PM |
| 7 inert install, cutover, first autonomous pass | steps 5–7 — **needs the host** | ⏳ PM |
| 8 stop mid-run is clean and resumes | step 8 — **needs the host** | ⏳ PM |
| 9 disable/enable the timer | step 8 — **needs the host** | ⏳ PM |
| 10 runbook + CHANGELOG | runbook 100 and CHANGELOG updated this slice; the commands in them are step 6/8's verbatim | ✅ written; exercised at step 6 |
| 11 one INFO line per settled window | `test_sync_settled.py` (three windows → three records, k=1,2,3) + **244 lines observed live** in step 1 | ✅ proven |
| 12 gates clean, no new dependency | step 2 — ruff/mypy/strict pyright/shellcheck clean, `git diff main -- pyproject.toml uv.lock` empty | ✅ proven |

## Risk Assessment

- **The timer fires while a long hand-run `sync` holds the lock** (precondition 4 ignored): every firing exits 1 until the run ends. Visible in `mt-run status`, retried hourly, no data effect. Mitigation is sequencing in the walkthrough and a runbook note; no code.
- **The `mt-run` env-forwarding change** touches a script the EODHD passes' operators already rely on. Its non-root branch is unchanged; the root branch is exercised in step 6 both ways (`sudo mt-run data kalshi status` and a `sudo mt-run data caggs status` to prove the EODHD path still sees `MT_EODHD_API_KEY`).

## Design review disposition (20260825)

Review: `user/reviews/263-review.slice.collection-pass-and-supervised-install.md`, claude-sonnet-5, verdict PASS against `a599f0e` (four passes, two notes).

- **F005 (note) — Decision 8 (per-window INFO line) is left to a post-design PM veto.** Both branches are specified; the open gate is deliberate because the slice plan recorded the line as "not yet needed" and the first production run's outcome (in progress at design time) is the evidence for it. **Resolution: ratified by the PM (20260825).** Decision 8 and Criterion 11 stand as written. Rationale recorded: the trigger condition — unattended catch-up with only the journal to watch — is created by this slice's cutover, so the slice plan's "not yet needed" stops holding there; the settled drain is the one phase whose duration scales with elapsed time since the last pass, so it is the one phase that earns a per-unit-of-work line.
- **F006 (note) — the `mt-run` root-branch fix touches shared tooling.** Acknowledged; no action. The non-root branch is unchanged, and the EODHD regression check (`sudo mt-run data caggs status` still sees `MT_EODHD_API_KEY`) is in Criterion 6 / walkthrough step 6.

## Implementation Notes

### Development Approach

Suggested order: `KalshiRun` context extracted from `run_sync` (tests still green, `sync` behavior identical) → `collection_pass.py` with fake-phase unit tests → `CatalogPhase` + `run_id` parameter + pass events → `run_pass` CLI + tests → `sync_settled.py` window line + test → unit pair, installer, `mt-run`, env example + `test_units.py` + shellcheck → integration test on `kalshi_db` → runbook + CHANGELOG → rehearsal (walkthrough step 1) → merge/tag → host steps 5–9 with the PM → walkthrough refresh.

Branch: `263-slice.collection-pass-and-supervised-install` from `main` (no integration branch configured).

### Special Considerations

- Host steps are PM-executed and are **not** slice tasks that wait on a schedule: the task file phrases them as "run X; record the observed output", and the first autonomous firing is observed after cutover as part of the walkthrough refresh, not as a blocking task.
- The pass never reads `MT_TIMESCALE_MAINTENANCE_URL`; the env file continues to omit it (913).
- `--events-file` under the unit is unsupported by design (`PrivateTmp`, `ProtectSystem=full`); it is a hand-run tool. The journal is production's sink.
- Nothing in this slice references `public` schema objects (extraction discipline).
