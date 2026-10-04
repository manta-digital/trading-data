#!/usr/bin/env bash
# cron_nightly_metadata.sh — cron glue for the nightly metadata tier (9.1).
#
# Dump the metadata tables, then push the metadata directory offsite with
# checksum verification. This glue is the *caller* that supplies the explicit
# arguments the D5 tools demand; credentials are grep'd from the named env
# file, never sourced.
#
# Usage:
#   cron_nightly_metadata.sh --env-file <path> [--url-key <MT_KEY>] \
#       [--remote <rclone-path>] --dest <dir>
#
# --url-key names the env key holding the cluster's URL (the cluster table's
# url_key); --remote is the offsite prefix, to which `/metadata` is appended
# (production b2:<bucket>, tick b2:<bucket>/17-tick). Slice 227.
#
# Absent --url-key reads MT_TIMESCALE_MAINTENANCE_URL and absent --remote
# reads b2:<MT_BACKUP_S3_BUCKET>: those forms exist only for the pre-227
# installed cron file and go after the cutover.
#
# The URL read uses the shared env_value helper (anchored `KEY=`, first
# line). It replaced a `grep ^KEY | sed | tr` read in 227 because the key is
# now a parameter; both give the same value for a single `KEY=value` line.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../deploy/lib/env_value.sh
. "$SCRIPT_DIR/../deploy/lib/env_value.sh"
# shellcheck source=../deploy/lib/backup_clusters.sh
. "$SCRIPT_DIR/../deploy/lib/backup_clusters.sh"

# Pre-227 cron lines pass neither argument (removed after the 227 cutover).
PRE227_URL_KEY=MT_TIMESCALE_MAINTENANCE_URL
PRE227_BUCKET_KEY=MT_BACKUP_S3_BUCKET
RCLONE_REMOTE=b2

usage() {
  echo "usage: $0 --env-file <path> [--url-key <MT_KEY>] [--remote <rclone-path>] --dest <dir>" >&2
}

ENV_FILE=""; URL_KEY="$PRE227_URL_KEY"; REMOTE=""; DEST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --url-key)  URL_KEY="${2:-}"; shift 2 ;;
    --remote)   REMOTE="${2:-}"; [ -n "$REMOTE" ] || { echo "error: --remote needs a value" >&2; usage; exit 2; }; shift 2 ;;
    --dest)     DEST="${2:-}"; shift 2 ;;
    *) echo "error: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done
[ -n "$ENV_FILE" ] || { echo "error: --env-file is required" >&2; usage; exit 2; }
[ -n "$DEST" ] || { echo "error: --dest is required" >&2; usage; exit 2; }
[[ $URL_KEY =~ $BC_URL_KEY_RE ]] || { echo "error: --url-key $URL_KEY does not match $BC_URL_KEY_RE" >&2; usage; exit 2; }

DB_URL=$(env_value "$ENV_FILE" "$URL_KEY")
[ -n "$DB_URL" ] || { echo "error: $URL_KEY not in $ENV_FILE" >&2; exit 1; }
if [ -z "$REMOTE" ]; then
  BUCKET=$(env_value "$ENV_FILE" "$PRE227_BUCKET_KEY")
  [ -n "$BUCKET" ] || { echo "error: $PRE227_BUCKET_KEY not in $ENV_FILE" >&2; exit 1; }
  REMOTE="$RCLONE_REMOTE:$BUCKET"
fi

echo "=== nightly metadata run: $(date -Is) ==="
"$SCRIPT_DIR/backup_metadata.sh" --db-url "$DB_URL" --dest "$DEST"
"$SCRIPT_DIR/offsite_sync.sh" --source "$DEST" --remote "$REMOTE/metadata"
echo "=== nightly metadata done: $(date -Is) ==="
