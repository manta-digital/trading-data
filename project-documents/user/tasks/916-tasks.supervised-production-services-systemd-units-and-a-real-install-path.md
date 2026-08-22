---
docType: tasks
slice: supervised-production-services-systemd-units-and-a-real-install-path
project: trading-data
lldReference: user/slices/916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [128, 908]
interfaces: [907, 913, 915]
projectState: >
  Production on manta9000 (192.168.1.144) is the developer checkout at
  ~/source/repos/manta/trading-data, run by hand. No systemd units exist, no
  /opt/manta-trading, no /etc/manta-trading.env. The 2026-08-19 crash brought
  back PostgreSQL and the backup cron but not acquisition. deploy/systemd/ holds
  two never-installed .service.tmpl files from slice 128 plus a journald drop-in.
  The host has no passwordless sudo.
status: not_started
dateCreated: 20260822
dateUpdated: 20260822
---

# Tasks: Supervised production services — systemd units and a real install path

## Context summary

Production has no supervision: a reboot or crash stops acquisition silently and
nothing brings it back. This slice installs a dedicated, pinned checkout at
`/opt/manta-trading` under a `manta-trading` service account, and puts three
things under systemd — the daily pass, the minute pass, and `mt serve`.

The production invocation does not change: `mt data daemon run {--daily|--minute}
--stop-when-done` is still one bounded pass that exits. A timer fires it instead
of a human.

Read the design before starting. The decisions that shape these tasks are:
oneshot-passes-on-timers (not a looping daemon), the dedicated `/opt` install,
one PM-run install script that **enables nothing**, and a cutover that is a
single reversible `systemctl enable --now`.

## Execution notes

- **Root boundary.** manta9000 has no passwordless sudo. Every task marked
  **[PM]** is executed by the Project Manager. **[agent]** tasks need no
  elevation. Do not attempt to work around a **[PM]** task.
- **Stage everything in the repo.** Multi-line pastes into the host's zsh have
  already proven unreliable. Nothing longer than a single command line is ever
  relayed for the PM to paste — it goes in a file that is committed first.
- **Nothing is enabled until Group F.** Groups A–E leave production running
  exactly as it does today, by hand from the dev checkout. If a step would start
  or enable a unit before F, that is a stop-and-ask.
- **The dev checkout is never modified.** It stays runnable throughout, and is
  the rollback path.
- **Nothing here takes more than minutes.** The single bounded wait is E.1: a
  `--stop-when-done` pass that meets a closed cadence gate *sleeps* rather than
  exiting, for at most 30 minutes (the gate is UTC-midnight + 30 min, and the
  retry interval is 30 min). There is no "already ran today, come back tomorrow"
  path — the due-predicate deliberately makes no UTC-day comparison. Check
  `acquisition_state.last_daily_cycle_end_utc` before E.1 and the wait is
  usually zero. A wait, if it happens, announces itself every 5 minutes.
- **No task waits on a wall-clock event.** Timer *firing* is proven by
  `systemctl start` on the same unit the timer activates, plus `list-timers` for
  the schedule. The one reboot (G.4) is a PM-scheduled moment executed
  immediately, not a wait.
- Credentials never enter a tracked file. `deploy/manta-trading.env.example`
  carries placeholders only.

---

## Group A — Unit files in the repo

Effort: 2/5. All **[agent]**, all repo-only. Nothing is installed here.

New files live in `deploy/systemd/`. Every unit gets
`Documentation=` pointing at the current repo URL — the existing templates carry
a stale `manta-trading/trading` URL from before the rename.

- [ ] **A.1 [agent] Create `manta-acquisition.slice`**
  - [ ] `[Unit]` with a `Description` only. **No resource settings** —
        `IOWeight`/`CPUWeight`/`MemoryMax` are deliberately absent (design:
        Deferred). This unit exists so units have a home, not to tune anything
  - [ ] Success: file exists; `systemd-analyze verify` reports no errors

- [ ] **A.2 [agent] Create `mt-daily-pass.service`**
  - [ ] `Type=oneshot`, `User=manta-trading`, `Slice=manta-acquisition.slice`,
        `WorkingDirectory=/opt/manta-trading`,
        `EnvironmentFile=/etc/manta-trading.env`
  - [ ] `ExecStart=/opt/manta-trading/.venv/bin/mt data daemon run --daily
        --stop-when-done` — the venv entry point directly. **Not `uv run`**: uv
        is deploy-time only and would risk a resolve/sync at service start
  - [ ] `TimeoutStartSec=infinity` with the comment explaining why (a pass
        legitimately runs long; the 90s default would kill it mid-fetch)
  - [ ] `TimeoutStopSec=300` with the comment explaining why (the runner traps
        SIGTERM and exits between symbols, but its sleeps are capped at 60s, so
        the 90s default would SIGKILL a shutdown that is working correctly)
  - [ ] Hardening: `NoNewPrivileges=true`, `ProtectSystem=full`,
        `ProtectHome=true`, `PrivateTmp=true`; journal for stdout and stderr
  - [ ] **No `Restart=`** — recovery is the next timer firing. **No `[Install]`
        section** — the timer is what gets enabled, not the service
  - [ ] Success: `systemd-analyze verify` clean; no `[Install]` section present

- [ ] **A.3 [agent] Create `mt-daily-pass.timer`**
  - [ ] `OnCalendar=*-*-* 00:35:00 UTC` and `OnCalendar=*-*-* 12:35:00 UTC` —
        after the 00:30 UTC daily gate; the second firing is a same-day catch-up
  - [ ] `Persistent=true` (post-reboot catch-up), `WantedBy=timers.target`
  - [ ] Success: `systemd-analyze calendar '*-*-* 00:35:00 UTC'` resolves to the
        expected next elapse; `systemd-analyze verify` clean

- [ ] **A.4 [agent] Create `mt-minute-pass.service`**
  - [ ] Identical to A.2 except `ExecStart` uses `--minute`
  - [ ] Success: as A.2. Confirm the two services differ **only** in the flag
        and the description

- [ ] **A.5 [agent] Create `mt-minute-pass.timer`**
  - [ ] `OnCalendar` at 01:05 and 13:05 UTC — staggered 30 minutes behind daily
        so the two passes never start simultaneously
  - [ ] `Persistent=true`, `WantedBy=timers.target`
  - [ ] Success: no `OnCalendar` value collides with A.3's

- [ ] **A.6 [agent] Create `mt-serve.service`**
  - [ ] `Type=simple`, same `User`/`EnvironmentFile`/hardening block,
        `ExecStart=/opt/manta-trading/.venv/bin/mt serve`
  - [ ] `Restart=on-failure`, `RestartSec=10s`, `StartLimitBurst=5`,
        `StartLimitIntervalSec=300s`, `WantedBy=multi-user.target`
  - [ ] Success: `systemd-analyze verify` clean; `[Install]` section present
        (this unit *is* enabled directly, unlike the pass services)

- [ ] **A.7 [agent] Create `deploy/manta-trading.env.example`**
  - [ ] `MT_TIMESCALE_DB_URL` and `MT_EODHD_API_KEY` with **placeholder values
        that are obviously placeholders**, plus commented optional tuning
        (`MT_LOG_LEVEL`, `MT_DAILY_CYCLE_RETRY_MINUTES`)
  - [ ] `MT_TIMESCALE_MAINTENANCE_URL` is **absent**, with a comment saying why:
        the DDL credential stays out of service environments (slice 913)
  - [ ] Success: no real credential in the file; `git diff` shows placeholders

- [ ] **A.8 [agent] Delete the superseded slice-128 templates**
  - [ ] Remove `deploy/systemd/mt-daily-daemon.service.tmpl` and
        `mt-minute-daemon.service.tmpl` — they assume the looping daemon form and
        a templated user, both superseded
  - [ ] Leave `journald-manta-trading.conf` **unchanged**; only its install
        becomes real
  - [ ] Success: `ls deploy/systemd/` shows six units, the slice, and the
        journald drop-in, and no `.tmpl` files

- [ ] **A.9 [agent] Verify the unit set as a whole**
  - [ ] `systemd-analyze verify deploy/systemd/*.service deploy/systemd/*.timer
        deploy/systemd/*.slice` — clean, allowing only warnings that stem from
        the service account and `/opt` not existing yet on the dev machine
  - [ ] Confirm by grep: every acquisition unit sets
        `Slice=manta-acquisition.slice`; no unit carries the pre-rename
        `Documentation=` URL; no `OnCalendar` value appears in two timers
  - [ ] Success: the checks above pass, and the findings are recorded in the
        commit message

- [ ] **A.10 [agent] Commit the unit files**
  - [ ] Success: units, slice, env example, and the template deletions are one
        reviewable commit

---

## Group B — The install script

Effort: 3/5. All **[agent]** (writing it); it is *executed* by the PM in Group D.

`deploy/install-production.sh` is the slice's only substantial new I/O path.
Every step is check-then-act so that the recovery for any failure is "fix the
cause, re-run the whole script." Keep it plain shell — no templating engine.

- [ ] **B.1 [agent] Script skeleton, argument handling, and step logging**
  - [ ] `set -euo pipefail`. Require `--ref <tag-or-sha>`; fail with usage if
        absent. **No default ref** — a silent default would let `/opt` track
        something nobody chose
  - [ ] Refuse to run as non-root with a clear message
  - [ ] Each step announces itself before acting, so a failure names the step it
        died in
  - [ ] Success: `--help` prints usage; running without `--ref` exits non-zero
        with a message naming the missing argument

- [ ] **B.2 [agent] Step 1 — service account**
  - [ ] `getent passwd manta-trading`; create only if absent (system account,
        `nologin` shell, home `/opt/manta-trading`)
  - [ ] If present: verify shell and home match and continue. If present with a
        **different shape, abort** — adopting a colliding or hand-made account
        silently is a PM decision, not a script's
  - [ ] Success: creating, matching-and-skipping, and mismatched-abort are three
        distinct outcomes with distinct messages

- [ ] **B.3 [agent] Step 2 — checkout**
  - [ ] If `/opt/manta-trading/.git` is absent: remove any leftover directory
        (a died-mid-way clone leaves no usable `.git`), then clone at `--ref`
  - [ ] If present: `git status --porcelain` must be empty and `origin` must
        match, else **abort untouched**
  - [ ] A clean checkout at a *different* ref is fetched and checked out to the
        requested ref — the guard is about local modifications only, and must
        never block a retry
  - [ ] Ownership is `manta-trading:manta-trading` throughout
  - [ ] Success: the four paths (fresh clone, clean re-run, different-ref
        checkout, dirty-tree abort) are each reachable and each print why

- [ ] **B.4 [agent] Step 3 — virtualenv**
  - [ ] `uv sync --frozen` as the service account. Do **not** delete `.venv` on
        failure — `uv sync` is resumable and reconciles a partial venv next run
  - [ ] Then verify `/opt/manta-trading/.venv/bin/mt --version` runs; stop hard
        if it does not, since every `ExecStart` depends on that exact path
  - [ ] Success: the version check gates the rest of the script

- [ ] **B.5 [agent] Step 4 — environment file**
  - [ ] Install `/etc/manta-trading.env` from the example **only if it does not
        exist**. Never overwrite, never merge — the PM's filled-in credentials
        must survive every re-run
  - [ ] Re-assert ownership `root:manta-trading` and mode `0640` on every run
  - [ ] Success: a second run leaves an existing file byte-identical (prove by
        checksum) while still correcting mode or owner if they drifted

- [ ] **B.6 [agent] Step 5 — unit files and journald drop-in**
  - [ ] Copy all six units plus `manta-acquisition.slice` to
        `/etc/systemd/system/`, and `journald-manta-trading.conf` to
        `/etc/systemd/journald.conf.d/manta-trading.conf`
  - [ ] Copy unconditionally — the repo is the source of truth and these files
        hold no host-local state
  - [ ] Success: a partial failure here leaves units present but unreferenced
        (inert, because nothing is enabled) and the re-run completes the set

- [ ] **B.7 [agent] Step 6 — reload, and the closing message**
  - [ ] `systemctl daemon-reload` unconditionally at the **end** of every run,
        including runs where step 5 changed nothing. Reload last, so a
        half-copied unit set is never loaded
  - [ ] Restart `systemd-journald` so the journald caps take effect
  - [ ] Print the next two steps (fill the env file; run one pass by hand) and
        restate plainly that **nothing has been enabled**
  - [ ] Success: `systemctl list-unit-files 'mt-*'` after a run shows every unit
        as `disabled`

- [ ] **B.8 [agent] Static-check the script**
  - [ ] `bash -n` and `shellcheck` clean (fix or justify each suppression
        inline — no blanket disables)
  - [ ] Read it against the design's per-step failure table and confirm each
        enumerated failure state matches what the code actually does
  - [ ] Success: both checks pass and the read-through found no divergence, or
        the divergence was fixed

- [ ] **B.9 [agent] Commit the install script**

---

## Group C — Code and documentation

Effort: 2/5. All **[agent]**.

- [ ] **C.1 [agent] Fix the `serve.py` help text**
  - [ ] `src/manta_trading/cli/commands/serve.py` — the `--workers` help says
        "slice 155 adds supervised launch". Slice 155 is dead. Replace with a
        reference to the `mt-serve` systemd unit
  - [ ] Success: no slice number appears in user-facing help text

- [ ] **C.2 [agent] Test the help text**
  - [ ] Assert `mt serve --help` output contains no `slice` reference and names
        the unit. Follow the existing CLI test conventions in
        `test/` rather than inventing a new pattern
  - [ ] Success: the test fails against the old string and passes against the new

- [ ] **C.3 [agent] Rewrite `production-deploy.md` around the `/opt` install**
  - [ ] Delete the "Future target — /opt + systemd" section; the target exists
  - [ ] Close all three "Not yet documented" items by stating the answers: where
        the env file lives, what re-invokes the passes (the timers, with their
        schedule and the gate interaction), and the restart-after-reboot
        procedure — which is "nothing", plus how to verify that
  - [ ] Add the update procedure: fetch, checkout a ref, `uv sync` as the
        service account, `systemctl restart mt-serve`; passes pick up new code at
        their next firing. Migrations stay a separate operator step with the
        maintenance credential
  - [ ] Add status-checking via `systemctl` alongside the existing
        `acquisition_state` queries
  - [ ] **Keep** the timing-constraints section (gate wait, retry interval, cheap
        re-runs) — it now explains timer behavior
  - [ ] State plainly which install is production, and how to tell a supervised
        run from a manual one (`_SYSTEMD_UNIT` in the journal)
  - [ ] Success: no "Future target" and no "Not yet documented" remain in the file

- [ ] **C.4 [agent] Add the "adding a source" procedure to the runbook**
  - [ ] The naming pattern (`mt-{source-or-cadence}-pass` for a bounded pass,
        `mt-{name}` for a long-running service) and the checklist: copy a unit
        pair, change `ExecStart`, pick a non-colliding `OnCalendar`, set
        `Slice=manta-acquisition.slice`, install, enable
  - [ ] State the two limits: `oneshot`+timer suits a **bounded** pass — a
        streaming subscription is a `Type=simple` unit like `mt-serve`, with
        backfill as a separate pass — and systemd serializes a unit only against
        *itself*, so cross-source arbitration does not exist (Future Work 4)
  - [ ] Success: a reader could add a Kalshi pass without reopening the design

- [ ] **C.5 [agent] Add the "pausing a source" procedure to the runbook**
  - [ ] Three levels: `stop` (this boot only), `disable --now` (**the pause that
        survives reboot**), `mask` (nothing can start it, even manually)
  - [ ] `systemctl stop` on a *running* pass is a clean SIGTERM exit, not a kill
  - [ ] **Resuming fires a catch-up immediately** because of `Persistent=true`;
        name `/var/lib/systemd/timers/stamp-mt-{unit}.timer` as the escape hatch
        when a resume should not backfill
  - [ ] Success: pause, resume, and their side effects are all documented

- [ ] **C.6 [agent] Update the two documents that point at slice 916 by name**
  - [ ] `backup-and-restore.md`: the "916 will decide cron vs timers" pointer
        becomes "decided 2026-08-22: backups stay on cron"
  - [ ] The crontab comment wording is updated **via the runbook** — the crontab
        itself is PM-owned host config and is not edited by an agent
  - [ ] Success: no document still describes the cron-vs-timer question as open

- [ ] **C.7 [agent] Commit the code and documentation changes**

---

## Group D — Install on the host, inert

Effort: 2/5. Every root step is **[PM]**. Nothing is enabled in this group.

- [ ] **D.1 [agent] Choose and record the ref to pin**
  - [ ] A tag or commit SHA that contains Groups A–C. Record the exact value;
        every later step uses it
  - [ ] Success: the ref is recorded here, and `git log` confirms it contains
        the unit files and the install script

- [ ] **D.2 [PM] Run the install script**
  - [ ] `sudo deploy/install-production.sh --ref <recorded ref>` from the dev
        checkout
  - [ ] Success: exits 0 and prints the "nothing has been enabled" message

- [ ] **D.3 [PM] Run it a second time — idempotence**
  - [ ] Same command, unchanged
  - [ ] Success: exits 0; no account recreated; the env file is byte-identical
        (checksum before and after); nothing became enabled

- [ ] **D.4 [PM] Fill `/etc/manta-trading.env`**
  - [ ] `sudoedit /etc/manta-trading.env`; set `MT_TIMESCALE_DB_URL` (the
        DML-only application credential) and `MT_EODHD_API_KEY`. Values come
        from the dev checkout's `.env`; **the file itself is never copied**
  - [ ] Success: the file holds real values at mode 0640 `root:manta-trading`

- [ ] **D.5 [agent] Verify the install without starting anything**
  - [ ] `/opt/manta-trading` exists, owned by `manta-trading`, at the pinned ref,
        clean working tree; `.venv/bin/mt --version` runs
  - [ ] **No `.env` file inside `/opt/manta-trading`** — configuration reaches
        services only through `/etc/manta-trading.env`
  - [ ] `MT_TIMESCALE_MAINTENANCE_URL` is absent from the env file
  - [ ] No credential appears in any tracked file (grep the repo)
  - [ ] `systemctl list-unit-files 'mt-*'` shows all units **disabled**;
        `systemctl list-timers 'mt-*'` is empty
  - [ ] Success: every check above holds. Production is still running by hand
        from the dev checkout, unchanged

---

## Group E — Prove one pass under systemd, before cutover

Effort: 1/5.

- [ ] **E.1 [PM] Run one daily pass through its unit**
  - [ ] First check `acquisition_state.last_daily_cycle_end_utc`. If it is more
        than 30 minutes ago and the clock is past 00:30 UTC, the pass starts
        working immediately; otherwise it sleeps out the gate, at most 30
        minutes, restating the remaining time every 5 minutes
  - [ ] `sudo systemctl start mt-daily-pass.service`, then
        `journalctl -u mt-daily-pass.service -f`
  - [ ] Success: normal pass output; the unit ends `inactive (dead)` with
        `status=0/SUCCESS`

- [ ] **E.2 [agent] Confirm the supervised pass matches a manual one**
  - [ ] Same log shape as today's by-hand invocation, and the same
        `acquisition_state` effect, now running as `manta-trading` from `/opt`
  - [ ] **If E.1 exited "no actionable work"**, that is a valid pass but a weak
        comparison — it never touched the fetch path. Do not wait for one; run
        E.3 instead and compare there. The minute gate is one minute rather than
        thirty, so a minute pass almost always has real work
  - [ ] Confirm the journal identifies it by `_SYSTEMD_UNIT`, which is what
        distinguishes supervised runs from manual ones
  - [ ] Success: no behavioral difference beyond user and working directory.
        **A difference here stops the cutover** — investigate before Group F

- [ ] **E.3 [PM] Run one minute pass through its unit**
  - [ ] `sudo systemctl start mt-minute-pass.service`
  - [ ] Success: as E.1

- [ ] **E.4 [agent] Confirm the minute pass as in E.2**

---

## Group F — Cutover

Effort: 1/5, risk concentrated here. This is the one explicit step.

- [ ] **F.1 [PM] Enable the timers and the API server**
  - [ ] `sudo systemctl enable --now mt-daily-pass.timer mt-minute-pass.timer
        mt-serve.service`
  - [ ] From this instant production is the `/opt` install. Operator practice
        changes: passes and `mt serve` are no longer started by hand
  - [ ] Success: the command exits 0

- [ ] **F.2 [agent] Verify the timers and the API**
  - [ ] `systemctl list-timers 'mt-*'` shows both timers with sane next-fire
        times consistent with the design's schedule
  - [ ] `systemctl is-active mt-serve.service` is `active`; the API answers on
        its configured port
  - [ ] Success: both checks pass

- [ ] **F.3 [agent] Verify journald caps and the acquisition slice**
  - [ ] The 2 GiB / 200 MiB caps are in effect (`journalctl --header` or a
        disk-usage check)
  - [ ] `systemctl status manta-acquisition.slice` shows the pass units as its
        members when one runs
  - [ ] Success: caps confirmed; units are in the slice

---

## Group G — Prove supervision, pause, and rollback

Effort: 2/5. All **[PM]** for the acting steps; verification is **[agent]**.

- [ ] **G.1 [PM] Crash supervision**
  - [ ] `sudo kill -9 "$(systemctl show -p MainPID --value mt-serve.service)"`
  - [ ] Success: `systemctl status mt-serve.service` shows it active again with
        the restart count incremented

- [ ] **G.2 [PM] Clean stop of a running pass**
  - [ ] Start a pass by hand (`systemctl start mt-daily-pass.service`), then
        `systemctl stop mt-daily-pass.service` while it is running
  - [ ] Success: the journal shows the runner's clean-exit path, **not**
        `Killed` or `signal=KILL`; `acquisition_state` shows a resumable
        position. A SIGKILL here means `TimeoutStopSec` is too low — fix A.2/A.4

- [ ] **G.3 [PM] Pause one source**
  - [ ] `sudo systemctl disable --now mt-minute-pass.timer`
  - [ ] Success: `systemctl list-timers 'mt-*'` no longer lists it; daily is
        still listed and `mt-serve` still active

- [ ] **G.4 [PM] Reboot survival, with the pause still in force**
  - [ ] PM-scheduled moment, executed immediately — this is not a wait
  - [ ] Prefer a moment when no pass is mid-flight, but do not wait for one:
        an interrupted pass resumes where it stopped (slice 912), which is
        exactly the property the reboot is testing
  - [ ] `sudo reboot`
  - [ ] Success after boot, with **no operator action**: `mt-serve` is active,
        the daily timer is back in `list-timers`, and the paused minute timer is
        **still absent**. `Persistent=true` fires any daily schedule missed while
        the host was down

- [ ] **G.5 [PM] Resume the paused source**
  - [ ] `sudo systemctl enable --now mt-minute-pass.timer`
  - [ ] Success: the timer returns to `list-timers`, and the `Persistent=true`
        catch-up pass fires immediately — the documented behavior from C.5

- [ ] **G.6 [PM] Rollback rehearsal**
  - [ ] `sudo systemctl disable --now mt-daily-pass.timer mt-minute-pass.timer
        mt-serve.service`
  - [ ] Run one pass manually from the dev checkout, exactly as before this slice
  - [ ] Re-enable the three
  - [ ] Success: rollback works, the dev checkout was never modified, and
        re-enabling restores the supervised state

- [ ] **G.7 [agent] Record the measured results of G.1–G.6**
  - [ ] Restart counts, timer states before and after the reboot, the clean-stop
        journal line, and the rollback outcome
  - [ ] Success: each success criterion in the design is answered with an
        observation, not an assertion

---

## Group H — Close the slice

Effort: 1/5. All **[agent]**.

- [ ] **H.1 [agent] Fold the measured results into the runbook**
  - [ ] `production-deploy.md` gains anything Groups D–G proved that the draft
        got wrong or left vague. A correction found while running belongs in both
        the runbook and this task file
  - [ ] Success: the runbook describes what actually happened on the host

- [ ] **H.2 [agent] Refine the design's verification walkthrough**
  - [ ] Replace the draft walkthrough with the commands as actually run,
        including anything that needed a correction
  - [ ] Success: the walkthrough is reproducible by someone who was not present

- [ ] **H.3 [agent] Update slice status and the plan entry**
  - [ ] Design frontmatter `status: complete`; slice-plan entry 17 checked off
        with its outcome recorded
  - [ ] Success: no document still describes this slice as open

- [ ] **H.4 [agent] Final commit**
