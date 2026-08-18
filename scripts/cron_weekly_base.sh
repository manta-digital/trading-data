#!/usr/bin/env bash
# cron_weekly_base.sh — cron glue for the weekly base-backup tier (9.1).
#
# Refuses to run while the archive-health flag exists: taking a new base
# backup while WAL archiving is broken would silently produce a backup with
# no PITR continuity behind it (5.2). On success, prunes base backups and the
# WAL archive to the retention window (4.5).
#
# Usage:
#   cron_weekly_base.sh --env-file <path> --base-dir <dir> --wal-dir <dir> \
#                       --keep-days <n> --health-flag <path>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
  echo "usage: $0 --env-file <path> --base-dir <dir> --wal-dir <dir> --keep-days <n> --health-flag <path>" >&2
}

ENV_FILE=""; BASE_DIR=""; WAL_DIR=""; KEEP_DAYS=""; HEALTH_FLAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file)    ENV_FILE="${2:-}"; shift 2 ;;
    --base-dir)    BASE_DIR="${2:-}"; shift 2 ;;
    --wal-dir)     WAL_DIR="${2:-}"; shift 2 ;;
    --keep-days)   KEEP_DAYS="${2:-}"; shift 2 ;;
    --health-flag) HEALTH_FLAG="${2:-}"; shift 2 ;;
    *) echo "error: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done
[ -n "$ENV_FILE" ] || { echo "error: --env-file is required" >&2; usage; exit 2; }
[ -n "$BASE_DIR" ] || { echo "error: --base-dir is required" >&2; usage; exit 2; }
[ -n "$WAL_DIR" ] || { echo "error: --wal-dir is required" >&2; usage; exit 2; }
[ -n "$KEEP_DAYS" ] || { echo "error: --keep-days is required" >&2; usage; exit 2; }
[ -n "$HEALTH_FLAG" ] || { echo "error: --health-flag is required" >&2; usage; exit 2; }

if [ -e "$HEALTH_FLAG" ]; then
  echo "error: $HEALTH_FLAG exists — WAL archiving is unhealthy; refusing to take a base backup until it is fixed (see runbook)" >&2
  exit 1
fi

DB_URL=$(grep '^MT_TIMESCALE_MAINTENANCE_URL' "$ENV_FILE" | sed 's/^[^=]*=//' | tr -d '"')
BUCKET=$(grep '^MT_BACKUP_S3_BUCKET' "$ENV_FILE" | sed 's/^[^=]*=//' | tr -d '"')
[ -n "$DB_URL" ] || { echo "error: MT_TIMESCALE_MAINTENANCE_URL not in $ENV_FILE" >&2; exit 1; }
[ -n "$BUCKET" ] || { echo "error: MT_BACKUP_S3_BUCKET not in $ENV_FILE" >&2; exit 1; }

# Replication is admitted from localhost only.
DB_URL="${DB_URL/@192.168.1.144:/@127.0.0.1:}"

STAMP=$(date +%Y%m%d)
echo "=== weekly base backup run: $(date -Is) ==="
"$SCRIPT_DIR/backup_prod.sh" --db-url "$DB_URL" --dest "$BASE_DIR/$STAMP" \
  --remote "b2:$BUCKET/base/$STAMP"
"$SCRIPT_DIR/prune_wal_archive.sh" --base-dir "$BASE_DIR" --wal-dir "$WAL_DIR" \
  --keep-days "$KEEP_DAYS"
echo "=== weekly base backup done: $(date -Is) ==="
