#!/usr/bin/env bash
# verify_tick_archive_backup.sh — prove the tick archive is in the nightly
# restic backup: back up once, count, restore one file, compare (slice 223,
# LLD 224 Technical Decision 11, "Proof, now").
#
# Run as root (restic reads root-owned paths in the include set). Checks
# before it acts, prints the expected and observed value at each step, and
# appends everything to --log. Steps:
#   1. MT_TICK_ARCHIVE_DIR (read from --env-file, never sourced) is set, is a
#      directory holding at least one data file, and is in INCLUDE_PATHS of
#      scripts/cron_system_backup.sh;
#   2. the restore directory /data/restore-test/tick-archive does not exist;
#   3. run cron_system_backup.sh once with the cron line's arguments;
#   4. `restic ls latest <archive>` file count == the archive's own file count,
#      .partial files excluded;
#   5. restore one data file into the restore directory (created here);
#   6. SHA-256 of the restored file == the original's;
#   7. remove only the directories this script created.
#
# Usage:
#   sudo verify_tick_archive_backup.sh --env-file <path> --repo-prefix <name> \
#       --exclude-file <path> --stamp <file> --lock <file> --backup-log <file> \
#       --log <file>
# The first five and --backup-log are cron_system_backup.sh's arguments as the
# root cron line passes them (--backup-log is its --log).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_SCRIPT="$SCRIPT_DIR/cron_system_backup.sh"
RESTIC_REPO_LIB="$SCRIPT_DIR/../deploy/lib/restic_repo.sh"
ENV_VALUE_LIB="$SCRIPT_DIR/../deploy/lib/env_value.sh"
ARCHIVE_ENV=MT_TICK_ARCHIVE_DIR
RESTORE_PARENT=/data/restore-test
RESTORE_ROOT="$RESTORE_PARENT/tick-archive"
PARTIAL_GLOB='*.partial'
DATA_GLOB='*.dbn.zst'

usage() {
  sed -n '/^# Usage:/,/^# root cron line/p' "$0" | sed 's/^# \{0,1\}//'
}

ENV_FILE=""; REPO_PREFIX=""; EXCLUDE_FILE=""; STAMP=""; LOCK=""; BACKUP_LOG=""; LOG=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)      usage; exit 0 ;;
    --env-file)     ENV_FILE="${2:-}"; shift 2 ;;
    --repo-prefix)  REPO_PREFIX="${2:-}"; shift 2 ;;
    --exclude-file) EXCLUDE_FILE="${2:-}"; shift 2 ;;
    --stamp)        STAMP="${2:-}"; shift 2 ;;
    --lock)         LOCK="${2:-}"; shift 2 ;;
    --backup-log)   BACKUP_LOG="${2:-}"; shift 2 ;;
    --log)          LOG="${2:-}"; shift 2 ;;
    *) usage >&2; echo "error: unknown argument: $1" >&2; exit 2 ;;
  esac
done
for pair in "--env-file:$ENV_FILE" "--repo-prefix:$REPO_PREFIX" \
    "--exclude-file:$EXCLUDE_FILE" "--stamp:$STAMP" "--lock:$LOCK" \
    "--backup-log:$BACKUP_LOG" "--log:$LOG"; do
  [ -n "${pair#*:}" ] || { usage >&2; echo "error: ${pair%%:*} is required" >&2; exit 2; }
done
[ "$(id -u)" -eq 0 ] || { echo "error: run as root (sudo)" >&2; exit 2; }

exec > >(tee -a "$LOG") 2>&1
echo "=== tick archive backup verify: $(date -Is) ==="

fail() { echo "FAIL: $*"; exit 1; }
step() { echo "--- $*"; }
compare() {  # compare <what> <expected> <observed>
  echo "    $1: expected $2, observed $3"
  [ "$2" = "$3" ] || fail "$1 differs"
}

# shellcheck source=SCRIPTDIR/../deploy/lib/env_value.sh
. "$ENV_VALUE_LIB"

step "1. archive setting and include set"
ARCHIVE=$(env_value "$ENV_FILE" "$ARCHIVE_ENV")
[ -n "$ARCHIVE" ] || fail "$ARCHIVE_ENV is not set in $ENV_FILE"
[ -d "$ARCHIVE" ] || fail "$ARCHIVE_ENV=$ARCHIVE is not a directory"
INCLUDED=$(grep '^INCLUDE_PATHS=' "$BACKUP_SCRIPT" | tr -d '()' | cut -d= -f2)
echo "    $ARCHIVE_ENV=$ARCHIVE; INCLUDE_PATHS: $INCLUDED"
case " $INCLUDED " in
  *" $ARCHIVE "*) echo "    in the include set: yes" ;;
  *) fail "$ARCHIVE is not in INCLUDE_PATHS of $BACKUP_SCRIPT" ;;
esac
LOCAL_COUNT=$(find "$ARCHIVE" -type f ! -name "$PARTIAL_GLOB" | wc -l)
SAMPLE=$(find "$ARCHIVE" -type f -name "$DATA_GLOB" | sort | head -1)
[ -n "$SAMPLE" ] || fail "no data file ($DATA_GLOB) under $ARCHIVE to restore"
echo "    archive files (no .partial): $LOCAL_COUNT; sample: $SAMPLE"

step "2. restore directory is free"
[ ! -e "$RESTORE_ROOT" ] || fail "$RESTORE_ROOT exists; remove it or inspect it first"
echo "    $RESTORE_ROOT: absent (expected absent)"

step "3. one backup run"
"$BACKUP_SCRIPT" --env-file "$ENV_FILE" --repo-prefix "$REPO_PREFIX" \
  --exclude-file "$EXCLUDE_FILE" --stamp "$STAMP" --log "$BACKUP_LOG" \
  --lock "$LOCK" || fail "cron_system_backup.sh exited $?"
tail -1 "$BACKUP_LOG" | grep -q 'system backup OK' \
  || fail "backup log does not end in 'system backup OK' (lock held?)"
echo "    backup: OK"

restic_run() { "$RESTIC_REPO_LIB" --env-file "$ENV_FILE" --prefix "$REPO_PREFIX" run -- "$@"; }

step "4. snapshot file count"
SNAPSHOT_COUNT=$(restic_run ls --json latest "$ARCHIVE" \
  | grep '"type":"file"' | grep -cv '\.partial"' || true)
compare "files under $ARCHIVE" "$LOCAL_COUNT" "$SNAPSHOT_COUNT"

step "5. restore one file"
CREATED_PARENT=0
[ -e "$RESTORE_PARENT" ] || CREATED_PARENT=1
cleanup() {
  rm -rf -- "$RESTORE_ROOT"
  [ "$CREATED_PARENT" -eq 0 ] || rmdir -- "$RESTORE_PARENT"
}
trap cleanup EXIT
mkdir -p "$RESTORE_ROOT"
restic_run restore latest --target "$RESTORE_ROOT" --include "$SAMPLE" \
  || fail "restic restore exited $?"
RESTORED="$RESTORE_ROOT$SAMPLE"
[ -f "$RESTORED" ] || fail "restored file not found at $RESTORED"
echo "    restored: $RESTORED"

step "6. SHA-256"
ORIGINAL_SHA=$(sha256sum "$SAMPLE" | cut -d' ' -f1)
RESTORED_SHA=$(sha256sum "$RESTORED" | cut -d' ' -f1)
compare "SHA-256 of $(basename "$SAMPLE")" "$ORIGINAL_SHA" "$RESTORED_SHA"

step "7. clean up"
echo "    removing $RESTORE_ROOT (and $RESTORE_PARENT if created here)"
echo "=== tick archive backup verify OK: $(date -Is) ==="
