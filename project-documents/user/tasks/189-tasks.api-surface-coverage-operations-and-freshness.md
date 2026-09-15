---
docType: tasks
slice: api-surface-coverage-operations-and-freshness
project: trading-data
lld: user/slices/189-slice.api-surface-coverage-operations-and-freshness.md
parent: user/architecture/180-slices.data-serving-api.md
dependencies: [188, 922]
interfaces: [190]
projectState: >
  Design 189 committed at ca0cd6b; slice review CONCERNS (which passes the
  gate) with two findings, both addressed at d5d6219 and recorded as D8. No
  re-review required. Slice 922 shipped `pass_runs` (migration 055),
  `PassRunRepository`, `build_overview`/`gather`, `read_source_freshness` and
  `mt data overview` to the CLI only. `src/manta_trading/api_server/` has no
  reference to `pass_runs` or to any pass state. Slice 188 merged nine Kalshi
  routes and established the conventions this slice follows: thin routes over
  a reader, `{"error": "…"}` bodies, `GATEWAY_TIMEOUT_RESPONSE`, the
  `create_app(db_url=)` seam and the `test/load/` tier.
dateCreated: 20260913
dateUpdated: 20260915
status: in_progress
---

## Context Summary

- Working on **189 API surface coverage: operations and freshness** in five
  sections: (1) the `gather` split; (2) response models; (3) the two routes;
  (4) the load tier; (5) documentation and the OpenAPI artifact.
- Source of truth: the slice design. Tasks cite its Technical Decisions
  (D1–D8), *API Specification*, *Testing Strategy* and Success Criteria
  (SC1–SC12). **Read the cited section before starting each task.**
- Section order matters: routes need the split and the models; the load
  fixture needs both routes; `openapi.json` is regenerated last so it is
  written once against the final route set.
- Phase 6 branch: `189-slice.api-surface-coverage-operations-and-freshness`,
  forked from the integration target (`cf config get git.integration_branch`,
  `main` if empty). Verify before Task 1.1.
- **No migration and no new table.** Every row this slice reads was created by
  922's migration 055. If a task appears to need DDL, stop and raise it.
- **No new derivation.** `build_overview` is called unmodified. If a task
  seems to need a second computation of running/last-run/next-firing inside
  `api_server/`, that is SC2 failing — stop and raise it.
- Two behaviors deliberately differ from `mt data overview`, and both are
  pinned by success criteria: `abandoned` is omitted (D4/SC5) and `credits`
  lives on its own route (D2/SC7). Neither is an oversight to "fix".

---

## Section 1: The `gather` split

Design *D3*, *SC2*. The refactor that lets the API reuse 922's reads instead
of copying them. No behavior change.

- [x] **Task 1.1: Confirm the branch and the baseline** (effort: 1)
  - [x] `cf config get git.integration_branch`; call its value (or `main` if
        empty) the target. Create or switch to
        `189-slice.api-surface-coverage-operations-and-freshness` from the
        target.
  - [x] Run the unit tier and record the pass/fail counts. Run the
        integration tier and record which tests already fail on the target —
        `test_migration_051_052` (×2) and `test_policy_advances_head` (×2) are
        known. A failure not on that list at baseline is a new problem and
        must be raised before any code is written.
  - [x] **Capture the SC3 baseline now, before any code changes.** With
        `mt serve` against production read-only, save the response bodies of
        `/api/v1/health` and a fixed `/api/v1/status` request (record the
        exact URL used) to files outside the repo. Task 5.3 diffs against
        these. Captured here because after Section 3 the "pre-slice output"
        no longer exists to capture.
  - [x] Success: the baseline numbers are written into this file under
        Task 1.1 so later sections can attribute regressions, and the two
        SC3 baseline bodies are saved with their request URLs recorded.

  **Baseline, recorded 2026-09-15** (branch created from `main` at `fa78970`;
  `git.integration_branch` is empty, so the target is `main`):

  - **Unit tier:** 3471 passed, 5 skipped, 0 failed (112 s).
    `MT_TIMESCALE_TEST_URL` must be **exported** — unexported, 40 DB-backed
    unit tests error with "MT_TIMESCALE_TEST_URL is not set", which is a
    configuration error and not a code failure.
  - **Integration tier:** 396 passed, 6 failed, 144 skipped (880 s). All six
    failures are pre-existing on the target and none is attributable to this
    slice:
    - `test_migration_051_052.py::TestChainAppliesOnColdStart::test_chain_ends_at_052`
      and `::TestCoverageFreshnessOnRealHistory::test_pre_167_cagg_keeps_the_generic_budget`
      — the two named in this task.
    - `test_policy_advances_head.py::TestPolicyAdvancesHeadUnaided::test_closed_bucket_is_materialized_by_the_policy_alone`
      and `::test_open_bucket_is_never_materialized_while_open` — the two
      named in this task; the test cluster runs no TimescaleDB job scheduler,
      so these fail there deterministically.
    - `test_cli_lists.py::test_lists_ls_includes_priority1` and
      `::test_lists_show_priority1_emits_ten_symbols` — the known priority1
      list mismatch, a standing integration flake not named in this task.
      Recorded here so a later section does not mistake it for a regression.
  - **SC3 baseline bodies:** captured from `mt serve --host 127.0.0.1 --port
    8189` against the production database, read-only, before any code change.
    Saved outside the repo with their request URLs in `URLS.txt` alongside:
    - `/api/v1/health` → `{"status":"ok","db":"ok","coverage":"ok"}`
    - `/api/v1/status?symbol=AAPL&granularity=minute` → `scope: "symbol"`,
      `count: 1`, one row plus `summary` and `coverage` blocks.
    - Note: the status filter parameter is `symbol` (singular). An initial
      capture used `symbols=`, which FastAPI ignores, returning the
      unfiltered 24,349-row body; the filtered request above is the recorded
      baseline.

- [x] **Task 1.2: Extract `gather_db_facts` from `gather`** (effort: 2)
  - [x] In `cli/commands/overview.py`, split `gather` (line ~173) at the
        credit boundary. Everything from the `OverviewFacts` construction
        through `facts.sources = read_source_freshness(conn)` becomes
        `gather_db_facts(conn, settings, *, now=None, hostname=None) ->
        OverviewFacts`.
  - [x] `gather` keeps its **exact current signature**, including
        `fetch_credits=fetch_credit_usage`, and becomes a call to
        `gather_db_facts` followed by the existing guarded credit block,
        unchanged. Every current caller — the command, its tests, the load
        tier — must keep working untouched.
  - [x] Carry the existing docstring rationale for reading
        `settings.minute_firing_days` as a plain attribute into
        `gather_db_facts` (D5): no `getattr` default, because `weekdays=None`
        means *daily* to `FiringSchedule` and a default would render a
        plausible wrong cadence instead of failing loudly.
  - [x] Add `gather_db_facts` to `__all__`.
  - [x] Success: `test/unit/cli/commands/test_overview.py` passes
        **unmodified**. If a 922 test needs editing, the split changed
        behavior — stop and fix the split instead.

- [x] **Task 1.3: Test the split** (effort: 1)
  - [x] Add to `test/unit/cli/commands/test_overview.py`: `gather_db_facts`
        populates `open_runs`, `latest_ended` and `sources`, and leaves
        `credits` and `credits_error` at their defaults (it must never touch
        the credit path).
  - [x] Assert `gather` still returns the credit fields, using the existing
        `fetch_credits` injection point — proof the split did not cost the CLI
        anything.
  - [x] Success: both new tests and the whole 922 overview suite pass.
        Commit (section checkpoint).

---

## Section 2: Response models

Design *API Specification*, *D2*, *D4*, *SC5*, *SC8*. Pure translation from
922's frozen dataclasses to Pydantic. No I/O in this section.

- [x] **Task 2.1: The overview response models** (effort: 3)
  - [x] New module `api_server/models/operations.py`. Models mirroring the
        design's *API Specification*: `RunningRecord`, `LastRunRecord`,
        `PassLineRecord`, `SourceRecord`, `HealthBlock`, `UniverseBlock`,
        `OverviewResponse`.
  - [x] `RunningRecord` carries `phase`, `done`, `total`, `since`,
        `progress_at`, `hostname`, `pid` — and **no `abandoned`** (D4). The
        omission is deliberate; put the reason in the model docstring citing
        D4, so a later reader does not "restore" it.
  - [x] `pass` and `outcome` are typed as `PassKind` and `PassRunOutcome`
        imported from `data/acquisition/pass_runs.py`, never as `str` and
        never as re-spelled literals (SC8).
  - [x] `last_run` is `None` where 922 renders `NEVER_RUN`;
        `universe.summary` is `None` where it renders `NO_ACCOUNTING`. The
        API serves nulls and leaves prose to the client — do not import the
        sentinel strings.
  - [x] Success: module imports nothing from `routes/`; `ruff` and `mypy`
        clean.

- [x] **Task 2.2: `OverviewResponse.from_overview`** (effort: 2)
  - [x] A classmethod translating a 922 `Overview` into the response model.
        Walk the dataclass fields where practical rather than restating them
        (the pattern `CandleRecord.from_row` used against `CANDLE_COLUMNS` in
        188).
  - [x] `running` is a list — two live runs of one kind is a real state 922
        already renders and tests. Do not collapse it to an optional single.
  - [x] Success: `mypy` clean; no field of `Overview` is silently dropped
        except `credits`/`credits_text` (D2) and `abandoned` (D4).

- [x] **Task 2.3: The credits response model** (effort: 1)
  - [x] `CreditsResponse` in the same module: `credits: CreditsRecord | None`
        and `error: str | None`. `CreditsRecord` carries `used`,
        `daily_limit`, `extra`, `remaining` — matching `CreditUsage`,
        whose `remaining` is a computed property, not a stored field.
  - [x] Success: `mypy` clean.

- [x] **Task 2.4: Unit tests for the models** (effort: 2)
  - [x] New `test/unit/api_server/test_operations_models.py`. Build an
        `Overview` by hand (no DB, no network — 922 made `build_overview`
        pure precisely so this is possible) and assert every field
        translates.
  - [x] A pass with two running rows serializes as a two-element list.
  - [x] A pass with no completed run serializes `last_run: null`, not a
        sentinel string; likewise `universe.summary` with no accounting run.
  - [x] `"abandoned"` appears nowhere in `model_dump_json()` output (SC5).
  - [x] `"credits"` appears nowhere in `OverviewResponse` output (SC7).
  - [x] Enum parity: iterate `PassKind` and `PassRunOutcome` and assert each
        member round-trips — a test that **iterates the enums** rather than
        listing tokens, so adding a member cannot pass silently (SC8).
  - [x] `CreditsResponse` covers all three shapes: usage present; no key;
        fetch failure.
  - [x] Success: all tests pass in the unit tier. Commit (section checkpoint).

---

## Section 3: The two routes

Design *D1*, *D2*, *D4*, *D6*, *D8*, *SC1*, *SC4*, *SC6*. Thin routes over
Section 1's reader and Section 2's models (188 D9).

- [x] **Task 3.1: `GET /api/v1/overview`** (effort: 3)
  - [x] New module `api_server/routes/operations.py`. Async handler,
        `Depends(get_db)`, dispatching `gather_db_facts` through
        `loop.run_in_executor(None, …)` as every other DB route does.
  - [x] Call `build_overview(facts, pid_alive=lambda _pid: True)` (D4). Put
        the reason in a comment citing D4: a pid from another host says
        nothing about a process here, so the API must not judge abandonment.
        A bare `pid_is_alive` here is a bug.
  - [x] Declare `responses=GATEWAY_TIMEOUT_RESPONSE` (D8 — this route does
        query the DB).
  - [x] Return `OverviewResponse` directly as the annotated return type —
        **not** `response_class=Response`, which suppresses the 200 schema
        (the trap 188 recorded and left to slice 190). SC9 depends on this.
  - [x] Read `settings.minute_firing_days` as a plain attribute (D5).
  - [x] No query parameters, no path parameters, therefore no 404 and no 422
        (D6).
  - [x] Success: `mypy` clean; the module contains no SQL and no derivation.

- [x] **Task 3.2: `GET /api/v1/credits`** (effort: 2)
  - [x] Same module. Dispatches `fetch_credit_usage(settings.eodhd_api_key)`
        through the executor.
  - [x] **No `GATEWAY_TIMEOUT_RESPONSE`** (D8): the route issues no
        statement and takes no parameters, so a 504 and its "narrow the
        requested range" remedy would both be false in the published schema.
        Comment the omission citing D8 so it is not "fixed" later.
  - [x] Unset key → 200 with `credits: null` and `error` = `CREDITS_NO_KEY`,
        imported from `cli/overview_types.py`, not re-spelled.
  - [x] Fetch failure → 200 with `credits: null` and `error` =
        `credits_unavailable(redact_token(str(exc)))`. Both helpers are
        imported; neither message is re-spelled. Log at WARNING with
        `exc_info=True`, matching 922's rationale: the catch is broad by
        contract, so a one-line warning would hide a programming error behind
        a plausible "unavailable" line.
  - [x] The `except` is deliberately broad and **must** carry the comment
        explaining why swallowing is correct here (a provider outage is a
        reported condition, not a server fault) — the project's exception rule
        requires it.
  - [x] Success: no token substring can reach the response body or the log.

- [x] **Task 3.3: Register the router** (effort: 1)
  - [x] `app.include_router(operations_router)` in `app.py`, alongside the
        existing seven.
  - [x] Success: `GET /api/v1/overview` and `GET /api/v1/credits` appear in
        `/openapi.json` at runtime.

- [x] **Task 3.4: Route unit tests** (effort: 2)
  - [x] New `test/unit/api_server/test_operations.py`, `TestClient` with
        `gather_db_facts` patched — no DB.
  - [x] Assert the handler passes a `pid_alive` that returns `True` for any
        pid: patch `build_overview` and inspect the keyword it received. A
        test asserting only "abandoned is absent from the body" would pass
        even with a real `pid_is_alive`, so this assertion is what actually
        pins D4.
  - [x] `/api/v1/credits` in all three shapes, with `fetch_credit_usage`
        patched: success, raising, and no key configured. All three are 200
        (SC6).
  - [x] A raised exception carrying an `api_token=` query string produces a
        body with no token — redaction proven, not assumed (SC6).
  - [x] **Pin SC2 with a test, not a grep.** Assert `routes/operations.py`
        imports `build_overview`, and that its module source contains no
        reference to `next_firing_at`, `schedule_for`, `pid_is_alive` or
        `PassRunRepository` — the four symbols a second derivation would
        have to reach for. Read the module source via
        `inspect.getsource(...)`, so the assertion travels with the code
        rather than depending on a command someone remembers to run.
  - [x] Success: all pass in the unit tier. Commit (section checkpoint).

- [x] **Task 3.5: Integration tests against a real DB** (effort: 3)
  - [x] New `test/integration/test_operations_serving.py` built on the
        `migrated_db` fixture (`test/conftest.py`), which applies the full
        `MINUTE_MIGRATIONS` chain to a UUID-named throwaway database.
        **Never** the production DB URL.
  - [x] Both routes return 200 against `migrated_db` (055 included).
  - [x] Insert an open `pass_runs` row: it appears in `running` with its
        phase, progress and hostname. Close it: it appears in `last_run` with
        its outcome and exit code, and leaves `running` empty.
  - [x] **The pre-055 degradation (SC4):** against a database *without*
        `pass_runs`, the route returns 200 with every pass empty and the
        `sources` block still populated — not 500. This is the single most
        important test in the section; 922's `_read` already degrades this
        way and this proves the API inherited it.
    - [x] Build the fixture by applying `MINUTE_MIGRATIONS` up to but not
          including 055, rather than by dropping the table afterwards: a
          drop leaves the chain's own bookkeeping claiming 055 was applied,
          which is not the state being simulated. If slicing the chain
          proves impractical, a `DROP TABLE pass_runs` is acceptable **only**
          against the `ephemeral_db`-derived database the fixture itself
          created — never against any other target — and record which
          approach was used and why.
        Recorded: the `DROP TABLE pass_runs CASCADE` fallback was used, against
        the UUID-named `ephemeral_db` the fixture itself created. Slicing the
        chain is not possible here — `MINUTE_MIGRATIONS` is ordered by
        dependency rather than number, `055_create_pass_runs` sits at index 25,
        and `056_data_status_open_gaps_and_walk_anchor` defines `data_status`
        with a `SELECT ... FROM pass_runs`, so omitting 055 fails the chain with
        `UndefinedTable`. Verified by building the omit-the-migration fixture
        first and observing that error.
    - [x] The source tables must still exist, or the test cannot tell "no
          pass rows" from "nothing at all".
  - [x] **The split guard (D3):** on one connection, `gather_db_facts` and
        `gather` return equal `OverviewFacts` for the DB-derived fields. This
        fails the moment a future change adds a DB read to `gather` alone.
  - [x] Success: all pass; no failure outside the Task 1.1 baseline list.
        Commit (section checkpoint).

---

## Section 4: The load tier

Design *D8*, *Testing Strategy*, *SC10*, *SC12*. Two bounds: one latency, one
contention. **Measure first, then write the bound** — never invent a number.

- [x] **Task 4.1: Latency bound on `/api/v1/overview`** (effort: 3)
  - [x] New `test/load/test_189_operations_nfr.py`, built on
        `create_app(db_url=…)` as `test_187_api_nfr.py` does. This tier never
        reads the production DB URL.
  - [x] Measure the endpoint against a seeded database, record the observed
        figure, **then** write a bound with headroom above it. Put the
        measured number and the bound in the design's *Testing Strategy* under
        SC10 — the design currently says the number goes in after it is
        observed.
  - [x] The reads are `2 × |PassKind|` indexed lookups plus four `MAX()`
        probes. Assert no unbounded scan on the source probes by plan
        inspection, as 187 D10 did.
  - [x] Success: the bound passes and the measurement is written down.

- [x] **Task 4.2: Executor-contention bound** (effort: 3)
  - [x] The assertion D8 requires: with `fetch_credit_usage` **stubbed to
        block** for the full `EODHD_ACCOUNT_TIMEOUT_SECONDS` (5.0s), issue
        concurrent `/api/v1/credits` requests and assert `/api/v1/overview`
        latency stays within its Task 4.1 bound.
  - [x] The stub is required, not a convenience: the assertion is about this
        server's shared thread budget. A live call would make the test depend
        on EODHD's response time and spend real quota.
  - [x] Both routes dispatch through `run_in_executor(None, …)`, so they
        share one default pool of `min(32, cpu + 4)` threads. Size the
        concurrency high enough to actually contend for it — a test with
        fewer concurrent calls than the pool has threads proves nothing.
        State the chosen concurrency and why in a comment.
  - [x] **If the bound cannot be met:** implement a small dedicated executor
        for the credit call (so a third party's slowness cannot reach the DB
        routes), and record in the design the measurement that forced it. Do
        not widen the shared pool, and do not relax the bound.
  - [x] Success: the bound passes, or the dedicated executor exists and the
        design records why (SC12).

        Recorded: the bound FAILED on first measurement. With 36 concurrent
        stubbed credit calls against the default pool of 32 threads, the first
        `/api/v1/overview` request took 4.564 s. A dedicated two-thread
        executor for the credit fetch was implemented, as D8 prescribes; the
        shared pool was not widened and the bound was not relaxed. The
        assertion was also moved from the median to `max(samples)` — written
        against the median it passed while that 4.564 s sample was present.
        Full measurement in the slice design's Testing Strategy.

- [x] **Task 4.3: Prove the guards fail for the right reason** (effort: 2)
  - [x] Deliberately break each of the two bounds and confirm the failure
        message identifies the real cause, then restore. A load test that
        cannot fail is not a test — 188 did this for four guards.
  - [x] Success: both breakages observed and described in the commit message.
        Commit (section checkpoint).

---

## Section 5: Documentation and the OpenAPI artifact

Design *D2*, *D8*, *SC3*, *SC7*, *SC9*, *SC11*. Last, so the artifact is
written once against the final route set.

- [x] **Task 5.1: README and app description** (effort: 1)
  - [x] Add both routes to the README endpoint list.
  - [x] State where credits live and that `/api/v1/overview` is **not** a
        strict superset of `mt data overview --json` (D2/SC7) — the CLI
        payload's `credits`/`credits_text` are at `/api/v1/credits`, and
        `abandoned` is a local-host judgment available only from the CLI
        (D4).
  - [x] Update `app.py`'s `description` to name operations coverage. Slice
        190 replaces it with the real reference; this only stops it being
        wrong by omission.
  - [x] Success: no statement in the README contradicts the design.

- [x] **Task 5.2: Regenerate `openapi.json`** (effort: 1)
  - [x] Run `scripts/dump_openapi.py`; commit the regenerated
        `docs/api/openapi.json`.
  - [x] Success: `test/unit/api_server/test_openapi_artifact.py` passes
        (the committed artifact matches the app).

- [x] **Task 5.3: Pin the schema properties** (effort: 2)
  - [x] Extend `test_openapi_artifact.py` (or add a sibling): the committed
        artifact declares a **200 schema** for both new routes (SC9); declares
        `504` on `/api/v1/overview` and **not** on `/api/v1/credits` (SC11);
        and its `pass`/`outcome` token sets equal `PassKind` and
        `PassRunOutcome` exactly, read from the enums (SC8).
  - [x] Assert every pre-existing path is byte-identical to the previous
        committed artifact, with exactly two additions and no removals (SC3
        schema level) — the property 188 established, checked here rather
        than trusted.
  - [x] **SC3 response level:** re-issue the two requests captured in Task
        1.1 and diff the bodies. The *shape* must be identical — same keys,
        same nesting, same types. Values that legitimately move between the
        two captures (coverage freshness verdicts, row counts, any
        timestamp) are not failures; compare structure, and inspect any
        value difference to confirm it reflects data arriving rather than
        this slice.
  - [x] If the shapes differ at all, stop: nothing in this slice touches
        `status.py` or `health.py`, so a shape change means the `gather`
        split reached further than D3 intended.
  - [x] Success: all assertions pass against the committed artifact, and
        both response shapes are unchanged from the Task 1.1 baseline.

- [ ] **Task 5.4: Walk the verification walkthrough** (effort: 3)
  - [ ] Execute steps 1–13 of the design's *Verification Walkthrough* against
        production read-only, with `mt serve` on a local port as 188 did.
        Record the observed output for each step in the design under an
        *Evidence, walked {date}* subsection.
  - [ ] Step 3 (endpoint agrees with the screen) and step 4 (diff against the
        CLI's own JSON) are the two that prove SC1 and SC5/SC7 together. If
        the diff shows anything beyond `now` and passes that advanced between
        the reads, stop — the models have drifted from 922.
  - [ ] If a walkthrough step is wrong about production, correct the design
        rather than silently working around it (188 recorded three such
        corrections).
  - [ ] Success: every step has observed output recorded; every SC1–SC12 has
        named evidence.

- [ ] **Task 5.5: Close the slice** (effort: 1)
  - [ ] Run the unit, integration and load tiers **separately** (a whole
        `test/` collection yields spurious errors). Attribute any failure
        against the Task 1.1 baseline before investigating.
  - [ ] `mypy` over every touched file in **one** invocation including src and
        test paths — narrower runs report false errors.
  - [ ] `ruff format` scoped to touched files, then `git diff main` for
        deletions before committing: scoping is necessary but not sufficient,
        since it rewrites whole files.
  - [ ] Set this file's and the design's frontmatter `status: complete`; tick
        plan entry 9 in `180-slices.data-serving-api.md`.
  - [ ] Success: all tiers green except the known baseline failures; the slice
        is ready for its code review. Commit.

---

## Notes

- **Effort scale** is 1–5 relative, not time.
- **Commit at each section checkpoint**, not per numbered subtask.
- The code review is launched by the Project Manager, not from this session.
  After findings are addressed, merge → tag → push.
- Two findings from the slice review are already resolved in the design (D8);
  no task re-litigates them.
