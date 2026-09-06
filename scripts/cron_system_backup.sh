#!/usr/bin/env bash
# cron_system_backup.sh — nightly restic backup of the host's non-derivable
# state to B2, run as root from cron.d (slice 920, D9).
#
# Include set (constants below): /etc, /root, /var/spool/cron/crontabs,
# /home/manta. Excludes come from --exclude-file (deploy/restic-excludes.txt).
# The repository and its credentials are assembled by deploy/lib/restic_repo.sh
# from the env file's MT_BACKUP_S3_* keys and MT_BACKUP_RESTIC_PASSWORD; they
# reach restic only through its environment and are never printed here.
#
# Order: `flock -n` (one restic process at a time — so a `restic unlock` first
# is safe, and a stale lock from an interrupted run cannot fail every later
# backup), `restic backup --one-file-system`, `restic forget --keep-daily 7
# --keep-weekly 4 --keep-monthly 3 --prune`. Any non-zero step: reason to
# --log and `logger -t manta-backup`, exit non-zero, stamp untouched; the
# health check's system_backup_stale raises BACKUP-STALE within two days.
# Success touches --stamp.
#
# --check runs `restic check --read-data-subset=5%` instead (the monthly
# cron.d entry); it needs no --exclude-file or --stamp.
#
# Usage:
#   cron_system_backup.sh --env-file <path> --repo-prefix <name> --exclude-file <path> \
#       --stamp <file> --log <file> --lock <file>
#   cron_system_backup.sh --check --env-file <path> --repo-prefix <name> --log <file> --lock <file>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESTIC_REPO_LIB="$SCRIPT_DIR/../deploy/lib/restic_repo.sh"

INCLUDE_PATHS=(/etc /root /var/spool/cron/crontabs /home/manta)
KEEP_DAILY=7
KEEP_WEEKLY=4
KEEP_MONTHLY=3
CHECK_READ_DATA_SUBSET=5%
LOGGER_TAG=manta-backup
SKIP_MESSAGE="skipped: previous run active"

usage() {
  echo "usage: $0 [--check] --env-file <path> --repo-prefix <name> [--exclude-file <path>] [--stamp <file>] --log <file> --lock <file>" >&2
}

CHECK=0; ENV_FILE=""; REPO_PREFIX=""; EXCLUDE_FILE=""; STAMP=""; LOG=""; LOCK=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check)        CHECK=1; shift ;;
    --env-file)     ENV_FILE="${2:-}"; shift 2 ;;
    --repo-prefix)  REPO_PREFIX="${2:-}"; shift 2 ;;
    --exclude-file) EXCLUDE_FILE="${2:-}"; shift 2 ;;
    --stamp)        STAMP="${2:-}"; shift 2 ;;
    --log)          LOG="${2:-}"; shift 2 ;;
    --lock)         LOCK="${2:-}"; shift 2 ;;
    *) usage; echo "error: unknown argument: $1" >&2; exit 2 ;;
  esac
done
REQUIRED=("--env-file:$ENV_FILE" "--repo-prefix:$REPO_PREFIX" "--log:$LOG" "--lock:$LOCK")
[ "$CHECK" -eq 1 ] || REQUIRED+=("--exclude-file:$EXCLUDE_FILE" "--stamp:$STAMP")
for pair in "${REQUIRED[@]}"; do
  [ -n "${pair#*:}" ] || { usage; echo "error: ${pair%%:*} is required" >&2; exit 2; }
done
[ -x "$RESTIC_REPO_LIB" ] || { echo "error: $RESTIC_REPO_LIB not found" >&2; exit 1; }

NOW=$(date -Is)
fail() {
  local msg="system backup FAILED: $*"
  echo "$NOW $msg" >> "$LOG"
  echo "$msg" >&2
  logger -t "$LOGGER_TAG" "$msg"
  exit 1
}
# All restic calls go through the lib so the environment is built in one place.
restic_run() { "$RESTIC_REPO_LIB" --env-file "$ENV_FILE" --prefix "$REPO_PREFIX" run -- "$@"; }

# The password's presence is checked before anything else, without running
# restic: the lib names the missing key on stdout and exits 1.
PRECHECK=$("$RESTIC_REPO_LIB" --env-file "$ENV_FILE" --prefix "$REPO_PREFIX" check 2>&1 || true)
case "$PRECHECK" in
  "MISSING restic-password"*|"MISSING restic-env"*) fail "$PRECHECK" ;;
esac

exec 9>>"$LOCK"
if ! flock -n 9; then
  echo "$NOW $SKIP_MESSAGE" >> "$LOG"
  echo "$SKIP_MESSAGE"
  exit 0
fi

if [ "$CHECK" -eq 1 ]; then
  echo "=== system backup check: $NOW ==="
  restic_run unlock || fail "restic unlock exited $?"
  restic_run check --read-data-subset="$CHECK_READ_DATA_SUBSET" || fail "restic check exited $?"
  echo "$NOW system backup check OK (read-data-subset $CHECK_READ_DATA_SUBSET)" >> "$LOG"
  echo "=== system backup check done: $(date -Is) ==="
  exit 0
fi

[ -r "$EXCLUDE_FILE" ] || fail "exclude file not readable: $EXCLUDE_FILE"
echo "=== system backup run: $NOW ==="
restic_run unlock || fail "restic unlock exited $?"
restic_run backup --one-file-system --exclude-file "$EXCLUDE_FILE" "${INCLUDE_PATHS[@]}" || fail "restic backup exited $?"
restic_run forget --keep-daily "$KEEP_DAILY" --keep-weekly "$KEEP_WEEKLY" --keep-monthly "$KEEP_MONTHLY" --prune || fail "restic forget exited $?"
touch "$STAMP"
echo "$NOW system backup OK" >> "$LOG"
echo "=== system backup done: $(date -Is) ==="
