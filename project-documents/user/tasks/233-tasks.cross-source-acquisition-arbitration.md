---
docType: tasks
slice: cross-source-acquisition-arbitration
project: trading-data
lld: user/slices/233-slice.cross-source-acquisition-arbitration.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [226]
interfaces: [234]
projectState: >
  Slice design committed and reviewed (CONCERNS, addressed). Prerequisites 226
  and 916 are complete. `manta-acquisition.slice` is a comment-only unit with
  no resource settings; three oneshot units (minute, daily, Kalshi) name it.
  Production runs `v0.19.0` and nothing in this slice is installed.
dateCreated: 20261014
dateUpdated: 20261014
status: not_started
---

# Tasks: Cross-Source Acquisition Arbitration

## Context Summary

- Working on slice 233. It sets `MemoryMax` on `manta-acquisition.slice`,
  derived from the journal's 30-day memory peaks, and records that calendar
  stagger stays as the arbitration mechanism. It closes 900 Future Work item 4.
- **No `CPUWeight` or `IOWeight`** (inert on this host: Technical Decision 1)
  and **no contention run** (Technical Decision 3). PM scope cut, 2026-10-14.
- It delivers:
  - `scripts/acquisition_memory_bound.py` (read-only derivation);
  - `deploy/systemd/manta-acquisition.slice` with `MemoryMax=` and its comment;
  - a drift guard in `test/unit/deploy/test_units.py`;
  - the runbook, 900 plan, architecture Revision Log and CHANGELOG entries.
- **Nothing is installed and no cutover script is built.** 234's cutover
  installs the file and verifies it is in force (issue #33). This slice claims
  only that the bound is declared and derived.
- **No change to `src/`.** The integration and load tiers are not required;
  the unit tier is.
- The slice plan's 233 entry and 234's #33 obligation were already written
  with the design; do not edit them.
- Next planned slice: 234 (Tick Pass Production Wiring).

**Conventions for every task below**

- Run from the repository root. Confirm with `pwd` before file or shell work.
- `ruff format` only the files the task touched. Before each commit, run
  `git diff main --stat` and look for deletions in files you did not mean to
  change (ruff rewrites whole files).
- mypy: one invocation covering the kalshi source paths plus tests, never a
  narrower run (project memory: narrower runs report false errors).
- Commit messages use the project's semantic prefixes and end with the
  attribution lines in force for the session.

---

## Section 0 — Baseline (read only)

- [ ] **0.1 Create the findings note and record the host facts**
  - [ ] Create `project-documents/user/notes/{YYYY-MM-DD}-233-findings.md`
        (today's date; the 916 note
        `2026-08-23-916-verification-results.md` is the format model), with
        YAML front matter per `file-naming-conventions.md`.
  - [ ] Re-read, read-only, and record the observed value for each row of
        Technical Decision 1's table: `systemd-cgroup-ls`-style path of the
        slice (`systemctl show -p ControlGroup manta-acquisition.slice`),
        `cat /sys/block/nvme*/queue/scheduler`, presence of
        `/sys/fs/cgroup/io.cost.qos`, `free -g`, and the cgroup of
        `postgresql@17-main.service` and `postgresql@17-tick.service`.
  - [ ] Where an observation differs from the design's table, write the
        difference in the note and **stop and tell the Project Manager**
        before continuing; the no-weights decision rests on these facts.
  - [ ] Success: the note exists and states, per row, "matches design" or the
        difference
  - [ ] Effort: 1

- [ ] **0.2 Capture the 30-day memory-peak lines**
  - [ ] For each of `mt-minute-pass.service`, `mt-daily-pass.service`,
        `mt-kalshi-pass.service`, run
        `journalctl -u <unit> -o cat --since -30d | grep 'memory peak'` and
        record each unit's maximum peak in the note, with the unit name.
  - [ ] Copy into the note, verbatim, at least: one minute line in the
        `1h 28min …` wall-clock form with a `G` peak (a Saturday firing), one
        minute line with an `M` peak, one daily line, one Kalshi line. These
        become the parser test's fixture (task 1.2).
  - [ ] Success: the note holds three maxima and four verbatim lines, and the
        minute maximum is of the order the design cites (about 6.2 G)
  - [ ] Effort: 1

- [ ] **0.3 Commit the baseline**
  - [ ] Commit the note: `docs: record 233 baseline host facts and peaks`
  - [ ] Effort: 1

---

## Section 1 — Derivation script

The parser and the rule are pure functions; the journal read is the only I/O
and sits at the entry point (design: Component Structure).

- [ ] **1.1 Member discovery and the journal peak parser**
  - [ ] Create `scripts/acquisition_memory_bound.py`. Module docstring cites
        the 233 design and states the script writes nothing.
  - [ ] Define named constants with docstrings citing the design:
        `MEMORY_BOUND_HEADROOM = 2`, `JOURNAL_WINDOW_DAYS = 30`, and the slice
        name `manta-acquisition.slice` (once, in this file).
  - [ ] `discover_members(systemd_dir) -> list[str]`: the unit names of
        `*.service` files whose `[Service]` section declares
        `Slice=<the slice name>`. Parse leniently (configparser with
        `strict=False`, `interpolation=None`, `optionxform = str`, as
        `test/unit/deploy/test_units.py::_parse` does). Sorted result. Never a
        hand-written list.
  - [ ] `parse_memory_peak(line) -> int | None`: bytes of the
        `<size> memory peak` clause wherever it appears in a `Consumed …`
        line; `None` when the line has no such clause. Accept the `K`, `M`,
        `G`, `T` suffixes with fractional values (`1024` base, as systemd
        prints). Do not anchor on what precedes or follows the clause
        (CPU, wall clock, swap peak vary by systemd version).
  - [ ] `max_peak(lines) -> int`: the largest parsed peak; raise `ValueError`
        naming the problem when no line yields a peak. No zero or default.
  - [ ] Success: the file imports cleanly; functions are < 50 lines each
  - [ ] Effort: 2

- [ ] **1.2 Tests for discovery and the parser**
  - [ ] Create `test/unit/scripts/__init__.py` (empty) and
        `test/unit/scripts/test_acquisition_memory_bound.py`. Import the
        script by inserting `scripts/` on `sys.path`, as
        `test/unit/test_drill_228_fingerprint.py` does.
  - [ ] Parser tests use the **verbatim lines from task 0.2** as fixtures
        (a module constant), covering: the `1h 28min` form with a `G` peak,
        an `M` peak, a line with no peak clause (returns `None`), and a line
        with extra clauses after the peak (e.g. a swap peak) if the journal
        showed one.
  - [ ] Discovery tests: run against the real `deploy/systemd/` and assert
        exactly the minute, daily and Kalshi services come back; run against
        a `tmp_path` directory to prove a unit without `Slice=` and a unit
        naming another slice are excluded.
  - [ ] `max_peak` tests: picks the largest of several lines; raises on an
        empty list and on lines with no peak.
  - [ ] Success: `uv run pytest test/unit/scripts -q` passes
  - [ ] Effort: 2

- [ ] **1.3 The `MemoryMax` rule**
  - [ ] Add a pure `memory_max_gib(peaks_by_member: dict[str, int]) -> int`:
        `ceil_GiB(MEMORY_BOUND_HEADROOM × Σ peaks)`, GiB = 2**30 bytes.
        Raise `ValueError` for an empty mapping (a slice with no members is
        a discovery failure, not a zero bound).
  - [ ] Success: for peaks of 6.2 G, 426 M and 317 M the result is 14
  - [ ] Effort: 1

- [ ] **1.4 Tests for the rule**
  - [ ] Add to the existing test file: the 14 GiB case above; an exact-GiB
        boundary (the sum doubling to exactly N GiB gives N, not N+1); a
        single-member case; empty mapping raises.
  - [ ] Add a test that a member with no peak line fails the derivation by
        way of `max_peak` raising, with the member named in the message
        (see 1.5 for where the name is attached).
  - [ ] Success: `uv run pytest test/unit/scripts -q` passes
  - [ ] Effort: 1

- [ ] **1.5 Entry point: journal read, print, exit codes**
  - [ ] `read_journal(unit) -> list[str]`: runs
        `journalctl -u <unit> -o cat --since -{JOURNAL_WINDOW_DAYS}d` with
        `subprocess.run(..., check=False)`; a non-zero return raises a
        `RuntimeError` carrying the unit and the captured stderr. Nothing
        catches broadly.
  - [ ] `main()`: discover members from `deploy/systemd/` (resolved from the
        script's own path, not the cwd); per member, read the journal and
        take `max_peak`; print one line per member (name, peak in a human
        unit) then the result in GiB and the rule's inputs. Writes no file.
  - [ ] Exit `0` on success, `2` when a member's journal could not be read or
        has no peak line (design: API Contracts). The handler at the process
        boundary prints the error to stderr and exits 2; it catches only the
        two exception types this script raises, with a comment saying why.
  - [ ] Keep the entry point separate from the pure functions so the unit
        tests never call `journalctl`.
  - [ ] Success: the file is < 300 lines; `uv run ruff check` clean on it
  - [ ] Effort: 2

- [ ] **1.6 Test the entry point without a host**
  - [ ] Test `main()` with `read_journal` replaced (monkeypatch) by canned
        lines: exit 0 and the output names all three members and the result;
        a member whose canned lines have no peak gives exit 2 and the member
        name on stderr; a `read_journal` that raises gives exit 2.
  - [ ] Assert the script created no file (check `tmp_path` / cwd is
        unchanged).
  - [ ] Success: `uv run pytest test/unit/scripts -q` passes
  - [ ] Effort: 2

- [ ] **1.7 Run on the host and commit**
  - [ ] Run `uv run python scripts/acquisition_memory_bound.py`. Expected:
        three members, minute about 6.2 G, a result of about 14 GiB, exit 0
        (walkthrough step 1). Paste the output into the findings note.
  - [ ] `ruff check` and scoped `ruff format` on the script and its test;
        mypy per the conventions above.
  - [ ] Commit: `feat: add acquisition memory-bound derivation script`
  - [ ] Success: output recorded; checks clean; committed
  - [ ] Effort: 1

---

## Section 2 — Unit file and drift guard

- [ ] **2.1 Set `MemoryMax` in the slice file**
  - [ ] In `deploy/systemd/manta-acquisition.slice`, replace the "Deliberately
        carries NO resource settings" comment with one that states:
    - [ ] the rule (`ceil_GiB(2 × Σ member 30-day peaks)`) and that
          `scripts/acquisition_memory_bound.py` computes it;
    - [ ] the inputs (each member's peak) and the date they were read, copied
          from task 1.7's output;
    - [ ] that a unit joining the slice re-derives the value;
    - [ ] why `CPUWeight` and `IOWeight` are absent: no sibling cgroup under
          `manta.slice`, and no I/O controller honouring weights on either
          NVMe device (cite the 233 design, Technical Decision 1), so a
          later reader does not "fix" it.
  - [ ] Add `MemoryMax=<N>G` under `[Slice]`, with `N` being the script's
        result from 1.7. Keep the existing `[Unit]` keys.
  - [ ] Success: `systemd-analyze verify deploy/systemd/manta-acquisition.slice`
        prints nothing about `MemoryMax` (walkthrough step 3)
  - [ ] Effort: 1

- [ ] **2.2 Drift guard in `test/unit/deploy/test_units.py`**
  - [ ] Add a `TestAcquisitionSlice` class using the file's existing
        `_parse` helper and `_SYSTEMD` path:
    - [ ] the slice file declares `MemoryMax` in `[Slice]`, in the form
          `<integer>G`, and the integer is > 0;
    - [ ] it declares neither `CPUWeight` nor `IOWeight`;
    - [ ] every `*.service` in `deploy/systemd/` whose `[Service]` has
          `Slice=manta-acquisition.slice` is `Type=oneshot` (a `Type=simple`
          unit must not join a staggered, memory-shared slice without the
          reopen decision in Technical Decision 3);
    - [ ] at least one member exists (a guard that passes on an empty set
          proves nothing).
  - [ ] Do not edit existing tests in this file. Reuse the script's
        discovery function only if a clean import is possible; otherwise
        repeat the one-line `Slice=` check and keep the guard independent of
        the script.
  - [ ] Success: `uv run pytest test/unit/deploy/test_units.py -q` passes
        and the new class fails if `MemoryMax` is deleted from the slice
        file (confirm by deleting the line once, running, restoring it)
  - [ ] Effort: 2

- [ ] **2.3 Verify and commit**
  - [ ] `uv run pytest test/unit/deploy/test_units.py test/unit/scripts/test_acquisition_memory_bound.py -q`
        passes (walkthrough step 2).
  - [ ] `ruff check`, scoped `ruff format`, mypy; `git diff main --stat`
        shows only the files in this slice.
  - [ ] Commit: `feat: set MemoryMax on manta-acquisition.slice`
  - [ ] Success: tests green; commit contains the slice file, the guard and
        nothing else
  - [ ] Effort: 1

---

## Section 3 — Documents

- [ ] **3.1 Runbook 100: unit table row**
  - [ ] In `user/runbooks/100-production-operations.md`, change the
        `manta-acquisition.slice` row from "carries no resource limits yet" to
        the bound and where it comes from (`MemoryMax`, derived by
        `scripts/acquisition_memory_bound.py`). State that the bound is
        applied at the release that follows 234.
  - [ ] Success: the phrase "carries no resource limits yet" no longer appears
        in the file
  - [ ] Effort: 1

- [ ] **3.2 Runbook 100: add-a-source step**
  - [ ] In "Adding a source or pass" add a step: a unit that sets
        `Slice=manta-acquisition.slice` re-derives `MemoryMax` by running
        `uv run python scripts/acquisition_memory_bound.py` and copying the
        result and inputs into the slice file's comment. Place it before the
        install step and renumber the steps that follow; fix any in-file
        reference to a step number.
  - [ ] Note that a new unit has no journal peak line until it has run once;
        the script exits 2 until then, which is intended (no default).
  - [ ] Success: the list is numbered consecutively; grep finds no stale
        "step N" reference pointing at a moved step
  - [ ] Effort: 1

- [ ] **3.3 Runbook 100: rewrite the second "limit of the pattern"**
  - [ ] Replace the bullet "systemd serializes a unit only against itself …
        cross-source arbitration does not exist yet (Future Work 4 …)" with:
        stagger is the kept mechanism, and why (the minute and daily passes
        share EODHD's quota, so they start 30 minutes apart; Kalshi and tick
        overlaps measured harmless: 226, and 24 Kalshi × heavy-minute
        overlaps). Name the two reopen triggers from Technical Decision 3: a
        pass that slows measurably when it overlaps another (seen in
        `pass_runs` durations), and a `Type=simple` streaming source.
  - [ ] Success: the bullet no longer says arbitration "does not exist yet"
        and names both triggers
  - [ ] Effort: 1

- [ ] **3.4 900 plan: close Future Work item 4**
  - [ ] In `user/architecture/900-slices.foundation-cleanup.md`, mark item 4
        `[x]` and append, without altering the existing text: closed by 233;
        `MemoryMax` only, weights not set because they are inert on this
        host (one sentence); stagger kept on existing evidence (one
        sentence); the two reopen triggers (one sentence).
  - [ ] Success: item 4 is checked, original wording intact, three added
        sentences
  - [ ] Effort: 1

- [ ] **3.5 Architecture Revision Log entry**
  - [ ] In `user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md`
        Revision Log, add a dated entry (today) in the existing bullet style:
        **"Cross-source arbitration"**: `MemoryMax` only; weights not set
        (reasons in the 233 design, Technical Decision 1); the mechanism is
        calendar stagger, kept on existing evidence. Do not rewrite the
        "Cross-source arbitration" Technical Considerations paragraph (the
        log carries the change, as for prior entries).
  - [ ] Success: the entry exists, names the slice, and appears after the
        2026-10-03 entry
  - [ ] Effort: 1

- [ ] **3.6 CHANGELOG**
  - [ ] Under `## [Unreleased]` in `CHANGELOG.md`, add an entry in the file's
        user-facing voice: the acquisition slice gets a memory bound derived
        from measured peaks, a runaway pass is killed inside its own slice
        rather than the host's OOM killer picking among PostgreSQL backends,
        and the bound takes effect at the release that follows 234. Use the
        `### Added` section as the neighbouring entries do.
  - [ ] Success: one entry; no claim that the bound is already in force
  - [ ] Effort: 1

- [ ] **3.7 Commit the documents**
  - [ ] Commit: `docs: record 233 arbitration decision in runbook, plan, log`
  - [ ] Success: committed; `git status` clean
  - [ ] Effort: 1

---

## Section 4 — Validation and close-out

- [ ] **4.1 Static checks**
  - [ ] `uv run ruff check` on every touched `.py` file; scoped
        `ruff format --check`; mypy per the conventions above.
  - [ ] The script is < 300 lines and each function < 50 lines.
  - [ ] `git diff main --stat` shows no file outside: the script, its test
        package, `test_units.py`, the slice file, the three documents, the
        CHANGELOG and the findings note. No change under `src/`.
  - [ ] Success: all clean; diff scope matches
  - [ ] Effort: 1

- [ ] **4.2 Unit tier**
  - [ ] `uv run python scripts/run_tests.py unit`. Any failure not in the
        known pre-existing list in project memory must be fixed; re-run a
        suspected flake in isolation before investigating.
  - [ ] Success: green apart from the known pre-existing failures, each
        listed in the findings note
  - [ ] Effort: 1

- [ ] **4.3 Verification walkthrough**
  - [ ] Run the design's Verification Walkthrough steps 1–3 once more from
        the checkout root and record the outputs in the findings note.
  - [ ] Confirm `deploy/systemd/manta-acquisition.slice`'s `MemoryMax` equals
        a fresh run of the script (the journal window moves daily; a
        different integer means re-derive and update the file and comment
        before closing).
  - [ ] Confirm the slice plan's 234 entry still carries the issue #33
        obligation (read only; do not edit).
  - [ ] Success: outputs recorded; file and script agree; #33 obligation
        present
  - [ ] Effort: 1

- [ ] **4.4 Findings and standing 220 obligations**
  - [ ] In the findings note record: the Saturday 6.2 GiB minute-pass peak
        (every firing day, only then; other days 75–144 M; probable cause the
        trailing phase's gap-row seeding, not investigated here); the Kalshi
        contract diff as "none" (no pass code changed); realtime paths
        "neither path ruled out; a streaming source is the named reopen
        trigger"; data-correctness contract "no rows, no data path touched".
  - [ ] State plainly that nothing is installed and the bound is declared,
        not in force, until 234's cutover.
  - [ ] Success: all four obligations answered in one sentence each
  - [ ] Effort: 1

- [ ] **4.5 Close out the design**
  - [ ] Update the slice design with any walkthrough corrections and the
        findings note's path; set the design and this file to
        `status: complete`; bump `dateUpdated`.
  - [ ] Commit: `docs: record 233 implementation findings`
  - [ ] Success: committed on the slice branch; no merge step performed
  - [ ] Effort: 1
