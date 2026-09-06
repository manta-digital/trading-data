#!/usr/bin/env bash
# timeshift_merge.sh — merge the managed keys into timeshift.json (slice 920, D8).
#
# timeshift.json mixes operator intent (schedule, counts, excludes) with host
# identity (backup_device_uuid) and runtime state (snapshot_size,
# snapshot_count). This merges ONLY the managed keys with jq and writes back
# on drift; every other key is preserved byte-for-byte in value. The device
# UUID is reported, never set. A missing file is MISSING and is never created.
#
# Usage:
#   timeshift_merge.sh [--check] --file <timeshift.json> --count-weekly <n> \
#       --exclude <pattern> [--exclude ...]
#
# Managed keys: schedule_weekly=true, every other schedule_* false,
# count_weekly, exclude (compared as a set). Output lines:
#   OK <key> | DRIFT <key> <expected> <actual> | APPLIED <key> |
#   INFO timeshift-device-uuid <uuid> | MISSING timeshift-config <file>
# Exit 1 if any line is not OK/APPLIED/INFO.
set -euo pipefail

SCHEDULE_KEYS=(schedule_monthly schedule_weekly schedule_daily schedule_hourly schedule_boot)
SCHEDULE_ON=schedule_weekly
UUID_KEY=backup_device_uuid

usage() {
  echo "usage: $0 [--check] --file <timeshift.json> --count-weekly <n> --exclude <pattern> [--exclude ...]" >&2
}
die() { echo "error: $*" >&2; exit "${2:-1}"; }

CHECK=0; FILE=""; COUNT_WEEKLY=""; EXCLUDES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --check)        CHECK=1; shift ;;
    --file)         FILE="${2:-}"; shift 2 ;;
    --count-weekly) COUNT_WEEKLY="${2:-}"; shift 2 ;;
    --exclude)      [ -n "${2:-}" ] || { usage; die "--exclude needs a pattern" 2; }
                    EXCLUDES+=("$2"); shift 2 ;;
    *) usage; die "unknown argument: $1" 2 ;;
  esac
done
[ -n "$FILE" ] || { usage; die "--file is required" 2; }
[ -n "$COUNT_WEEKLY" ] || { usage; die "--count-weekly is required" 2; }
[ "${#EXCLUDES[@]}" -gt 0 ] || { usage; die "at least one --exclude is required" 2; }

if [ ! -f "$FILE" ]; then
  echo "MISSING timeshift-config $FILE"
  exit 1
fi

NOT_OK=0
report() { echo "$*"; case "${1%% *}" in OK|APPLIED|INFO) ;; *) NOT_OK=$((NOT_OK + 1)) ;; esac; }

# timeshift stores every scalar as a JSON string ("true", "2"); keep that.
EXPECTED_EXCLUDES=$(printf '%s\n' "${EXCLUDES[@]}" | jq -R . | jq -sc 'sort')
ACTUAL_EXCLUDES=$(jq -c '(.exclude // []) | sort' "$FILE")
echo "INFO timeshift-device-uuid $(jq -r ".[\"$UUID_KEY\"] // \"(unset)\"" "$FILE")"

DRIFTED=()
for key in "${SCHEDULE_KEYS[@]}"; do
  expected=false; [ "$key" != "$SCHEDULE_ON" ] || expected=true
  actual=$(jq -r ".[\"$key\"] // \"(unset)\"" "$FILE")
  if [ "$actual" = "$expected" ]; then report "OK $key"; else report "DRIFT $key $expected $actual"; DRIFTED+=("$key"); fi
done
actual=$(jq -r '.count_weekly // "(unset)"' "$FILE")
if [ "$actual" = "$COUNT_WEEKLY" ]; then report "OK count_weekly"; else report "DRIFT count_weekly $COUNT_WEEKLY $actual"; DRIFTED+=(count_weekly); fi
if [ "$ACTUAL_EXCLUDES" = "$EXPECTED_EXCLUDES" ]; then report "OK exclude"; else report "DRIFT exclude $EXPECTED_EXCLUDES $ACTUAL_EXCLUDES"; DRIFTED+=(exclude); fi

if [ "$CHECK" -eq 0 ] && [ "${#DRIFTED[@]}" -gt 0 ]; then
  MANAGED=$(jq -n --arg on "$SCHEDULE_ON" --arg count "$COUNT_WEEKLY" \
    --argjson keys "$(printf '%s\n' "${SCHEDULE_KEYS[@]}" | jq -R . | jq -sc .)" \
    --argjson excludes "$(printf '%s\n' "${EXCLUDES[@]}" | jq -R . | jq -sc .)" \
    '($keys | map({key: ., value: (if . == $on then "true" else "false" end)}) | from_entries)
     + {count_weekly: $count, exclude: $excludes}')
  TMP=$(mktemp "$(dirname "$FILE")/.timeshift.XXXXXX")
  jq --argjson managed "$MANAGED" '. + $managed' "$FILE" > "$TMP"
  # cat-over, not mv: keeps the file's owner, mode, and inode.
  cat "$TMP" > "$FILE"; rm -f "$TMP"
  for key in "${DRIFTED[@]}"; do echo "APPLIED $key"; done
  NOT_OK=0
fi

[ "$NOT_OK" -eq 0 ]
