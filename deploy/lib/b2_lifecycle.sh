#!/usr/bin/env bash
# b2_lifecycle.sh — report whether the bucket's lifecycle rules cover one
# cluster's offsite prefixes (slice 227, TD10). Read-only, always.
#
# The `b2` rclone remote is the S3 backend, which has no lifecycle command, so
# the rules are read through rclone's native b2 backend (`:b2:<bucket>`), with
# the same bucket-scoped application key given in its environment.
#
# Never sets a rule. rclone v1.75.0 backend/b2/b2.go lifecycleCommand sends
# `LifecycleRules: []{newRule}` with an empty fileNamePrefix to
# b2_update_bucket: setting replaces EVERY rule on the bucket with one
# whole-bucket rule, which would delete production's per-prefix rules. A
# missing rule is therefore added by hand in the B2 console.
#
# Usage:
#   b2_lifecycle.sh --env-file <path> --subpath <prefix|-> --days <n> <prefix>...
#
# For each <prefix> (e.g. base/ wal/), the expected rule is fileNamePrefix
# <subpath>/<prefix> (or <prefix> when --subpath is `-`) with
# daysFromHidingToDeleting <n>. Prints per prefix
#   OK lifecycle <full-prefix>
#   MISSING lifecycle <full-prefix> (add in the B2 console: ...)
# and exits 0 either way: a missing rule costs storage, not data. Exits 1
# only when the rules cannot be read (printing `UNREADABLE lifecycle ...`).
set -euo pipefail

LIB_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=env_value.sh
. "$LIB_DIR/env_value.sh"

KEY_ID_KEY=MT_BACKUP_S3_KEY_ID
APP_KEY_KEY=MT_BACKUP_S3_APPLICATION_KEY
BUCKET_KEY=MT_BACKUP_S3_BUCKET
TABLE_NONE=-

usage() {
  echo "usage: $0 --env-file <path> --subpath <prefix|-> --days <n> <prefix>..." >&2
}
die() { echo "error: $*" >&2; exit "${2:-1}"; }

ENV_FILE=""; SUBPATH=""; DAYS=""; PREFIXES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --subpath)  SUBPATH="${2:-}"; shift 2 ;;
    --days)     DAYS="${2:-}"; shift 2 ;;
    -*) usage; die "unknown argument: $1" 2 ;;
    *) PREFIXES+=("$1"); shift ;;
  esac
done
for pair in "--env-file:$ENV_FILE" "--subpath:$SUBPATH" "--days:$DAYS"; do
  [ -n "${pair#*:}" ] || { usage; die "${pair%%:*} is required" 2; }
done
[ "${#PREFIXES[@]}" -gt 0 ] || { usage; die "at least one prefix is required" 2; }
case "$DAYS" in ''|*[!0-9]*) die "--days must be a whole number: $DAYS" 2 ;; esac

BUCKET=$(env_value "$ENV_FILE" "$BUCKET_KEY")
KEY_ID=$(env_value "$ENV_FILE" "$KEY_ID_KEY")
APP_KEY=$(env_value "$ENV_FILE" "$APP_KEY_KEY")
if [ -z "$BUCKET" ] || [ -z "$KEY_ID" ] || [ -z "$APP_KEY" ]; then
  echo "UNREADABLE lifecycle ($BUCKET_KEY, $KEY_ID_KEY or $APP_KEY_KEY missing from $ENV_FILE)"
  exit 1
fi

if ! RULES=$(RCLONE_B2_ACCOUNT="$KEY_ID" RCLONE_B2_KEY="$APP_KEY" \
             rclone backend lifecycle ":b2:$BUCKET" 2>&1); then
  echo "UNREADABLE lifecycle (rclone backend lifecycle :b2:$BUCKET failed: $(tr '\n' ' ' <<< "$RULES"))"
  exit 1
fi
if ! jq -e 'type == "array"' >/dev/null 2>&1 <<< "$RULES"; then
  echo "UNREADABLE lifecycle (not a JSON rule list: $(tr '\n' ' ' <<< "$RULES"))"
  exit 1
fi

for p in "${PREFIXES[@]}"; do
  full=$p
  [ "$SUBPATH" = "$TABLE_NONE" ] || full="$SUBPATH/$p"
  if jq -e --arg p "$full" --argjson d "$DAYS" \
       'any(.[]; .fileNamePrefix == $p and .daysFromHidingToDeleting == $d)' >/dev/null <<< "$RULES"; then
    echo "OK lifecycle $full"
  else
    echo "MISSING lifecycle $full (add in the B2 console: fileNamePrefix=$full daysFromHidingToDeleting=$DAYS; rclone can only replace all rules)"
  fi
done
