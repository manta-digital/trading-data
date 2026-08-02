---
docType: tasks
slice: mt-update-self-update-command
project: trading-data
lldReference: project-documents/user/slices/909-slice.mt-update-self-update-command.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [908]
interfaces: []
projectState: >
  908 complete — manta-trading-data published (0.6.1 live on PyPI), tag-gated
  CI publish workflow in place, DISTRIBUTION_NAME constant exists. Slice 909
  design reviewed (PASS, F006 addressed in design).
dateCreated: 20260802
dateUpdated: 20260802
status: not_started
---

# Tasks: `mt update` Self-Update Command

## Context summary

Add a top-level `mt update` that queries PyPI for the latest published
version, compares with `packaging.version`, detects how the tool was
installed, and auto-upgrades only the blessed `uv tool install --upgrade`
path. Ported from the production-proven `cf update` shape. All decisions
referenced below (D1–D9) are in the LLD.

### Non-negotiables from the design

- The update command never imports the DB layer or opens a connection (D6).
  The post-upgrade pending-migration count comes from a subprocess call to
  the **new** binary's `mt data migrate status --json`, best-effort.
- The registry call happens only inside the command body — never at CLI
  startup, never from any other command (D9).
- `--json` is a pure query: no prompt, no subprocess, no migration probe (D7).
- Every external operation is bounded: registry 10 s, upgrade subprocess
  600 s, migration probe 30 s — all constants in `constants.py`, no literals
  at call sites (D2, D5, D6).
- Editable/source installs refuse **before** any network call (D4).
- Version comparison uses `packaging.version` only — no string splitting (D3).

### Branch

Work on `909-slice.mt-update-self-update-command`, created from `main`.

---

## Task 1 — Dependency and constants

- [ ] **1.1 Declare `packaging` as a direct dependency (D3)**
  - [ ] Add `packaging>=24.0` to `[project] dependencies` in `pyproject.toml`.
  - [ ] `uv lock` regenerates cleanly; `uv sync` succeeds.
  - Success: `uv run python -c "import packaging.version"` works and
    `packaging` appears in `[project] dependencies`, not only transitively.
  - Effort: 1

- [ ] **1.2 Add update constants to `constants.py` (D2, D5, D6)**
  - [ ] `PYPI_JSON_URL_TEMPLATE: Final[str] = "https://pypi.org/pypi/{name}/json"`
  - [ ] `REGISTRY_TIMEOUT: Final[float] = 10.0` — seconds; mirrors `cf update`.
  - [ ] `UPGRADE_TIMEOUT: Final[float] = 600.0` — ~80x the measured 7.25 s
        cold-cache clean install (2026-08-02); bounds a hung download/build.
  - [ ] `UPDATE_MIGRATE_PROBE_TIMEOUT: Final[float] = 30.0` — ~4x the worst
        of three measured probe runs (0.58/1.93/6.90 s, prod mid-backfill).
  - [ ] Each carries a docstring citing its measurement/rationale per the
        existing `constants.py` convention.
  - Success: constants importable; no timeout or URL literal appears in
    `update.py` when Task 2 lands.
  - Effort: 1

- [ ] **1.3 Commit checkpoint**
  - [ ] `chore: add packaging dependency and mt update constants`
  - Effort: 1

## Task 2 — Pure helpers in `cli/commands/update.py`

Each helper is a module-level function taking no typer context and doing no
printing (D1). Tests live in `test/unit/cli/commands/test_update.py` and use
`monkeypatch` only — no new test dependencies, no network, no real
subprocesses.

- [ ] **2.1 `InstallMethod` enum and `detect_install_method()` (D4)**
  - [ ] `class InstallMethod(StrEnum)`: `UV_TOOL = "uv-tool"`,
        `PIPX = "pipx"`, `PIP = "pip"`,
        `EDITABLE_OR_SOURCE = "editable-or-source"`. Lives in `update.py`
        (single consuming module; values surface in `--json` output).
  - [ ] Detection order, first match wins:
    1. `EDITABLE_OR_SOURCE` — `importlib.metadata.version(DISTRIBUTION_NAME)`
       raises `PackageNotFoundError`, or the distribution's
       `direct_url.json` has `dir_info.editable: true` (PEP 660).
    2. `UV_TOOL` — `Path(sys.executable).resolve()` contains the adjacent
       path segments `uv/tools`.
    3. `PIPX` — resolved path contains `pipx/venvs`.
    4. `PIP` — everything else.
  - Success: function returns the enum, touches no network, raises nothing.
  - Effort: 2

- [ ] **2.2 Tests for `detect_install_method`**
  - [ ] One test per branch: metadata missing → `EDITABLE_OR_SOURCE`;
        editable `direct_url.json` → `EDITABLE_OR_SOURCE`; uv-tools path →
        `UV_TOOL`; pipx path → `PIPX`; plain venv path → `PIP`.
  - [ ] Paths injected by monkeypatching `sys.executable` and the metadata
        lookup — no real environments constructed.
  - Success: all branches covered and green.
  - Effort: 2

- [ ] **2.3 `fetch_latest_version() -> str | None` (D2)**
  - [ ] `httpx.get(PYPI_JSON_URL_TEMPLATE.format(name=DISTRIBUTION_NAME),
        timeout=REGISTRY_TIMEOUT)`, read `info.version`.
  - [ ] Returns `None` on any failure. Enumerated excepts only —
        `httpx.HTTPError`, `ValueError`, `KeyError`, `TypeError` — with a
        comment citing D2's "return nothing rather than raise" mandate. No
        bare `except Exception`.
  - Success: returns a version string on a well-formed 200; `None` on
    timeout, non-200, malformed JSON, missing/mistyped key.
  - Effort: 1

- [ ] **2.4 Tests for `fetch_latest_version`**
  - [ ] Success case (mocked 200 with `info.version`).
  - [ ] Failure cases, one test each: connect timeout (`httpx.TimeoutException`),
        non-200 status, invalid JSON body, missing `info.version`,
        `info.version` not a string. All must return `None`, never raise.
  - Success: all six green with httpx mocked via `monkeypatch`.
  - Effort: 2

- [ ] **2.5 `upgrade_command(method) -> list[str] | None` (D5)**
  - [ ] `UV_TOOL` → `["uv", "tool", "install", "--upgrade", DISTRIBUTION_NAME]`;
        all other methods → `None`. Fixed argv, no shell, no interpolation of
        registry data (Special Considerations).
  - [ ] The pipx/pip guidance strings the command prints live beside this
        mapping — one definition site for all method-dispatched values.
  - Success: mapping matches the D5 table exactly.
  - Effort: 1

- [ ] **2.6 Tests for `upgrade_command`**
  - [ ] One assertion per `InstallMethod` value; `UV_TOOL` argv asserted
        element-by-element against `DISTRIBUTION_NAME`.
  - Effort: 1

- [ ] **2.7 `report_pending_migrations() -> int | None` (D6)**
  - [ ] Runs `mt data migrate status --json` via `subprocess.run` with
        `timeout=UPDATE_MIGRATE_PROBE_TIMEOUT`, captured output.
  - [ ] Binary resolution: prefer the `mt` entry point adjacent to
        `sys.executable`'s environment; fall back to bare `"mt"` from PATH.
  - [ ] Returns `len(pending)` on a clean parse with `"connected": true`;
        `None` on non-zero exit, `TimeoutExpired`, unparseable output, or
        `"connected": false`. Never raises; never imports the DB layer.
  - Success: no psycopg/DB import appears in `update.py` (grep-verifiable).
  - Effort: 2

- [ ] **2.8 Tests for `report_pending_migrations`**
  - [ ] Success: mocked subprocess emitting valid JSON with 2 pending → 2.
  - [ ] Degradations, one test each: non-zero exit, `TimeoutExpired`,
        garbage stdout, `"connected": false` → all `None`.
  - Success: all green with subprocess mocked.
  - Effort: 2

- [ ] **2.9 Commit checkpoint**
  - [ ] `feat: add mt update pure helpers (fetch, detect, upgrade-map, probe)`
  - Effort: 1

## Task 3 — The `mt update` command and registration

- [ ] **3.1 Implement the typer command (D1, flow steps 1–8)**
  - [ ] Options: `--json` (D7), `--yes`. Output via `print_result` /
        `print_error` (`cli/output.py`).
  - [ ] Order per the Data Flow diagram: detect → (refuse editable/source,
        exit 0, no network) → fetch (None → message, exit 1) → compare with
        `packaging.version.Version` → `--json` short-circuit → up-to-date
        (exit 0) → confirm gate (`--yes` / `typer.confirm` on TTY / non-TTY
        report-only exit 0) → dispatch on `upgrade_command` result
        (run UV_TOOL argv with `timeout=UPGRADE_TIMEOUT`, stdio inherited;
        `None` → print guidance, exit 0) → on success,
        `report_pending_migrations` (count line or generic pointer).
  - [ ] `shutil.which("uv")` miss → degrade to printing the command (D5).
  - [ ] `TimeoutExpired` from the upgrade → report, name the manual command,
        exit 1. Non-zero upgrade exit → exit 1.
  - [ ] Exit codes exactly per the D8 table.
  - Success: every branch in the Data Flow diagram is reachable in code and
    none is silently merged with another.
  - Effort: 3

- [ ] **3.2 Register in `cli/app.py` (D9)**
  - [ ] `app.command(name="update")(update)` alongside `serve`.
  - [ ] Module import stays cheap: no network, DB, or heavy imports at
        module level of `update.py`.
  - Success: `mt update` appears in `mt --help`; `mt --help` runs offline
    with no registry traffic (D9 / success criterion 9).
  - Effort: 1

- [ ] **3.3 Behavior-matrix tests via `typer.testing.CliRunner`**

  With helpers already unit-tested, these mock at the helper boundary and
  assert orchestration: output, exit code, and which side effects occurred.
  - [ ] Up-to-date → "up to date" message, exit 0, no prompt, no subprocess.
  - [ ] Update available + TTY confirm accepted → upgrade argv invoked,
        success message, probe invoked.
  - [ ] Update available + TTY declined → exit 0, no subprocess.
  - [ ] `--yes` → no prompt, upgrade invoked.
  - [ ] Non-TTY without `--yes` → report + "run with --yes", exit 0, no
        subprocess.
  - [ ] `--json` purity (success criterion 4): documented four-key object,
        exit 0, and the mocked subprocess layer records **zero** calls even
        with `--yes` also passed; editable/source variant emits
        `latest: null` with no network call; registry-failure variant emits
        `error` key and exits 1.
  - [ ] Editable/source (human mode) → developer guidance, exit 0, no
        network call recorded.
  - [ ] PIPX and PIP → correct printed command, exit 0, no subprocess.
  - [ ] `uv` missing from PATH → printed command, exit 0.
  - [ ] Registry unreachable (human mode) → one-line message, exit 1, no
        traceback in output.
  - [ ] Upgrade subprocess non-zero → exit 1.
  - [ ] Upgrade `TimeoutExpired` → timeout reported, manual command named,
        exit 1.
  - [ ] Probe degradation → generic pointer line printed, update still
        exits 0 (success criterion 8).
  - Success: full matrix green.
  - Effort: 3

- [ ] **3.4 Static checks and commit checkpoint**
  - [ ] `uv run --extra dev mypy` and ruff clean on all touched files.
  - [ ] `feat: add mt update self-update command`
  - Effort: 1

## Task 4 — Documentation

- [ ] **4.1 README "Updating" section**
  - [ ] After the install section: `mt update` as the primary path, the
        manual `uv tool install --upgrade manta-trading-data` equivalent,
        and one line on the developer-install refusal (`git pull && uv sync`).
  - [ ] CHANGELOG entry under the next version.
  - [ ] Commit: `docs: add updating instructions for mt update`
  - Effort: 1

## Task 5 — Release and end-to-end verification

- [ ] **5.1 Merge and release**
  - [ ] Merge the slice branch into `main` (`--no-ff`) with tests green.
  - [ ] Bump version to `0.7.0` in `pyproject.toml`; `uv lock`; finalize
        CHANGELOG; commit `package: bump version to 0.7.0`.
  - [ ] Tag `v0.7.0`, push; confirm the CI publish workflow succeeds and
        0.7.0 is live on PyPI; `gh release create v0.7.0`.
  - Success: `https://pypi.org/pypi/manta-trading-data/json` reports
    `info.version == "0.7.0"`.
  - Effort: 2

- [ ] **5.2 Real end-to-end upgrade (walkthrough step 3; closes 908
      deferred criterion 4)**
  - [ ] In a clean environment (isolated `UV_TOOL_DIR`/`UV_CACHE_DIR`):
        `uv tool install manta-trading-data==0.6.1`, verify
        `mt --version` → 0.6.1.
  - [ ] `mt update --json` → `current: 0.6.1`, `latest: 0.7.0`,
        `update_available: true`, `install_method: "uv-tool"`.
  - [ ] `mt update --yes` → upgrade runs; `mt --version` → 0.7.0.
  - [ ] Record the observed pinned-receipt behavior (does the unpinned
        install replace the `==0.6.1` pin?) in the walkthrough — the design
        flags this as confirm-at-release-time.
  - Success: real 0.6.1 → 0.7.0 upgrade demonstrated and captured in the
    walkthrough.
  - Effort: 2

- [ ] **5.3 Close out**
  - [ ] Refine the LLD Verification Walkthrough with actual observed output;
        set slice and tasks frontmatter status; check the plan entry
        (delegate checklist updates to task-checker).
  - [ ] Commit: `docs: close out slice 909`
  - Effort: 1
