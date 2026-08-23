---
docType: notes
project: trading-data
slice: 916-supervised-production-services
dateCreated: 20260823
dateUpdated: 20260823
status: complete
---

# Slice 916 — measured verification results (Groups D–G)

Every success criterion answered with an observation, not an assertion.
All times MDT (UTC-6) unless suffixed. Host: manta9000.

## Install and cutover

- Install script ran 4 times total against the host (refs bf0eb0c → 942b542 →
  21514bdd → prod-20260823); every re-run took the exists-and-matches paths.
  A transient network outage during the first run (uv python download timeout,
  then DNS failure) was recovered exactly as designed: fix nothing, re-run.
  It also surfaced two real fixes: `UV_PYTHON_INSTALL_DIR` escaping the
  checkout, and the dirty-guard interaction (942b542).
- Different-ref checkout path exercised for real (bf0eb0c → 942b542).
- Idempotence (crit. 13): second run exit 0, no account recreated, env file
  byte-identical (sha256 printed both runs), nothing enabled.
- Cutover was the single `systemctl enable --now mt-daily-pass.timer
  mt-minute-pass.timer mt-serve.service` at 2026-08-23 05:52.

## The 13 success criteria

1. **Timers enabled, correct next-fires** — `list-timers` showed 12:35 UTC
   daily / 13:05 UTC minute after cutover, and post-reboot 18:35/19:05 local.
2. **kill -9 → auto-restart** — G.1: killed PID 2321611 at 05:54:3x; systemd
   restarted as PID 2323323 within ~10s (RestartSec); NRestarts 0→1; API
   answered immediately after.
3. **Reboot, no operator action** — proven TWICE. Boot 05:59:43: mt-serve
   active at 05:59:55 (12s into boot), daily timer listed, paused minute timer
   correctly still absent. Boot 09:32:46: mt-serve active at 09:32:58, both
   timers listed. Zero operator action either time.
4. **systemctl answers "is production acquiring?"** — `list-timers` carries
   LAST/NEXT per pass; `mt-run status` (added this slice) shows running state,
   latest output, last result without any SQL.
5. **Supervised pass ≡ manual pass** — E.1/E.2: full daily cycle under the
   unit (47min wall, 5.6min CPU, 373MB peak, 10,093 acquisition_state rows,
   normal outcome mix 9759 success / 318 partial / 15 empty); identical log
   shape; `_UID=997`, `_CMDLINE=/opt/.../mt data daemon run --daily
   --stop-when-done`. First autonomous firing (timer, not human): 06:35:07,
   completed 07:09:06, 34min, success.
6. **journald caps hold** — journal plateaued at 1.9–2.0G under the 2G
   SystemMaxUse through a 10h pass that logged ~7,900 fetch lines.
7. **Env file** — 0640 root:manta-trading verified; no active
   MT_TIMESCALE_MAINTENANCE_URL line (anchored grep = 0); no credential value
   in any tracked file (grepped by value); credentials later group-readable to
   operator via manta-trading group membership (no new exposure — operator
   already holds them in the dev .env).
8. **Runbook closed** — no "Future target" / "Not yet documented" remain;
   gained quick-reference, add-a-source, pause/resume, and the runbooks index.
9. **Rollback rehearsed** — G.6: disabled all three, manual pass from the
   never-modified dev checkout worked as before the slice, re-enable restored
   supervision (timers re-listed, serve active).
10. **serve.py help** — no slice reference; names mt-serve unit; test proves
    both directions.
11. **Pause survives reboot** — G.3/G.4: `disable --now mt-minute-pass.timer`
    removed it from list-timers; after reboot daily+serve returned, minute
    stayed absent until explicit re-enable.
12. **Clean SIGTERM stop** — proven twice. 10h14m minute pass: SIGTERM →
    "received signal 15 — initiating clean exit" → exit in ~1s, success, no
    SIGKILL. G.2 daily pass stopped mid-fetch: same clean path, exit <100ms
    after signal, resumable state.
13. **Idempotent re-run** — see Install above.

## G.5 — resume fires catch-up (crit. 11 second half)

`enable --now` at 08:08:40 with the 07:05 elapse missed while paused:
catch-up pass started the same second (LastTrigger 08:08:40). The pass
correctly degraded gap-discovery because `minute_4hour_ohlcv` lags —
pre-existing data-backlog condition (raw minute data ends ~Aug 14 for most
symbols; refresh jobs healthy, hourly, 0 failures). Self-heals as timers
drain the backfill. Triage rule recorded in runbooks/__readme.md.

## Found and fixed during verification

- `systemctl start` on oneshot blocks silently → `mt-run` front-end (start
  with live output, Ctrl-C detaches, status/follow, passthrough to
  production's mt CLI, auto-sudo fallback).
- eodhd_sync logged full URLs including api_token into journald → redaction
  at all log/exception sites + tests. NOTE: pre-fix warnings from the E.3
  night remain in the journal; consider EODHD key rotation and/or
  `journalctl --vacuum-time=` — PM decision, not done.
- uv managed-python dir would have landed inside /opt checkout and tripped
  the dirty guard → UV_PYTHON_INSTALL_DIR to /var/cache/manta-trading.
- Hex SHAs rejected for deploys → readable tags (prod-20260823); versioned
  v-tags adopted as the go-forward procedure.
- Runbooks renamed to indexed scheme with __readme.md index.
- Bluetooth-at-boot (host issue, not slice): `#AutoEnable=true` in
  /etc/bluetooth/main.conf identified; fix applied by PM at second reboot.

## Deferred / follow-ups

- Cross-source arbitration & slice resource weights — Future Work 4 in
  900-slices.foundation-cleanup.md (unchanged).
- EODHD key rotation + journal vacuum decision (above).
- minute cagg lag ERROR noise until backfill drains — expected, self-healing.
