#!/usr/bin/env bash
# backup_health_cron.sh — cron glue for the backup health check (slice 920, D6).
#
# Runs check_backup_health.sh and turns its `FLAGS archive=<n> stale=<m>`
# summary into two flag files: --flag (ARCHIVE-BROKEN, gates the weekly base
# backup) when archive>0 and --stale-flag (BACKUP-STALE, alarm only) when
# stale>0. Each is removed when its count returns to 0. A run that cannot
# check at all (no URL in the env file, checker crashed before its summary)
# writes the archive flag: an unreachable cluster is an alarm, not a pass.
#
# Delivery: the flag files, one `logger -t manta-backup` line per flag
# transition (naming the flag and the FAIL names), and one line per run in
# --log. Nothing pushes to a person; the host has no mail transport.
#
# Usage:
#   backup_health_cron.sh --env-file <path> --pgdata <dir> --wal-dir <dir> \
#       --stamp <file> --stale-after <minutes> --system-stamp <file> \
#       --base-dir <dir> --flag <path> --stale-flag <path> --log <path>
#
# The env file path is explicit; the URL is grep'd from it, never sourced
# (the $-in-password trap).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../deploy/lib/env_value.sh
. "$SCRIPT_DIR/../deploy/lib/env_value.sh"
# Sibling tools resolve by name, with this script's own directory as the last
# place searched, so a stub earlier on PATH stands in for them under test.
PATH="$PATH:$SCRIPT_DIR"

LOGGER_TAG=manta-backup
ARCHIVE_FLAG_TITLE="WAL ARCHIVING IS BROKEN OR UNCHECKABLE — see the backup-and-restore runbook"
STALE_FLAG_TITLE="A BACKUP TIER IS STALE — see the backup-and-restore runbook"

usage() {
  echo "usage: $0 --env-file <path> --pgdata <dir> --wal-dir <dir> --stamp <file> --stale-after <minutes> --system-stamp <file> --base-dir <dir> --flag <path> --stale-flag <path> --log <path>" >&2
}

ENV_FILE=""; PGDATA_DIR=""; WAL_DIR=""; STAMP=""; STALE_AFTER_MIN=""; SYSTEM_STAMP=""
BASE_DIR=""; FLAG=""; STALE_FLAG=""; LOG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file)     ENV_FILE="${2:-}"; shift 2 ;;
    --pgdata)       PGDATA_DIR="${2:-}"; shift 2 ;;
    --wal-dir)      WAL_DIR="${2:-}"; shift 2 ;;
    --stamp)        STAMP="${2:-}"; shift 2 ;;
    --stale-after)  STALE_AFTER_MIN="${2:-}"; shift 2 ;;
    --system-stamp) SYSTEM_STAMP="${2:-}"; shift 2 ;;
    --base-dir)     BASE_DIR="${2:-}"; shift 2 ;;
    --flag)         FLAG="${2:-}"; shift 2 ;;
    --stale-flag)   STALE_FLAG="${2:-}"; shift 2 ;;
    --log)          LOG="${2:-}"; shift 2 ;;
    *) echo "error: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done
for pair in "--env-file:$ENV_FILE" "--pgdata:$PGDATA_DIR" "--wal-dir:$WAL_DIR" \
            "--stamp:$STAMP" "--stale-after:$STALE_AFTER_MIN" "--system-stamp:$SYSTEM_STAMP" \
            "--base-dir:$BASE_DIR" "--flag:$FLAG" "--stale-flag:$STALE_FLAG" "--log:$LOG"; do
  [ -n "${pair#*:}" ] || { echo "error: ${pair%%:*} is required" >&2; usage; exit 2; }
done

DB_URL=$(env_value "$ENV_FILE" MT_TIMESCALE_MAINTENANCE_URL 2>/dev/null)
if [ -z "$DB_URL" ]; then
  OUTPUT="FAIL cannot_check: MT_TIMESCALE_MAINTENANCE_URL not found in $ENV_FILE"
else
  OUTPUT=$(check_backup_health.sh --db-url "$DB_URL" --pgdata "$PGDATA_DIR" \
    --wal-dir "$WAL_DIR" --stamp "$STAMP" --stale-after "$STALE_AFTER_MIN" \
    --system-stamp "$SYSTEM_STAMP" --base-dir "$BASE_DIR" 2>&1)
fi

# The summary line is the contract. Without one the checker did not finish,
# which is itself an archive-class alarm.
SUMMARY=$(grep '^FLAGS archive=[0-9]* stale=[0-9]*$' <<< "$OUTPUT" | tail -1)
if [ -n "$SUMMARY" ]; then
  ARCHIVE_COUNT=${SUMMARY#FLAGS archive=}; ARCHIVE_COUNT=${ARCHIVE_COUNT%% *}
  STALE_COUNT=${SUMMARY##* stale=}
else
  OUTPUT="FAIL cannot_check: no FLAGS summary from check_backup_health.sh
$OUTPUT"
  ARCHIVE_COUNT=1; STALE_COUNT=0
fi

NOW=$(date -Is)
echo "$NOW $(tr '\n' ' ' <<< "$OUTPUT")" >> "$LOG"

# fail_names <class>: the failed check names of one class, from the checker's
# `FAILED archive=<a,b> stale=<c>` line, for the journal line of that flag.
fail_names() {
  local line; line=$(grep '^FAILED ' <<< "$OUTPUT" | tail -1)
  line=${line#*"$1="}; line=${line%% *}; echo "${line//,/ }"
}

# set_flag <path> <count> <title> <class>: write the flag when count>0, remove it
# otherwise. The flag's own prior existence is the transition memory: a
# journal line is written only when it appears or disappears.
set_flag() {
  local path="$1" count="$2" title="$3"
  local was_raised=0
  [ ! -e "$path" ] || was_raised=1
  if [ "$count" -gt 0 ]; then
    {
      echo "$title"
      echo "detected: $NOW"
      echo "$OUTPUT"
    } > "$path"
    [ "$was_raised" -eq 1 ] || logger -t "$LOGGER_TAG" "$(basename "$path") raised: $(fail_names "$4")"
  else
    rm -f "$path"
    [ "$was_raised" -eq 0 ] || logger -t "$LOGGER_TAG" "$(basename "$path") cleared"
  fi
}

set_flag "$FLAG" "$ARCHIVE_COUNT" "$ARCHIVE_FLAG_TITLE" archive
set_flag "$STALE_FLAG" "$STALE_COUNT" "$STALE_FLAG_TITLE" stale

[ "$((ARCHIVE_COUNT + STALE_COUNT))" -eq 0 ]
