#!/usr/bin/env bash
# sync_wal_offsite.sh — every rclone operation on the WAL archive's offsite
# copy, serialised by one lock (slice 920, D4).
#
# Default (the hourly push): `rclone copy` of --wal-dir to --remote, additive
# only, skipping files younger than MIN_AGE and every *.tmp (the archiver may
# still be writing them). On success touches --stamp; the health check reads
# its age (offsite_wal_stale). On failure: exit non-zero, stamp untouched, one
# line here and one `logger -t manta-backup` line. No retry — the next run is
# the retry.
#
# --verify: `rclone check --one-way` with the same filters instead (nothing
# written, stamp untouched). --sync-max-delete <n>: `rclone sync` with
# `--max-delete <n>` instead — the weekly reconcile's mirror step, refused by
# rclone if more than n deletions would be needed. Both exist here so the
# filters that define "what belongs offsite" are written once.
#
# Lock: `flock -n` on --lock; if held, prints `skipped: previous run active`
# and exits 0 (the cron case). --lock-wait <min> waits instead (the weekly
# job's case). The whole rclone call runs under `timeout --timeout` minutes so
# a hung endpoint can never queue waiting processes.
#
# Usage:
#   sync_wal_offsite.sh --wal-dir <dir> --remote <rclone-path> --stamp <file> \
#       --timeout <minutes> --lock <file> [--lock-wait <minutes>] \
#       [--verify | --sync-max-delete <n>]
set -euo pipefail

MIN_AGE=2m
BWLIMIT=""          # e.g. "8M"; empty = no limit. Set if uplink contention shows.
LOGGER_TAG=manta-backup
SKIP_MESSAGE="skipped: previous run active"

usage() {
  echo "usage: $0 --wal-dir <dir> --remote <rclone-path> --stamp <file> --timeout <minutes> --lock <file> [--lock-wait <minutes>] [--verify | --sync-max-delete <n>]" >&2
}
die() { echo "error: $*" >&2; exit "${2:-1}"; }

WAL_DIR=""; REMOTE=""; STAMP=""; TIMEOUT_MIN=""; LOCK=""; LOCK_WAIT_MIN=""; MODE=copy; MAX_DELETE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --wal-dir)         WAL_DIR="${2:-}"; shift 2 ;;
    --remote)          REMOTE="${2:-}"; shift 2 ;;
    --stamp)           STAMP="${2:-}"; shift 2 ;;
    --timeout)         TIMEOUT_MIN="${2:-}"; shift 2 ;;
    --lock)            LOCK="${2:-}"; shift 2 ;;
    --lock-wait)       LOCK_WAIT_MIN="${2:-}"; shift 2 ;;
    --verify)          MODE=check; shift ;;
    --sync-max-delete) MODE=sync; MAX_DELETE="${2:-}"; shift 2 ;;
    *) usage; die "unknown argument: $1" 2 ;;
  esac
done
for pair in "--wal-dir:$WAL_DIR" "--remote:$REMOTE" "--stamp:$STAMP" "--timeout:$TIMEOUT_MIN" "--lock:$LOCK"; do
  [ -n "${pair#*:}" ] || { usage; die "${pair%%:*} is required" 2; }
done
for num in "--timeout:$TIMEOUT_MIN" "--lock-wait:${LOCK_WAIT_MIN:-0}" "--sync-max-delete:${MAX_DELETE:-0}"; do
  case "${num#*:}" in ''|*[!0-9]*) usage; die "${num%%:*} must be a whole number: ${num#*:}" 2 ;; esac
done
[ "$MODE" != sync ] || [ -n "$MAX_DELETE" ] || { usage; die "--sync-max-delete needs a number" 2; }
[ -d "$WAL_DIR" ] || die "not a directory: $WAL_DIR"

exec 9>>"$LOCK"
if [ -n "$LOCK_WAIT_MIN" ]; then
  flock -w $((LOCK_WAIT_MIN * 60)) 9 || die "lock $LOCK still held after ${LOCK_WAIT_MIN} min"
elif ! flock -n 9; then
  echo "$SKIP_MESSAGE"
  exit 0
fi

FILTERS=(--min-age "$MIN_AGE" --exclude '*.tmp')
[ -z "$BWLIMIT" ] || FILTERS+=(--bwlimit "$BWLIMIT")
case "$MODE" in
  copy)  RCLONE=(rclone copy "$WAL_DIR" "$REMOTE" "${FILTERS[@]}") ;;
  check) RCLONE=(rclone check "$WAL_DIR" "$REMOTE" --one-way "${FILTERS[@]}") ;;
  sync)  RCLONE=(rclone sync "$WAL_DIR" "$REMOTE" --max-delete "$MAX_DELETE" "${FILTERS[@]}") ;;
esac

echo "wal offsite $MODE: $WAL_DIR -> $REMOTE ($(date -Is))"
rc=0
timeout "${TIMEOUT_MIN}m" "${RCLONE[@]}" || rc=$?
if [ "$rc" -ne 0 ]; then
  msg="wal offsite $MODE FAILED (rclone exit $rc) $WAL_DIR -> $REMOTE"
  echo "$msg" >&2
  logger -t "$LOGGER_TAG" "$msg"
  exit "$rc"
fi
[ "$MODE" != copy ] || touch "$STAMP"
echo "wal offsite $MODE done ($(date -Is))"
