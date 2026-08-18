#!/usr/bin/env bash
# offsite_sync.sh — checksum-verified offsite copy (slice 915, D6).
#
# Pushes a backup directory to an rclone remote and then verifies it **by
# checksum** with `rclone check --one-way`. A clean exit from `rclone sync`
# alone is not evidence: sync's quick-pass compares size and modtime, so a
# corrupted file with both unchanged sails through sync and is only caught by
# the checksum pass. The check is the load-bearing stage.
#
# Usage:
#   offsite_sync.sh --source <dir> --remote <rclone-remote-path>
#
# Both arguments are required and explicit (D5). Called by backup_prod.sh
# when --remote is given; standalone for re-verifying an existing copy.
set -euo pipefail

usage() {
  echo "usage: $0 --source <directory> --remote <rclone-remote-path>" >&2
}

SOURCE=""
REMOTE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE="${2:-}"; shift 2 ;;
    --remote) REMOTE="${2:-}"; shift 2 ;;
    *) echo "error: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [ -z "$SOURCE" ]; then
  echo "error: --source is required" >&2; usage; exit 2
fi
if [ -z "$REMOTE" ]; then
  echo "error: --remote is required" >&2; usage; exit 2
fi
if [ ! -d "$SOURCE" ]; then
  echo "error: source is not a directory: $SOURCE" >&2; exit 1
fi

echo "offsite sync: $SOURCE -> $REMOTE ($(date -Is))"
rclone sync "$SOURCE" "$REMOTE"

echo "offsite checksum verification ($(date -Is))"
rclone check "$SOURCE" "$REMOTE" --one-way

echo "offsite copy verified by checksum: $REMOTE ($(date -Is))"
