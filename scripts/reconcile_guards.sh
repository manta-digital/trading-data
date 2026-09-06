#!/usr/bin/env bash
# reconcile_guards.sh — the refusal contract before any offsite mirror
# (slice 920, D4).
#
# `rclone sync` mirrors its source unconditionally, so it must never run
# against anything but the live, complete archive. Four guards, any failure
# prints `reconcile refused: <reason>` and exits 1:
#   1. the backup root is on a mounted filesystem, not the root filesystem
#      (an unmounted /data leaves an empty or stale directory in its place);
#   2. the WAL directory is non-empty;
#   3. the segment pg_stat_archiver.last_archived_wal names exists locally,
#      raw or .zst (proves this is the live archive, not a stale copy);
#   4. the oldest retained base backup's manifest start segment exists
#      locally (proves the prune kept what it said it kept).
# All pass: prints `reconcile guards passed`, exit 0. Read-only.
#
# Usage:
#   reconcile_guards.sh --db-url <url> --backup-root <dir> --wal-dir <dir> --base-dir <dir>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_FS_TARGET=/

usage() {
  echo "usage: $0 --db-url <url> --backup-root <dir> --wal-dir <dir> --base-dir <dir>" >&2
}
die() { echo "error: $*" >&2; exit "${2:-1}"; }
refuse() { echo "reconcile refused: $*"; exit 1; }

DB_URL=""; BACKUP_ROOT=""; WAL_DIR=""; BASE_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --db-url)      DB_URL="${2:-}"; shift 2 ;;
    --backup-root) BACKUP_ROOT="${2:-}"; shift 2 ;;
    --wal-dir)     WAL_DIR="${2:-}"; shift 2 ;;
    --base-dir)    BASE_DIR="${2:-}"; shift 2 ;;
    *) usage; die "unknown argument: $1" 2 ;;
  esac
done
for pair in "--db-url:$DB_URL" "--backup-root:$BACKUP_ROOT" "--wal-dir:$WAL_DIR" "--base-dir:$BASE_DIR"; do
  [ -n "${pair#*:}" ] || { usage; die "${pair%%:*} is required" 2; }
done

# A segment is "present" in either archive shape.
segment_present() { [ -f "$WAL_DIR/$1" ] || [ -f "$WAL_DIR/$1.zst" ]; }

# 1. mounted filesystem
[ -d "$BACKUP_ROOT" ] || refuse "backup root $BACKUP_ROOT is not a directory"
MOUNT_TARGET=$(findmnt -n -o TARGET --target "$BACKUP_ROOT" 2>/dev/null || true)
[ -n "$MOUNT_TARGET" ] || refuse "cannot determine the filesystem holding $BACKUP_ROOT"
[ "$MOUNT_TARGET" != "$ROOT_FS_TARGET" ] || refuse "$BACKUP_ROOT is on the root filesystem (mount target $MOUNT_TARGET) — is the data disk mounted?"

# 2. non-empty archive
[ -d "$WAL_DIR" ] || refuse "WAL directory $WAL_DIR is not a directory"
[ -n "$(find "$WAL_DIR" -maxdepth 1 -type f -print -quit)" ] || refuse "WAL directory $WAL_DIR is empty"

# 3. the archiver's last segment is here
LAST_ARCHIVED=$(psql "$DB_URL" -X -At -v ON_ERROR_STOP=1 -c "SELECT COALESCE(last_archived_wal, '') FROM pg_stat_archiver;" 2>&1) \
  || refuse "cannot read pg_stat_archiver: $(paste -sd ' ' <<< "$LAST_ARCHIVED")"
[ -n "$LAST_ARCHIVED" ] || refuse "pg_stat_archiver.last_archived_wal is NULL — nothing has been archived yet"
segment_present "$LAST_ARCHIVED" || refuse "last archived segment $LAST_ARCHIVED is not in $WAL_DIR — this is not the live archive"

# 4. the oldest retained backup's first needed segment is here
OLDEST=$(find "$BASE_DIR" -maxdepth 1 -mindepth 1 -type d -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]' -printf '%f\n' 2>/dev/null | sort | head -1 || true)
[ -n "$OLDEST" ] || refuse "no dated base backup under $BASE_DIR"
MANIFEST="$BASE_DIR/$OLDEST/backup_manifest"
[ -r "$MANIFEST" ] || refuse "cannot read $MANIFEST"
read -r TLI START_LSN < <(jq -r '.["WAL-Ranges"][0] | "\(.Timeline) \(.["Start-LSN"])"' "$MANIFEST")
START_SEGMENT=$("$SCRIPT_DIR/wal_segment_name.py" from-lsn "$TLI" "$START_LSN")
segment_present "$START_SEGMENT" || refuse "base backup $OLDEST needs WAL from $START_SEGMENT, which is not in $WAL_DIR"

echo "reconcile guards passed"
