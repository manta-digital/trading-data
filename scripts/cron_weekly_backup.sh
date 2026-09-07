#!/usr/bin/env bash
# cron_weekly_backup.sh — cron.d glue for the weekly base backup and the
# guarded offsite reconcile (slice 920, D4; replaces the 915 weekly glue).
#
# Refuses to run while the archive-health flag exists (a base backup taken on
# a suspect chain is what the gate prevents). Then, in an order that makes it
# impossible for a lagging push to let the local prune delete a segment that
# was never uploaded:
#   base backup (backup_prod.sh, with its own offsite copy)
#   1. catch-up push of the WAL directory      (sync_wal_offsite.sh)
#   2. checksum check, abort on any difference (sync_wal_offsite.sh --verify)
#   3. local prune, capturing its PRUNED line  (prune_wal_archive.sh)
#   4. reconcile_guards.sh — refuse loudly on any failed guard
#   5. ONLY IF --armed exists: rclone sync with --max-delete (pruned wal count
#      + MAX_DELETE_MARGIN), then purge each offsite base/<date> absent
#      locally, logging every removal. Otherwise log `reconcile skipped: not
#      armed` and exit 0 — the destructive step never runs unwatched.
#   6. final checksum check.
#
# Usage:
#   cron_weekly_backup.sh --env-file <path> --backup-root <dir> --base-dir <dir> \
#       --wal-dir <dir> --keep-days <n> --health-flag <path> --remote-wal <rclone-path> \
#       --remote-base <rclone-path> --stamp <file> --lock <file> --armed <file> [--skip-base-backup]
#
# --skip-base-backup runs only the reconcile (steps 1-6): for the scratch-
# prefix rehearsal and the runbook's reconcile drill, never from cron.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../deploy/lib/env_value.sh
. "$SCRIPT_DIR/../deploy/lib/env_value.sh"
# Sibling tools resolve by name, with this script's own directory as the last
# place searched, so a stub earlier on PATH stands in for them under test.
PATH="$PATH:$SCRIPT_DIR"

# A discrepancy larger than the prune explains fails the sync instead of deleting.
MAX_DELETE_MARGIN=50
# The push skips segments younger than this; a check must skip everything
# younger than (its push's start + this), or a segment archived mid-run is a
# false "missing" (observed 2026-09-07: every weekly run would have aborted).
PUSH_MIN_AGE_SEC=120
# The catch-up push may have a week of segments to move; the hourly push's
# lock is waited for, not skipped.
CATCHUP_TIMEOUT_MIN=180
LOCK_WAIT_MIN=60
LOGGER_TAG=manta-backup

usage() {
  echo "usage: $0 --env-file <path> --backup-root <dir> --base-dir <dir> --wal-dir <dir> --keep-days <n> --health-flag <path> --remote-wal <rclone-path> --remote-base <rclone-path> --stamp <file> --lock <file> --armed <file> [--skip-base-backup]" >&2
}
die() { echo "error: $*" >&2; logger -t "$LOGGER_TAG" "weekly backup: $*"; exit "${2:-1}"; }

ENV_FILE=""; BACKUP_ROOT=""; BASE_DIR=""; WAL_DIR=""; KEEP_DAYS=""; HEALTH_FLAG=""
REMOTE_WAL=""; REMOTE_BASE=""; STAMP=""; LOCK=""; ARMED=""; SKIP_BASE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file)    ENV_FILE="${2:-}"; shift 2 ;;
    --backup-root) BACKUP_ROOT="${2:-}"; shift 2 ;;
    --base-dir)    BASE_DIR="${2:-}"; shift 2 ;;
    --wal-dir)     WAL_DIR="${2:-}"; shift 2 ;;
    --keep-days)   KEEP_DAYS="${2:-}"; shift 2 ;;
    --health-flag) HEALTH_FLAG="${2:-}"; shift 2 ;;
    --remote-wal)  REMOTE_WAL="${2:-}"; shift 2 ;;
    --remote-base) REMOTE_BASE="${2:-}"; shift 2 ;;
    --stamp)       STAMP="${2:-}"; shift 2 ;;
    --lock)        LOCK="${2:-}"; shift 2 ;;
    --armed)       ARMED="${2:-}"; shift 2 ;;
    --skip-base-backup) SKIP_BASE=1; shift ;;
    *) usage; echo "error: unknown argument: $1" >&2; exit 2 ;;
  esac
done
for pair in "--env-file:$ENV_FILE" "--backup-root:$BACKUP_ROOT" "--base-dir:$BASE_DIR" "--wal-dir:$WAL_DIR" \
            "--keep-days:$KEEP_DAYS" "--health-flag:$HEALTH_FLAG" "--remote-wal:$REMOTE_WAL" \
            "--remote-base:$REMOTE_BASE" "--stamp:$STAMP" "--lock:$LOCK" "--armed:$ARMED"; do
  [ -n "${pair#*:}" ] || { usage; echo "error: ${pair%%:*} is required" >&2; exit 2; }
done

if [ -e "$HEALTH_FLAG" ]; then
  die "$HEALTH_FLAG exists — WAL archiving is unhealthy; refusing to take a base backup until it is fixed (see runbook)"
fi

DB_URL=$(env_value "$ENV_FILE" MT_TIMESCALE_MAINTENANCE_URL)
[ -n "$DB_URL" ] || die "MT_TIMESCALE_MAINTENANCE_URL not in $ENV_FILE"

# Replication is admitted from localhost only.
DB_URL="${DB_URL/@192.168.1.144:/@127.0.0.1:}"

WAL_SYNC=(sync_wal_offsite.sh --wal-dir "$WAL_DIR" --remote "$REMOTE_WAL" --stamp "$STAMP" \
          --timeout "$CATCHUP_TIMEOUT_MIN" --lock "$LOCK" --lock-wait "$LOCK_WAIT_MIN")

DATE_STAMP=$(date +%Y%m%d)
echo "=== weekly backup run: $(date -Is) ==="
if [ "$SKIP_BASE" -eq 1 ]; then
  echo "--- base backup skipped (--skip-base-backup: reconcile only)"
else
  backup_prod.sh --db-url "$DB_URL" --dest "$BASE_DIR/$DATE_STAMP" --remote "$REMOTE_BASE/$DATE_STAMP" \
    || die "base backup failed (backup_prod.sh exit $?); nothing pruned or reconciled"
fi

# check_since <epoch>: verify offsite against everything older than the push
# that started at <epoch>.
check_since() { "${WAL_SYNC[@]}" --verify --min-age "$(( $(date +%s) - $1 + PUSH_MIN_AGE_SEC ))s"; }

echo "--- 1. catch-up push"
PUSH_START=$(date +%s)
"${WAL_SYNC[@]}"
echo "--- 2. offsite check before prune"
check_since "$PUSH_START" || die "offsite WAL differs from local before prune; not pruning (see rclone output above)"
echo "--- 3. local prune"
PRUNE_OUTPUT=$(prune_wal_archive.sh --base-dir "$BASE_DIR" --wal-dir "$WAL_DIR" --keep-days "$KEEP_DAYS") \
  || die "prune failed (prune_wal_archive.sh exit $?); not reconciling"
echo "$PRUNE_OUTPUT"
PRUNED_LINE=$(grep '^PRUNED wal=[0-9]* base=[0-9]*$' <<< "$PRUNE_OUTPUT" | tail -1)
[ -n "$PRUNED_LINE" ] || die "prune printed no PRUNED line; not reconciling"
PRUNED_WAL=${PRUNED_LINE#PRUNED wal=}; PRUNED_WAL=${PRUNED_WAL%% *}
echo "--- 4. reconcile guards"
reconcile_guards.sh --db-url "$DB_URL" --backup-root "$BACKUP_ROOT" --wal-dir "$WAL_DIR" --base-dir "$BASE_DIR" \
  || die "reconcile guards refused; offsite left untouched"

if [ ! -e "$ARMED" ]; then
  echo "reconcile skipped: not armed ($ARMED absent; create it after watching a reconcile by hand — see runbook)"
  echo "=== weekly backup done (unarmed): $(date -Is) ==="
  exit 0
fi

echo "--- 5. offsite mirror (armed; max-delete $((PRUNED_WAL + MAX_DELETE_MARGIN)))"
SYNC_START=$(date +%s)
"${WAL_SYNC[@]}" --sync-max-delete $((PRUNED_WAL + MAX_DELETE_MARGIN))
mapfile -t OFFSITE_BASES < <(rclone lsf --dirs-only "$REMOTE_BASE" | tr -d / | grep -E '^[0-9]{8}$' || true)
for b in "${OFFSITE_BASES[@]}"; do
  if [ ! -d "$BASE_DIR/$b" ]; then
    echo "removing offsite base $REMOTE_BASE/$b (absent locally)"
    rclone purge "$REMOTE_BASE/$b"
    logger -t "$LOGGER_TAG" "weekly backup: removed offsite base $REMOTE_BASE/$b"
  fi
done
echo "--- 6. final offsite check"
check_since "$SYNC_START" || die "offsite WAL differs from local after reconcile"
echo "=== weekly backup done: $(date -Is) ==="
