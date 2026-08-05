---
docType: analysis
project: trading-data
dateCreated: 20260804
dateUpdated: 20260804
status: draft — for PM review
relatedNotes:
  - 2026-08-04-prod-metadata-truncation-incident.md
---

# Prod-DB guardrails: what is enforced deterministically, and what remains rules

Scoping requested by the PM after the 2026-08-04 truncation incident: split the
prevention work into (a) controls that are **deterministic** — enforced by a
machine regardless of anyone's judgment, discipline, or context — and (b)
**rules** — written guidance covering only what cannot be made deterministic.
The ordering principle: a rule is the *residue* after deterministic options are
exhausted, never the first line.

The incident's causal chain had four links, each of which is a separate
enforcement point:

1. Production credentials reachable by a test process (`.env` injected whole).
2. A fixture that reads the production variable directly
   (`test/integration/conftest.py`).
3. A tier with no mechanical guard (the load tier had one since slice 167; the
   integration tier had none).
4. A connection role permitted to do anything (`MT_TIMESCALE_DB_URL` connects
   as **`postgres` — superuser, owner of every table**; verified 2026-08-04).

Break any link and the incident does not happen. Deterministic controls exist
for all four.

---

## Layer 1 — Server-side (Postgres roles). Strongest; cannot be bypassed by any client code

**1a. Split the connection roles.** Today every consumer — daemon, API, CLI,
tests, scratchpad scripts — connects as `postgres`. Proposed:

| Role | Privileges | Used by |
| --- | --- | --- |
| `trading_app` (exists, unused) | SELECT/INSERT/UPDATE/DELETE on app tables; **no TRUNCATE, no DDL, not owner**; SELECT-only on `schema_migrations` | daemon, API, `mt data pull/ca/universes/status`, `MT_TIMESCALE_DB_URL` |
| `postgres` (or a new `trading_maint`) | owner / DDL / TRUNCATE | `mt data init|migrate|restore|rechunk|caggs repair` via a **separate variable** (e.g. `MT_TIMESCALE_MAINT_URL`), supplied only when doing maintenance |

Under this split, every statement the incident fixture ran — `TRUNCATE`,
`ALTER TABLE`, `DELETE FROM schema_migrations` — fails with `permission
denied` even if a test is handed the production URL. TRUNCATE is a distinct
grantable privilege in Postgres; withholding it costs the app path nothing.

Decision needed from PM: role names, and whether the maintenance URL lives in
`.env` (convenient, still one `TRUNCATE`-capable credential on disk) or is
supplied per-invocation (safer, slightly more friction). **Not executed —
changes prod auth posture; needs a privilege inventory of the daemon/API
write set first so nothing breaks at 2am.**

**1b. Session-resource bounds on the app role.** `ALTER ROLE trading_app SET
statement_timeout = '300s'` (and optionally a lower `work_mem`). Bounds the
blast radius of ad-hoc queries; the 512MB × 16-workers global config stays for
sessions that genuinely need it. (Journal 20260720 already requires
`statement_timeout` on ad-hoc prod work as a rule; this makes it default.)

## Layer 2 — Repo-side mechanical guards (deterministic given CI/test runs)

**2a. Rewrite `instruments_clean_db` onto `ephemeral_db`.** Already settled as
journal decision 2 (20260804): a destructive-by-design fixture must be unable
to *name* a shared database. Implemented alongside this note.

**2a′. The unit tier had the same hole, and it explains the incident note's
open question.** Fixtures in `test/unit/universe/test_tracking.py`,
`test/unit/data/test_equity_universe.py` and
`test/unit/cli/commands/test_data_universes.py` ran `DELETE FROM
universe_members` (sp500-scoped) against `MT_TIMESCALE_DB_URL`. Production
`universe_members` held only sp500 rows, so the scoped DELETE emptied the
table while leaving its relfilenode original — precisely the "unexplained,
separate event" in the incident note — and `pytest test/unit` reported 1855
green while doing it. All three, plus `test_state.py`'s prod
`acquisition_state` writes, now run on `migrated_db` (ephemeral). Two
generalizations worth keeping: **"unit tier" is a label, not a property** —
nothing stops a file under `test/unit/` from opening a DB connection; and the
load-tier guard's per-line scan **cannot see multiline reads**
(`os.environ.get(\n "VAR")`), which is the shape all three offenders used —
the shared predicate in `test/_prod_url_guard.py` is multiline-aware.

**2b. Per-tier prod-URL guard — as a ratchet, not a mirror.** The load
tier's guard bans all reads of the prod variable; the integration tier cannot
copy that verbatim because ~25 of its files read `MT_TIMESCALE_DB_URL` today,
most deliberately (read-only checks against real data). The deterministic form
that fits: an enforcement test with an **explicit frozen allowlist** of the
files that may read the variable. Any *new* file reading it fails the tier;
removing a file from the list is one-way (the test also fails on stale
allowlist entries, so the list can only shrink). Implemented alongside this
note. Long-term goal: allowlist reaches zero and the test collapses to the
load-tier form.

**2c. Environment discipline for child processes.** The runner that caused the
incident loaded `.env` wholesale and injected every variable. Deterministic
fix: a sanctioned test-runner path that passes an explicit **allowlist** of
variables (`MT_TIMESCALE_TEST_URL`, API keys where a tier needs them) and
nothing else. Whole-`.env` injection into a test process becomes impossible
through the sanctioned path; the hook layer (3a) is what makes unsanctioned
paths visible.

## Layer 3 — Agent-harness hooks (deterministic at the Claude boundary; PM: "later")

The squadron-style `PreToolUse` hook. Honest scoping: hooks match *command
strings*, and the incident's command was `uv run python <scratchpad>/runtests.py`
— no `pytest` substring, no URL text. String-matching hooks are therefore a
tripwire against the *common* shapes, not a boundary. The threat model is
accidents, not adversaries, so a tripwire still has value — but layers 1–2 are
what actually hold.

Candidate hook checks, in decreasing value:

- Block `Bash` commands that would run anything test-shaped
  (`pytest`, `test/`, `runtests`) when the command does not go through the
  sanctioned env-scrubbed runner (2c).
- Block `psql`/one-liner SQL containing `TRUNCATE|DROP|DELETE FROM
  schema_migrations` when the target dbname matches a configured prod list
  (`trading`), outside the sanctioned maintenance commands.
- Warn (not block) on any command that both reads `.env` and spawns a child
  process.

Hooks live in `.claude/settings.json` `PreToolUse`; deterministic to write,
worth doing after 1a lands (a hook should never become the *only* layer).

## Layer 4 — Rules (the non-deterministic residue)

What genuinely cannot be made mechanical, distilled into CLAUDE.md (the "fix
the rules" deliverable, applied 2026-08-04):

1. Tests never read `MT_TIMESCALE_DB_URL`; destructive fixtures target only
   databases they themselves created (`ephemeral_db`). — *The rule restates
   what 2a/2b enforce, for the cases the guards can't see (new tiers, scratch
   scripts).*
2. Never inject a whole `.env` into a child process; pass named variables.
3. Before running any test tier for the first time in a session, read its
   conftest for what its fixtures connect to. — *Pure judgment; no mechanical
   form.*
4. Ad-hoc prod sessions: `SET statement_timeout` sized to intent; after a
   client-side timeout, `pg_cancel_backend` before anything else (20260720).
5. Maintenance/restore tooling takes its URL from an explicit caller argument,
   never ambient environment (`restore_metadata.py` is the template).

## Backup exposure (from the incident note's action item)

What backup currency should be driven by — tables *not* cheaply re-derivable:

| Table | Re-derivable? | Notes |
| --- | --- | --- |
| `minute_ohlcv` (4.4 B) | in principle (EODHD), **in practice no** — quota/weeks of pulling | **backup priority 1** |
| `daily_ohlcv` | same, smaller | priority 2 |
| `acquisition_state` | no external source; losing it = mass re-pull cost | priority 3 |
| `instruments`, `splits`, `dividends`, `universe_members`, calendars | yes (EODHD / MarketDB / GitHub CSV / code) | low priority |
| `schema_migrations` | from repo | low |

## Restore-tooling defects found while executing the 2026-08-04 restore

Recorded here so the next incident's tooling starts honest:

- `assess()` expected `minute_1hour_ohlcv`, a view that has never existed
  (real name `minute_hourly_ohlcv`) — a by-name probe drift of exactly the
  slice-167 class. Fixed 2026-08-04.
- `assess()` omitted the three daily rollup caggs, so migration 034's losses
  were invisible to the tool (found only by querying the catalog directly).
  Fixed 2026-08-04.
- The ledger-replay design has a boundary: objects dropped while their
  creating migration is still ledgered (`minute_coverage`/046, columnstore
  config/045, coverage policies/047) are not recreated by replay and needed
  their idempotent DDL applied directly.
- Migration 036 skipped silently in the CLI because it reads
  `MT_MARKET_DB_URL` from `os.environ`, which the CLI does not populate from
  `.env` — the copy was performed manually. Same env-source split that caused
  the incident, in benign form: **settings and `os.environ` are two different
  sources of truth.**
