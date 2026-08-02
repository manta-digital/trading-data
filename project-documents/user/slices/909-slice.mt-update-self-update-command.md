---
docType: slice-design
slice: mt-update-self-update-command
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [908]
interfaces: []
dateCreated: 20260801
dateUpdated: 20260801
status: not_started
---

# Slice Design: `mt update` Self-Update Command

## Overview

Slice 908 made releases installable (`uv tool install manta-trading-data`), but
an installed user still has no in-tool way to discover or apply one:
`mt --version` reads `importlib.metadata` and never consults a registry. This
slice adds `mt update` — query PyPI for the latest published version, compare
against the running version, detect how the tool was installed, and shell out
to the matching upgrade command. The shape is ported from the proven
`cf update` (~187 lines, `context-forge/packages/cli/src/commands/update.ts`).

## Value

- **Installed users** learn about and apply updates without leaving the tool
  or knowing packaging mechanics.
- **Closes the 908 loose end**: shipping this slice produces the next published
  release, which supplies the second version needed for 908's deferred
  criterion 4 (a real `--upgrade` between two published versions).
- **Operators** get a post-upgrade pointer to pending migrations, connecting
  code upgrades to `mt data migrate status` without coupling the update path
  to the database.

## Technical Scope

**In scope:**

1. New top-level command `mt update` in `src/manta_trading/cli/commands/update.py`,
   registered in `cli/app.py` alongside `serve`.
2. Registry query: `GET https://pypi.org/pypi/manta-trading-data/json` →
   `info.version`, bounded timeout, returns `None` on *any* failure.
3. Version comparison via `packaging.version` (new explicit dependency).
4. Install-method detection (uv tool / pipx / pip / editable-or-source) and
   auto-upgrade of the blessed `uv tool install --upgrade` path only.
5. `--json` (pure query, no side effects), `--yes` (non-interactive), and
   non-TTY-without-`--yes` report-only behavior.
6. Best-effort post-upgrade pending-migration report via subprocess to the
   **new** binary — never a direct DB connection.
7. Unit tests for every branch with network and subprocess mocked; a real
   end-to-end upgrade between two published versions at release time.

**Explicitly excluded:**

- Any database connection from the update command itself (no psycopg import).
- Any registry call at CLI startup or from any other command — the network
  request happens only when `mt update` is invoked.
- Applying migrations, or any write to any store.
- Update channels, pre-release opt-in, version pinning, downgrade support.

## Dependencies

### Prerequisites

- **Slice 908 (complete):** `manta-trading-data` is published on PyPI;
  `DISTRIBUTION_NAME` exists in `constants.py`; the version callback pattern
  (metadata lookup with `PackageNotFoundError` → `"dev"`) is in place.
- **`packaging`** — added to `[project] dependencies` in `pyproject.toml` as an
  explicit dependency (it is currently only transitive). Version comparison
  must not be hand-rolled string splitting.
- `httpx` — already a direct dependency; used for the registry call.

### Interfaces Required

- `constants.DISTRIBUTION_NAME` — the distribution to query and upgrade.
- `mt data migrate status --json` — consumed *as a subprocess* post-upgrade;
  its JSON contract (`{"connected": ..., "applied": [...], "pending": [...]}`)
  is the parse target.
- `cli/output.py` (`print_result`, `print_error`) for output discipline.

## Architecture

### Component Structure

One new module, `src/manta_trading/cli/commands/update.py`, containing:

- **Pure helpers** (module-level functions, individually unit-testable):
  - `fetch_latest_version() -> str | None` — registry query.
  - `detect_install_method() -> InstallMethod` — inspects the running
    interpreter/distribution.
  - `upgrade_command(method: InstallMethod) -> list[str] | None` — maps a
    method to its argv (only `UV_TOOL` returns a runnable command; others
    return `None` and the command prints guidance instead).
  - `report_pending_migrations() -> int | None` — post-upgrade subprocess
    probe, best-effort.
- **The typer command** `update(...)` — orchestrates the above and owns all
  I/O (prompting, printing, exit codes).

`cli/app.py` gains one import and one `app.command(name="update")(update)`
registration. Nothing else in the codebase changes behavior.

### Data Flow

```
mt update
  │
  ├─ 1. detect_install_method()          (local: sys.executable + importlib.metadata)
  │      EDITABLE_OR_SOURCE ──► print "developer install — git pull && uv sync"; exit 0
  │                              (no network call is ever made on a dev machine)
  │
  ├─ 2. fetch_latest_version()           (network: pypi.org, bounded timeout)
  │      None ──► print "could not reach PyPI"; exit 1  (clean message, no traceback)
  │
  ├─ 3. compare(current, latest)         (packaging.version)
  │
  ├─ 4. --json? ──► emit JSON, exit 0    (PURE QUERY: no prompt, no subprocess, no DB)
  │
  ├─ 5. up to date ──► report; exit 0
  │
  ├─ 6. update available:
  │      --yes            ──► proceed
  │      TTY              ──► typer.confirm(); declined → exit 0
  │      non-TTY, no --yes ──► report "run with --yes"; exit 0 (no action taken)
  │
  ├─ 7. method == UV_TOOL ──► subprocess: uv tool install --upgrade manta-trading-data
  │      method == PIPX/PIP ──► print the correct command for the user; exit 0
  │
  └─ 8. on successful upgrade: report_pending_migrations()
         subprocess: mt data migrate status --json  (the NEW binary, bounded timeout)
         count parsed → "N migration(s) pending — run `mt data migrate status`"
         any failure → generic pointer line; NEVER fails the update
```

## Technical Decisions

### D1 — One module, pure helpers + one typer command

Mirrors `cf update`'s single-file shape and the existing `commands/` layout.
The helpers take no typer context and perform no printing, so unit tests
exercise them directly; the command function owns all I/O. Estimated well
under the 300-line file guideline.

### D2 — Registry endpoint and failure contract

`GET https://pypi.org/pypi/{DISTRIBUTION_NAME}/json`, read `info.version`.
Constants (in `constants.py`, per the single-definition rule):

- `PYPI_JSON_URL_TEMPLATE: Final[str] = "https://pypi.org/pypi/{name}/json"`
- `REGISTRY_TIMEOUT: Final[float] = 10.0` — seconds; mirrors `cf update`'s
  10 s budget.

`fetch_latest_version` returns `None` on **any** failure — connect/read
timeout, non-200, JSON decode error, missing/mistyped `info.version` — so a
registry outage can never produce a traceback. This is the one place in the
codebase where broad exception swallowing is correct by specification (the
slice plan mandates "returning nothing rather than raising on any failure");
the except clauses are still enumerated (`httpx.HTTPError`, `ValueError`,
`KeyError`, `TypeError`) with a comment citing this decision, not a bare
`except Exception`.

The simpler `https://pypi.org/simple/` index and the `/latest` shortcut used
by npm have no PyPI equivalent; the JSON API is the documented, stable
endpoint and returns the latest non-yanked version directly in `info.version`
(yanked releases — like the broken 0.6.0 — are excluded, which is exactly the
behavior we want).

### D3 — `packaging.version`, declared explicitly

`Version(latest) > Version(current)` — PEP 440-aware, handles `0.6.1` vs
`0.10.0` correctly where string comparison fails. `packaging` is added to
`[project] dependencies` (floor `>=24.0`), not relied on transitively: a
transitive dependency can vanish in a resolver change, and this is the only
correctness-critical use.

The running version comes from the same
`importlib.metadata.version(DISTRIBUTION_NAME)` lookup the `--version`
callback uses. `PackageNotFoundError` here means a source checkout without an
installed distribution — that case is already caught earlier by D4's
detection, so by the time comparison runs, a real version string exists.

### D4 — Install-method detection: enum, resolved from the interpreter path

```python
class InstallMethod(StrEnum):
    UV_TOOL = "uv-tool"
    PIPX = "pipx"
    PIP = "pip"
    EDITABLE_OR_SOURCE = "editable-or-source"
```

Detection order (first match wins), inspecting `Path(sys.executable).resolve()`
and distribution metadata:

1. **EDITABLE_OR_SOURCE** — `importlib.metadata.version(DISTRIBUTION_NAME)`
   raises `PackageNotFoundError` (running from a checkout), **or** the
   distribution's `direct_url.json` has `dir_info.editable: true` (PEP 660
   editable install, i.e. `uv sync` / `pip install -e`). This is the state
   every developer machine is in today; the command refuses before any
   network call and prints the developer path (`git pull && uv sync`).
2. **UV_TOOL** — the resolved interpreter path contains the path segments
   `uv/tools/` (uv's tool environments live at
   `~/.local/share/uv/tools/<dist>/` on Linux/macOS; the `uv/tools` segment
   pair is stable across platforms and honored under `UV_TOOL_DIR`-style
   relocations in practice).
3. **PIPX** — resolved path contains `pipx/venvs`.
4. **PIP** — everything else (plain venv or `pip install --user`).

Path-segment matching is a heuristic, but it is the same heuristic `cf update`
has run in production, and the failure mode is benign by construction: a
misclassification can only change *which command we print or run*, and the
only method that triggers an automatic subprocess is UV_TOOL — whose command
(`uv tool install --upgrade`) is a no-op-safe, idempotent operation even if
run inside some other environment that happens to have `uv` on PATH. All four
values and their mapped commands live in one place (`upgrade_command`).

### D5 — Auto-upgrade only the blessed path

`upgrade_command` returns:

| Method | Returns | Command behavior |
|---|---|---|
| `UV_TOOL` | `["uv", "tool", "install", "--upgrade", DISTRIBUTION_NAME]` | run via `subprocess.run` (no shell), stdio inherited |
| `PIPX` | `None` | print `pipx upgrade manta-trading-data`; exit 0 |
| `PIP` | `None` | print `pip install --upgrade manta-trading-data` (with a note to run it in the owning environment); exit 0 |
| `EDITABLE_OR_SOURCE` | `None` | refuse before network (step 1 of the flow) |

Before running the UV_TOOL command, `shutil.which("uv")` is checked; if `uv`
is somehow absent from PATH, degrade to printing the command (same treatment
as PIPX/PIP) rather than failing. A non-zero exit from the upgrade subprocess
is reported as a failure with the subprocess's own output visible (stdio is
inherited) and exits non-zero.

### D6 — Post-upgrade migration report: subprocess to the new binary, never a DB connection

The slice plan requires both "never touch the database" and "report the
pending-migration count". Resolved: after a successful upgrade, run

```
mt data migrate status --json
```

as a subprocess (bounded timeout, `REGISTRY_TIMEOUT` reused is *not*
appropriate — DB probes can be slower; a dedicated
`UPDATE_MIGRATE_PROBE_TIMEOUT: Final[float] = 30.0` constant) and parse
`len(pending)` from its JSON output.

This is deliberately a subprocess rather than an in-process
`list_migration_state()` call, for two reasons:

1. **Correctness:** pending migrations are defined by the *new* code's
   migration set. The still-running old process only knows the old set — an
   in-process check would systematically miss exactly the migrations the
   upgrade just shipped.
2. **The constraint itself:** the update command never imports the DB layer
   and never opens a connection; the subprocess boundary makes "never touch
   the database" structurally true rather than a discipline.

Best-effort contract: non-zero exit, timeout, unparseable output, or
`"connected": false` all degrade to the generic pointer line
(`Run 'mt data migrate status' to check for pending migrations.`) — no
failure of this probe may change the update's exit code. The binary invoked
is resolved as the `mt` entry point adjacent to the (just-upgraded)
interpreter environment rather than bare `mt` from PATH where practical;
if resolution fails, bare `mt` is an acceptable fallback for a best-effort
informational probe.

### D7 — `--json` is a pure query

`mt update --json` emits (via `print_result(..., json_mode=True)`):

```json
{
  "current": "0.6.1",
  "latest": "0.7.0",
  "update_available": true,
  "install_method": "uv-tool"
}
```

and exits 0 — no prompt, no subprocess, no migration probe, regardless of
`--yes`. On an editable/source install it emits
`{"current": ..., "latest": null, "update_available": false, "install_method": "editable-or-source"}`
without a network call. On registry failure it emits
`{"current": ..., "latest": null, "update_available": false, "error": "registry unreachable"}`
and exits 1. Tests assert the no-side-effects property directly (the mocked
subprocess layer records zero calls).

### D8 — Exit codes

| Outcome | Exit |
|---|---|
| Up to date / update applied successfully | 0 |
| `--json` query answered | 0 |
| Editable/source refusal, pipx/pip guidance printed, declined prompt, non-TTY report | 0 (informational — expected states, not errors) |
| Registry unreachable | 1 (query could not be answered; clean message, no traceback) |
| Upgrade subprocess failed | 1 |

### D9 — No startup cost

`update.py` is imported by `app.py` at CLI startup (typer registration
requires it), so the module's import must stay cheap: `httpx` is already
imported by the package; no network, no DB, no heavy imports at module level.
The registry call happens only inside the command body.

## Integration Points

### Provides to Other Slices

- Nothing structural. `InstallMethod` and the constants are available if a
  future diagnostics command (`mt status`) wants to report install provenance.

### Consumes from Other Slices

- **908:** the published distribution and `DISTRIBUTION_NAME`.
- **`mt data migrate status --json`** (slice 131/152 lineage): consumed as a
  subprocess with a tolerant parse — if that command's JSON shape changes,
  this probe degrades to the generic pointer line rather than breaking.

## Success Criteria

### Functional Requirements

1. `mt update` on an up-to-date uv-tool install reports "up to date" and
   exits 0 without prompting.
2. `mt update` with a newer published version prompts (TTY), upgrades via
   `uv tool install --upgrade manta-trading-data` on confirmation, and
   reports the new version.
3. `mt update --yes` performs the upgrade without prompting; non-TTY without
   `--yes` reports what it would do and takes no action.
4. `mt update --json` emits the documented object and provably performs no
   side effects (no prompt, no subprocess, no DB, no migration probe).
5. On an editable or source-checkout install, the command refuses with
   developer guidance and makes no network call.
6. On pipx/pip installs, the command prints the correct upgrade command and
   does not run it.
7. Registry unreachable (network down, non-200, malformed body) produces a
   clean one-line message and exit 1 — never a traceback.
8. After a successful upgrade, the pending-migration count (or the generic
   pointer) is printed; a database that is down, unconfigured, or absent
   cannot change the update's outcome or exit code.
9. No code path in the CLI performs a registry call except the body of
   `mt update`.

### Technical Requirements

- `packaging` declared in `[project] dependencies`; version comparison uses
  `packaging.version` exclusively.
- All comparison values (URL template, timeouts, install-method strings,
  upgrade argv) defined once — no scattered literals.
- Unit tests cover: up-to-date, update-available, unreachable-registry
  (timeout, non-200, malformed JSON, missing key), each `InstallMethod`
  branch (upgrade call mocked), `--json` purity, `--yes`/TTY/non-TTY matrix,
  migration-probe success and each degradation path.
- mypy and ruff clean on all touched files.

### Verification Walkthrough

Draft demo script — refined at Phase 6 completion.

**1. Developer machine (the state this repo is in):**

```console
$ uv run mt update
Developer install detected (editable/source checkout) — self-update disabled.
  To update: git pull && uv sync
$ echo $?
0
```

No network traffic occurs (verifiable with the unit test; casually, it
returns instantly offline).

**2. Pure query on an installed copy:**

```console
$ mt update --json
{
  "current": "0.6.1",
  "latest": "0.6.1",
  "update_available": false,
  "install_method": "uv-tool"
}
```

**3. Real end-to-end upgrade (release-time, completes 908's deferred criterion 4):**

This slice's own release (e.g. `v0.7.0`) provides the second published
version. On a machine with the previous version installed:

```console
$ uv tool install manta-trading-data==0.6.1   # pin the older version
$ mt --version
mt version 0.6.1
$ mt update
Update available: 0.6.1 → 0.7.0
Install now? [y/N]: y
... uv output ...
✓ Updated manta-trading-data to 0.7.0
Run 'mt data migrate status' to check for pending migrations.
$ mt --version
mt version 0.7.0
```

Note the pin must be removed for `uv tool install --upgrade` to move past it
— `mt update`'s upgrade command installs unpinned, which replaces the pinned
receipt; the walkthrough confirms this observed behavior at release time.

**4. Registry outage does not break the command:**

```console
$ HTTPS_PROXY=http://127.0.0.1:9 mt update
Could not reach PyPI — check your network connection.
$ echo $?
1
```

**5. Non-TTY safety:**

```console
$ echo | mt update            # stdin not a TTY
Update available: 0.6.1 → 0.7.0
Run with --yes to install non-interactively.
$ echo $?
0
```

## Implementation Notes

### Development Approach

Suggested order:

1. Constants + `packaging` dependency + `InstallMethod` enum.
2. Pure helpers with their unit tests (network and subprocess mocked via
   `monkeypatch`; no new test dependencies needed).
3. Typer command + registration; behavior-matrix tests via
   `typer.testing.CliRunner`.
4. README: add an "Updating" line (`mt update`, and the manual
   `uv tool install --upgrade` equivalent).
5. Release: version bump, CHANGELOG, tag → CI publishes; then run
   walkthrough step 3 against the two real published versions.

Effort: 2/5. Risk: low — the shape is ported from a production-proven
implementation, and every failure path degrades to a printed message.

### Special Considerations

- **Security:** the upgrade subprocess uses a fixed argv list (no shell, no
  user input interpolation). The registry response influences only the
  version string that is *displayed and compared* — it is never interpolated
  into the command (the upgrade installs latest-unpinned, deliberately).
- **The running process is old code during the upgrade:** on all platforms we
  target, replacing the venv while `mt` runs is safe (the old interpreter
  holds its images); the success message advises re-running `mt --version`
  to confirm the new copy.
