---
docType: tasks
slice: collection-pass-and-supervised-install
project: trading-data
lld: user/slices/263-slice.collection-pass-and-supervised-install.md
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [262, 916]
interfaces: [264, 265]
projectState: >
  Slice 262 (catalog sync with settlement capture) is merged on main at
  b91a55f: CatalogSync.run(), classify/SyncOutcome, EXIT_BY_OUTCOME,
  open_sync_connection (preflight + advisory lock), the JSONL event sink,
  and mt data kalshi sync/status. Slice 916 is cut over on manta9000:
  /opt/manta-trading at a pinned tag, manta-trading service account,
  /etc/manta-trading.env, manta-acquisition.slice, install-production.sh,
  mt-run, runbook 100. The kalshi migration track is applied to production
  through kalshi_004. The first production sync (historical drain) is being
  run by hand from the dev checkout. No pass command, phase contract, or
  kalshi unit files exist yet. Design 263 reviewed PASS; Decision 8
  PM-ratified 20260825.
dateCreated: 20260825
dateUpdated: 20260825
status: not_started
---

## Context Summary

- Working on **263 Collection Pass and Supervised Install** — the bounded
  `mt data kalshi pass` command (phase contract with the catalog phase as its
  only member), the `mt-kalshi-pass.service`/`.timer` pair installed by the
  916 install script, `mt-run kalshi`, and the runbook 100 additions. From
  cutover on, catalog and settlement data accumulate with no operator action.
- Source of truth: the slice design at
  `user/slices/263-slice.collection-pass-and-supervised-install.md`. Its
  **Technical Decisions 1–10**, **Implementation Details**, **Tests**,
  **Success Criteria 1–12**, and **Verification Walkthrough** are referenced
  below rather than restated. Read the design before starting any section.
- Design principle: **nothing is invented**. The pass is a thin composition
  over 262's `CatalogSync`; the unit pair is a copy of `mt-daily-pass.service`
  with a different `ExecStart` and schedule; `mt-run` gains one verb by making
  its kind list data. The only behavior change to a 916 artifact is the
  `mt-run` root-branch environment fix (Decision 6).
- Code to reuse, not reinvent: `cli/commands/kalshi.py::run_sync` (the
  preflight being extracted), `data/kalshi/{sync,sync_settled,events,db}.py`,
  `test/kalshi_support/` (fake source, fake repository, sync harness),
  `test/integration/test_kalshi_sync.py` (`kalshi_db` fixture, held-lock
  test), `deploy/systemd/mt-daily-pass.{service,timer}`, `deploy/mt-run`,
  `deploy/install-production.sh`, `test/unit/test_backup_scripts.py` (the
  repo-file consistency-test shape).
- Hard rules: exit codes are 262's `EXIT_BY_OUTCOME` verbatim — no new
  constants; `"skipped"` is never a `SyncOutcome` member; no new catch-all
  (only `ProviderError` / `psycopg.OperationalError` are caught); the
  timer cadence lives only in the unit file; nothing references `public`;
  credentials never enter a tracked file; the installer enables nothing.
- Tests: unit tier `uv run pytest test/unit -q`; integration only through
  `uv run python scripts/run_tests.py integration -- -k kalshi -q` (never with
  the production URL). Gates: `uv run ruff check …`, `uv run --extra dev mypy
  src/manta_trading/data/kalshi src/manta_trading/cli/commands/kalshi.py`,
  `npx --yes pyright` strict on the same paths plus tests, and
  `shellcheck deploy/mt-run deploy/install-production.sh` by hand.
- Host boundary (as 916): manta9000 has no passwordless sudo. Tasks marked
  **[PM]** are executed by the Project Manager and are phrased "run X; record
  the observed output". Tasks marked **[agent]** need no elevation. No task
  waits on a wall-clock event: timer firing is proven by `systemctl start` on
  the unit the timer activates plus `list-timers` for the schedule.
- Branch per CLAUDE.md git rules: `263-slice.collection-pass-and-supervised-install`
  from `main` (no integration branch configured). Commit checkpoints are
  marked; semantic prefixes. Merge and release tagging follow runbook 100's
  update procedure after PM approval and are not tasks here.
- Next slice: 264 (candles) appends a `PassPhase` to `PASS_PHASES`.

## Section 1: Shared preflight context `KalshiRun`

Decision 1 and *Architecture*. Placement note (the design's diagram shows the
context under the CLI; the data package must be able to import `KalshiRun`
for `PassPhase.run`, so the dataclass and the context manager live in the
data package and only the exit-code mapping stays in the CLI).

- [ ] **Task 1.1: Create `data/kalshi/run_context.py`** (effort: 2)
  - [ ] Frozen dataclass `KalshiRun(settings, client, conn, sink, run_id,
        clock)` — exactly the six fields the design names.
  - [ ] `open_kalshi_run(settings, events_file: Path | None)` as an
        `asynccontextmanager` that owns the lifetimes in `run_sync`'s current
        order: `KalshiClient.from_settings`, `open_sync_connection`, sink
        (`JsonlSyncEventSink` if `events_file` else `NullSyncEventSink`),
        `run_id = uuid4()`; on exit closes client, connection (releases the
        lock), and a JSONL sink — mirroring `run_sync`'s `finally` block.
  - [ ] `KalshiCredentialError` and `PreflightError` propagate unchanged (the
        CLI maps them to exit 1 once — Task 1.2). If the client is built and
        the connection fails, the client is closed before re-raising.
  - [ ] Success: module imports; no import from `manta_trading.cli`; the
        dataclass has no other fields.

- [ ] **Task 1.2: Rewire `run_sync` onto the context** (effort: 2)
  - [ ] `run_sync` becomes: preflight via a small shared helper that opens
        the context and turns the two preflight exceptions into
        `print_error` + `EXIT_PREFLIGHT`; run `CatalogSync`; classify; print.
        Observable `sync` behavior (output, exit codes, events file) is
        unchanged.
  - [ ] The preflight-to-exit-1 mapping exists in exactly one place; the
        `ProviderError` / `OperationalError` catches around `sync.run` stay
        as they are.
  - [ ] Success: `uv run pytest test/unit/cli/commands/test_data_kalshi.py
        test/unit/data/kalshi -q` green with no test edited.

- [ ] **Task 1.3: Unit tests for the context** (effort: 1)
  - [ ] In `test/unit/data/kalshi/test_run_context.py` (fakes patched at the
        module seams, as `test_data_kalshi.py` patches today): the context
        yields a `KalshiRun` whose `run_id` is a `UUID`; on normal exit the
        client and connection are closed and a JSONL sink is closed; a
        connection preflight failure closes the client and re-raises
        `PreflightError`; a credential failure re-raises
        `KalshiCredentialError` without opening a connection.
  - [ ] In `test_data_kalshi.py`: the preflight-exit-1 assertion is stated
        **once** for the shared helper (design *Tests*), not per command.
  - [ ] **Commit**: `refactor: extract KalshiRun preflight context from run_sync`.

## Section 2: Phase contract and pass sequencing (`collection_pass.py`)

Decision 2 and *Implementation Details → collection_pass.py*.

- [ ] **Task 2.1: Contract types** (effort: 2)
  - [ ] New `data/kalshi/collection_pass.py`: `PassPhaseName` StrEnum with
        the single member `CATALOG = "catalog"` (comment: 264 adds `CANDLES`,
        265 `TRADES`); frozen `PhaseReport(name, outcome: SyncOutcome |
        Literal["skipped"], summary: dict[str, Any], duration_ms: int,
        error: str | None)`; `PassPhase` Protocol (`name: PassPhaseName`,
        `async run(self, run: KalshiRun) -> PhaseReport`); frozen
        `PassResult(run_id, started_at, reports, outcome, duration_ms)` with
        `to_dict()` producing the design's JSON shape
        (`phases: [{name, outcome, duration_ms, summary}]`, `exit_code` from
        `EXIT_BY_OUTCOME`).
  - [ ] Define the `"skipped"` literal once (a module constant) and reference
        it everywhere it is compared.
  - [ ] Success: module imports; `PhaseReport.outcome` type never admits
        `"skipped"` into `SyncOutcome`; `to_dict()` output round-trips
        through `json.dumps`.

- [ ] **Task 2.2: `classify_pass` and `CollectionPass.run`** (effort: 2)
  - [ ] `classify_pass(reports) -> SyncOutcome`, pure: `STORAGE_ABORT` if any
        report is storage-aborted, else `PROVIDER_ABORT` if any
        provider-aborted, else `PARTIAL` if any partial, else `OK`. Skipped
        reports never influence the result.
  - [ ] `CollectionPass(run: KalshiRun, phases: Sequence[PassPhase])` with
        `async run() -> PassResult`: log the start line
        (`kalshi pass started run_id=… mode=… budget=…/min phases=…` from
        `client.mode` / `client.rate_limit`), emit `PASS_STARTED`; run phases
        in order; after a `PROVIDER_ABORT`/`STORAGE_ABORT` report, do not run
        the remainder and append `skipped` reports for them (`duration_ms=0`,
        `summary={}`); a `PARTIAL` report continues; emit `PASS_FINISHED`
        (`phase=None`, `error` = aborting phase's error or `None`,
        `duration_ms` = pass duration); log the finish line
        (`kalshi pass finished outcome=… exit=… duration=… phases: catalog=…`).
  - [ ] `PASS_PHASES: tuple[PassPhase, ...]` is declared here as the single
        registration point — filled in Task 3.2 (empty tuple is not a valid
        end state; the section-3 test asserts the first member).
  - [ ] Exceptions other than the two caught inside a phase propagate — no
        new `try/except` around `phase.run` in `CollectionPass`.
  - [ ] Success: the sequencing and aggregation rules above are visible as
        one loop and one pure function; no I/O other than logging and
        `sink.emit`.

- [ ] **Task 2.3: Unit tests with fake phases** (effort: 2)
  - [ ] `test/unit/data/kalshi/test_collection_pass.py` with scripted
        `PassPhase` fakes (record call order; return a chosen outcome or
        raise a chosen exception) and a recording sink (the `test_events.py`
        shape): execution order equals tuple order; a `PROVIDER_ABORT` first
        phase leaves the second `skipped` and pass outcome `PROVIDER_ABORT`;
        same for `STORAGE_ABORT`; a `PARTIAL` first phase runs the second and
        the pass is `PARTIAL`; `classify_pass` table-driven over every
        ordered pair of `SyncOutcome` values plus `skipped` (Criterion 3);
        `pass_started`/`pass_finished` emitted once each, both carrying the
        run's `run_id`; a `RuntimeError` from a phase propagates out of
        `CollectionPass.run`.
  - [ ] Tests target `SyncEventType.PASS_STARTED`/`PASS_FINISHED` — added in
        Task 3.1; write these two assertions so they fail until then, or
        order the work so Task 3.1's one-line enum change lands first.
  - [ ] **Commit**: `feat: add kalshi collection pass contract and sequencing`.

## Section 3: Catalog phase, shared `run_id`, pass events

Decision 3.

- [ ] **Task 3.1: `run_id` on `CatalogSync` and the two event types** (effort: 1)
  - [ ] `SyncEventType` gains `PASS_STARTED = "pass_started"` and
        `PASS_FINISHED = "pass_finished"`; `SyncEvent` shape unchanged.
  - [ ] `CatalogSync.__init__` gains `run_id: UUID | None = None`; `None`
        keeps today's fresh `uuid4()` so `sync` is untouched.
  - [ ] Success: existing `test_sync_core.py`, `test_events.py`, and
        `test_data_kalshi.py` pass unchanged; a new test in `test_sync_core.py`
        shows a supplied `run_id` appears on every emitted event.

- [ ] **Task 3.2: `CatalogPhase` and `PASS_PHASES`** (effort: 2)
  - [ ] `CatalogPhase` in `collection_pass.py`: `name = PassPhaseName.CATALOG`;
        `run(run)` constructs `CatalogSync(run.client,
        CatalogRepository(run.conn), run.sink, run_id=run.run_id)`, awaits
        `run()`, catches exactly `ProviderError` and
        `psycopg.OperationalError` the way `run_sync` does today (storage:
        `logger.exception`), and returns `PhaseReport(outcome=classify(
        sync.result, exc), summary=sync.result.to_dict(), error=str(exc)
        or None, duration_ms=…)`.
  - [ ] `PASS_PHASES = (CatalogPhase(),)`.
  - [ ] Success: `run_sync`'s classification and `CatalogPhase`'s use the
        same `classify` call; no duplicated outcome mapping.

- [ ] **Task 3.3: Unit tests for the catalog phase** (effort: 2)
  - [ ] In `test_collection_pass.py`: `PASS_PHASES[0].name is
        PassPhaseName.CATALOG`; `CatalogPhase` run through a real
        `CatalogSync` on the `test/kalshi_support` fake source and fake
        repository reports `OK` with `summary == result.to_dict()`; a fake
        source raising `ProviderError` mid-run reports `PROVIDER_ABORT` with
        `error` set; a fake repository raising `psycopg.OperationalError`
        reports `STORAGE_ABORT`; an item error yields `PARTIAL`.
  - [ ] Full-pass event order with the real phase (Criterion 4): recording
        sink shows `pass_started, run_started, phase_finished ×5,
        run_finished, pass_finished`, one `run_id` throughout.
  - [ ] **Commit**: `feat: add kalshi catalog phase with shared run_id and pass events`.

## Section 4: `mt data kalshi pass` CLI

Decision 1 and *Implementation Details → CLI*.

- [ ] **Task 4.1: `run_pass` and the `pass` command** (effort: 2)
  - [ ] `run_pass(settings, events_file, json_output) -> int` in
        `cli/commands/kalshi.py`, mirroring `run_sync`: shared preflight
        helper (Task 1.2) → `CollectionPass(run, PASS_PHASES).run()` →
        print → `EXIT_BY_OUTCOME[result.outcome]`.
  - [ ] Typer command `pass` with `--events-file PATH` and `--json` only (no
        phase options — Decision 1); reuse the existing option objects.
  - [ ] Rich summary: one row per phase (`Phase · Outcome · Duration`) then
        the catalog phase's existing summary block (reuse `print_summary`'s
        body for that block — do not duplicate it); `--json` prints
        `PassResult.to_dict()`.
  - [ ] Keep the module at ~300 lines: if adding `pass` pushes it over, move
        the Rich rendering helpers (`print_summary`, `print_status`, `_when`,
        the new phase table) to `cli/commands/kalshi_render.py` first, then
        add the command. Update the module docstring ("Slice 263 reuses
        both" is now true).
  - [ ] Success: `uv run mt data kalshi pass --help` lists exactly the two
        options; `uv run mt data kalshi sync --help` unchanged.

- [ ] **Task 4.2: Unit tests for `run_pass`** (effort: 2)
  - [ ] In `test_data_kalshi.py` with the same patching seams as the `sync`
        tests: exit codes 0/2/3/4 per outcome via fake phases (Criterion 2);
        `--json` payload has keys `run_id, started_at, phases, outcome,
        exit_code, duration_ms` and `phases[0].name == "catalog"`; the Rich
        path prints a row per phase; `--events-file` writes the file. The
        preflight exit-1 case is already covered once (Task 1.3) — do not
        repeat it.
  - [ ] **Commit**: `feat: add mt data kalshi pass command`.

## Section 5: Per-window settled log line (Decision 8, PM-ratified)

- [ ] **Task 5.1: INFO line after each watermark write** (effort: 1)
  - [ ] In `sync_settled.py`, immediately after a completed window's
        watermark write (the block at the `set_watermark` call): one
        `logger.info` — `settled window {start}→{end} fetched N written M
        ({k} windows)` where `k` is the running window count in this run.
        Nothing else in the module changes.
  - [ ] Success: a steady-state run logs the line once; the message contains
        no f-string-evaluated SQL or ticker lists.

- [ ] **Task 5.2: Unit test on the log record** (effort: 1)
  - [ ] In `test_sync_settled.py`, with `caplog` at INFO: a fake source
        spanning three windows produces exactly three records matching the
        pattern, with `k` = 1, 2, 3 and the window bounds in order
        (Criterion 11); a run with no completed window logs none.
  - [ ] **Commit**: `feat: log one INFO line per completed kalshi settled window`.

## Section 6: Deploy artifacts — units, installer, `mt-run`, env example

Decisions 4–7 and 10; *Implementation Details → Unit files / mt-run /
manta-trading.env.example*. All **[agent]**, repo-only; nothing is installed
here.

- [ ] **Task 6.1: `deploy/systemd/mt-kalshi-pass.service`** (effort: 1)
  - [ ] Copy `mt-daily-pass.service`; change only `Description=` (the
        design's text), `ExecStart=/opt/manta-trading/.venv/bin/mt data
        kalshi pass`, `TimeoutStartSec=infinity`, and **remove** the
        `TimeoutStopSec` override, replacing it with the design's comment
        (no clean-stop protocol; Decision 5). `Type=oneshot`, `User`/`Group`,
        `Slice=manta-acquisition.slice`, `WorkingDirectory`,
        `EnvironmentFile`, journal outputs, hardening block: byte-identical
        to the EODHD units. No `Restart=`, no `[Install]`.
  - [ ] Success: `diff deploy/systemd/mt-daily-pass.service
        deploy/systemd/mt-kalshi-pass.service` shows only the lines above;
        `systemd-analyze verify` on the file reports no errors (if the local
        host lacks the referenced slice, only that warning appears).

- [ ] **Task 6.2: `deploy/systemd/mt-kalshi-pass.timer`** (effort: 1)
  - [ ] Copy `mt-daily-pass.timer`; `[Timer]` becomes the design's block:
        `OnCalendar=*-*-* *:20:00 UTC`, `Persistent=true`, with the two
        comment lines (why `:20`; steady-state ≈ 2–3 min). `[Install]
        WantedBy=timers.target`. `Unit=` names the service from Task 6.1.
  - [ ] Success: `systemd-analyze calendar '*-*-* *:20:00 UTC'` prints
        hourly next-elapse times; the file has no other schedule lines.

- [ ] **Task 6.3: `install-production.sh`** (effort: 1)
  - [ ] Append `mt-kalshi-pass.service` and `mt-kalshi-pass.timer` to the
        `UNITS` array; the closing "Cutover (later, explicit)" hint lists
        `mt-kalshi-pass.timer`. Nothing else changes — the script still
        enables nothing, and the "ALREADY CUT OVER" branch is correct as-is
        (Decision 10).
  - [ ] Success: `grep -c 'mt-kalshi-pass' deploy/install-production.sh`
        ≥ 3; `bash -n deploy/install-production.sh` clean.

- [ ] **Task 6.4: `mt-run` — kinds become data** (effort: 2)
  - [ ] `KINDS=(daily minute kalshi)` defined once near the top; `unit_for`
        validates `$1` against `KINDS` (else `usage`) and echoes
        `mt-${1}-pass.service`; `show_status` loops `"${KINDS[@]}"`; the
        dispatch `case` accepts every kind (derive the pattern from `KINDS`
        or check membership before `start_and_stream`); header comment and
        `usage` gain `sudo mt-run kalshi` and `mt-run follow kalshi`.
  - [ ] Success: `bash -n deploy/mt-run` clean; the strings `daily` and
        `minute` appear in the script only inside `KINDS` and the usage
        comment.

- [ ] **Task 6.5: `mt-run` — root branch forwards every `MT_*` variable** (effort: 2)
  - [ ] In `run_production_mt`'s root branch, after sourcing the env file
        with `set -a`, build the `env` argument list from the names matched
        by `^MT_[A-Z0-9_]+=` **in the file** (indirect expansion of each
        name), replacing the two hard-coded assignments. The non-root branch
        is unchanged (Decision 6). Add a one-line comment naming the bug
        this fixes.
  - [ ] Success: with a scratch env file containing `MT_TIMESCALE_DB_URL`,
        `MT_EODHD_API_KEY`, `MT_KALSHI_REQUESTS_PER_MINUTE`, `MT_LOG_LEVEL`
        and a non-`MT_` line, a local dry run of the list-building code
        (extract to a function so it can be exercised with `bash -c`) yields
        exactly the four `MT_*` names; `bash -n` clean.

- [ ] **Task 6.6: `deploy/manta-trading.env.example`** (effort: 1)
  - [ ] Append the three commented lines from the design under "Optional
        operator tuning" (`#MT_KALSHI_REQUESTS_PER_MINUTE=300`,
        `#MT_KALSHI_API_KEY_ID=`, `#MT_KALSHI_PRIVATE_KEY_PATH=
        /etc/manta-trading-kalshi.pem` with the `0640 root:manta-trading;
        never under /home (ProtectHome)` note). No real value anywhere.
  - [ ] Success: `grep -c '^#MT_KALSHI' deploy/manta-trading.env.example`
        = 3; the file still contains no uncommented credential.

- [ ] **Task 6.7: Consistency test `test/unit/deploy/test_units.py`** (effort: 2)
  - [ ] Create `test/unit/deploy/__init__.py` and `test_units.py` (repo root
        via `Path(__file__).parents[…]` as `test_backup_scripts.py` does).
        Parse the service and timer with `configparser.ConfigParser(
        strict=False, interpolation=None)` (systemd allows repeated keys):
        `ExecStart` ends with `mt data kalshi pass`; `Type=oneshot`;
        `Slice=manta-acquisition.slice`;
        `EnvironmentFile=/etc/manta-trading.env`; no `Restart` key; no
        `TimeoutStopSec` key; timer `Persistent=true` and `OnCalendar`
        contains `:20:00 UTC`; timer `Unit` names the service file.
  - [ ] Both filenames appear inside `install-production.sh`'s `UNITS=( … )`
        block (parse the block, not the whole file); `kalshi` appears inside
        `mt-run`'s `KINDS=( … )`.
  - [ ] The CLI side of the drift guard: the `pass` command exists on the
        `kalshi` Typer app (e.g. `CliRunner` `--help` lists `pass`), so the
        unit's `ExecStart` names a real command (Criterion 5).
  - [ ] Success: `uv run pytest test/unit/deploy -q` green; deleting the
        kalshi line from `UNITS` makes it fail (check once, restore).

- [ ] **Task 6.8: `shellcheck` and commit** (effort: 1)
  - [ ] `shellcheck deploy/mt-run deploy/install-production.sh` clean (no CI
        gate for shell — record the command and its empty output in the
        walkthrough refresh, Task 10.3).
  - [ ] **Commit**: `feat: add mt-kalshi-pass units, installer entries, mt-run kalshi verb`.

## Section 7: Integration test on `kalshi_db`

- [ ] **Task 7.1: `test/integration/test_kalshi_pass.py`** (effort: 3)
  - [ ] Same fixtures and fake source as `test_kalshi_sync.py` (and its
        helper module): `run_pass` end-to-end → exit 0, `sync_state['catalog']`
        row present with `last_full_sync_at` and `watermark_ts` set
        (Criterion 1); a second `run_pass` on the same fixtures writes no
        rows (write-on-change: counts from the JSON summary are 0 for
        series/events/markets written).
  - [ ] Pass ≡ sync: on two fresh databases from the same fixtures, `run_sync`
        and `run_pass` leave identical `sync_state` rows and identical
        per-table row counts.
  - [ ] Held lock: while a connection from 262's held-lock test pattern holds
        `SYNC_ADVISORY_LOCK_KEY`, `run_pass` returns 1 (Criterion 2, lock
        case).
  - [ ] Events order end-to-end with `--events-file` into `tmp_path`
        (Criterion 4 against the real repository).
  - [ ] Success: `uv run python scripts/run_tests.py integration -- -k
        kalshi -q` green (re-run in isolation before investigating a known
        integration-tier flake); the test never reads the production URL.
  - [ ] **Commit**: `test: add kalshi pass integration proofs on kalshi_db`.

## Section 8: Runbook 100 and CHANGELOG

*Implementation Details → Runbook 100 changes*. Edit
`project-documents/user/runbooks/100-production-operations.md` section by
section; cite no job IDs; commands are exactly the ones the walkthrough runs.

- [ ] **Task 8.1: Quick reference and units** (effort: 1)
  - [ ] *What runs by itself*: the `mt-kalshi-pass.service` row (hourly at
        `:20` UTC; next firing resumes it). *Commands*: rows for `mt-run
        kalshi`, `mt-run follow kalshi`, `mt-run data kalshi status`; the
        rollback row lists the kalshi timer.
  - [ ] *The units*: two rows for the pair; the stop-semantics paragraph
        gains the Kalshi case — `15/TERM` is the normal stop, `9/KILL` is not
        (Decision 5).
  - [ ] Success: each new row's command matches Section 6's scripts and
        units verbatim.

- [ ] **Task 8.2: Environment file, passes, pausing, rollback** (effort: 1)
  - [ ] *Environment file*: the optional Kalshi variables and the PEM
        placement rule (`/etc/manta-trading-kalshi.pem`, `0640
        root:manta-trading`, never under `/home`, installed by hand).
  - [ ] *Running the acquisition passes*: `sudo mt-run kalshi`; the
        manual/rollback form `uv run mt data kalshi pass` from the dev
        checkout; `--events-file` is a hand-run tool only (unsupported
        under the unit — `PrivateTmp`/`ProtectSystem`).
  - [ ] *Pausing a source* and *Rollback*: kalshi units added to the
        examples.
  - [ ] Success: the section list in the design's *Runbook 100 changes* is
        fully ticked against the diff.

- [ ] **Task 8.3: Add-a-source step and the *Kalshi* subsection** (effort: 2)
  - [ ] *Adding a source* checklist: insert between steps 5 and 6 — "add the
        kind to `KINDS` in `deploy/mt-run`".
  - [ ] New subsection *Kalshi*: the two status layers (`mt-run status` vs
        `mt-run data kalshi status`); the first-run expectation (a cold
        catalog drains from the historical cutoff and runs long once —
        normally done by hand before cutover); a timer firing while a
        hand-run `sync` holds the lock exits 1 hourly, harmless (Risk 1);
        pointer to the migration-track apply step under *Update procedure*;
        the 429 evidence query (`journalctl -u mt-kalshi-pass.service |
        grep -c retry`, Decision 7).
  - [ ] Update `dateUpdated` in the runbook frontmatter.
  - [ ] Success: Criterion 10's list is satisfied; no "B7"-style opaque
        labels — every reference says what it is.

- [ ] **Task 8.4: CHANGELOG** (effort: 1)
  - [ ] Under `## [Unreleased]`: the pass command and phase contract, the
        unit pair and `mt-run kalshi`, the `mt-run` root-branch env fix
        (called out as a behavior change), the per-window log line, the env
        example variables. Version bump and tag happen at merge per
        runbook 100 (PM).
  - [ ] **Commit**: `docs: add kalshi pass operations to runbook 100 and CHANGELOG`.

## Section 9: Gates and throwaway-database rehearsal (walkthrough steps 1–2)

- [ ] **Task 9.1: Full gate pass** (effort: 1)
  - [ ] Run every command in walkthrough step 2 (unit tier, kalshi
        integration set, ruff, mypy, strict pyright, shellcheck). Record each
        command's final line.
  - [ ] Success: all clean; no new dependency in `pyproject.toml`
        (Criterion 12).

- [ ] **Task 9.2: Rehearsal on a throwaway database** (effort: 2)
  - [ ] Walkthrough step 1 exactly: apply `TRACKS["kalshi"]` to a throwaway
        database on the test cluster (runbook 400 pattern; the shell's
        `MT_TIMESCALE_DB_URL` points at it — never production); `pass
        --events-file`, `pass --json | jq`, the `jq -r .event_type` order
        check, and the lock interaction (`sync` in a second shell while a
        pass runs → exit 1 "another sync holds the run lock").
  - [ ] Record observed output (summary rows, outcome line, event order,
        `run_id` uniformity, the lock message) in
        `user/notes/2026-08-25-263-rehearsal.md` (or the date run).
  - [ ] Success: exit 0 twice; second pass writes ≈ live churn only; event
        order matches Criterion 4.
  - [ ] **Commit**: `docs: record 263 throwaway-database rehearsal`.

## Section 10: Host steps and walkthrough refresh

Walkthrough steps 4–9. **[PM]** steps run on manta9000 in this order; the
agent stages nothing longer than one command line for pasting. The release
must be merged and tagged per runbook 100 before 10.1 (not a task).

- [ ] **Task 10.1 [PM] Precondition check and inert install** (effort: 1)
  - [ ] Run `mt-run data kalshi status`; record output. If it shows a
        hand-run sync still holding the lock or no `last_full_sync_at`, stop
        after 10.1 and report — cutover (10.3) is the PM's timing call; 10.2
        may still proceed (a lock-held pass exits 1, which is itself the
        Criterion 2 proof on the host).
  - [ ] Run `sudo /opt/manta-trading/deploy/install-production.sh --ref
        vX.Y.Z` (the tag just cut) and `systemctl list-unit-files
        'mt-kalshi*'`; record: service `static`, timer `disabled`
        (Criterion 7, inert half). Optionally `sudoedit
        /etc/manta-trading.env` for the commented Kalshi lines.

- [ ] **Task 10.2 [PM] One supervised pass, no cutover** (effort: 2)
  - [ ] Run `sudo mt-run kalshi`; record the start line, 262's phase lines,
        the `settled window` line(s), the finish line, and "Pass complete …
        exited 0".
  - [ ] Run the `journalctl … -o verbose … grep` from walkthrough step 6;
        record `_UID`, `_SYSTEMD_UNIT`, `_SYSTEMD_SLICE`, `_CMDLINE`.
  - [ ] Run `mt-run status` (kalshi row present) and, for Decision 6, both
        `sudo mt-run data kalshi status` and `sudo mt-run data caggs status`;
        with `MT_LOG_LEVEL=DEBUG` set in the env file for one run, confirm
        debug output through the root path (Criterion 6), then restore.
  - [ ] Success: exit 0 as `manta-trading` from `/opt`; both root-path
        commands succeed (EODHD path still sees `MT_EODHD_API_KEY`).

- [ ] **Task 10.3 [PM] Cutover** (effort: 1)
  - [ ] `sudo systemctl enable --now mt-kalshi-pass.timer`; `systemctl
        list-timers 'mt-*'` — record the kalshi NEXT at the coming `:20` UTC
        (Criterion 7, timer half; the schedule is the proof — the first
        autonomous firing is recorded in 10.6 if it has occurred by then).

- [ ] **Task 10.4 [PM] Stop mid-run, pause, resume** (effort: 2)
  - [ ] Walkthrough step 8 verbatim: start with `--no-block`, `sleep 20`,
        `stop`; record the journal's `code=killed, status=15/TERM` and
        `Deactivated successfully` lines and the absence of `SIGKILL`; `sudo
        mt-run kalshi` → exit 0 and `mt-run data kalshi status` shows the
        watermark advanced (Criterion 8); `disable --now` → kalshi absent
        from `list-timers`; `enable --now` → present again (Criterion 9).

- [ ] **Task 10.5 [PM] Rollback rehearsal, kalshi only** (effort: 1)
  - [ ] Walkthrough step 9: `disable --now` the kalshi timer; `uv run mt
        data kalshi pass` from the dev checkout exits 0; `enable --now`
        again; `systemctl list-timers 'mt-*'` shows the EODHD timers
        untouched throughout. Record outputs.

- [ ] **Task 10.6 [agent] Walkthrough refresh and close** (effort: 2)
  - [ ] Replace the design's draft walkthrough expectations with the
        observed output from 9.2 and 10.1–10.5 (the 916 pattern); add the
        shellcheck run from 6.8; if a `:20` firing has occurred, add its
        `mt-run status` line and `journalctl … 'kalshi pass finished'` hit.
        Fill the *Success criteria — where each is proven* table with what
        was actually seen.
  - [ ] Set design `status: complete`, `dateUpdated`; add an entry to
        `user/notes/000-process-journal.md` noting the cutover date and the
        `mt-run` fix.
  - [ ] Delegate checklist updates for this file to `task-checker`.
  - [ ] **Commit**: `docs: refresh 263 walkthrough with observed host output`.
