#!/usr/bin/env bash
# render_cron.sh — render the backup cron.d file (slice 920 D7, slice 227).
#
# The host template (deploy/cron.d/manta-trading-backup) holds the header and
# the host block (restic). Its @CLUSTER_BLOCKS@ line is replaced by the
# cluster template (deploy/cron.d/manta-trading-backup.cluster) rendered once
# per row of the cluster table, in table order. Lines of the cluster template
# starting `##` are template comments and are dropped.
#
# Derives the three interval-dependent values from one number: the push
# schedule, the health check's --stale-after, and the push's --timeout.
# Refuses an unfilled placeholder or an unescaped `%` (cron turns `%` into a
# newline) so a bad render can never reach /etc/cron.d.
#
# Usage:
#   render_cron.sh --template <file> --cluster-template <file> --clusters <table> \
#       --interval <minutes> --checkout <dir> --env-file <path> --host-root <dir> \
#       --cron-user <user> --pgdata <cluster>=<dir> [--pgdata ...] --keep-days <n> \
#       --remote-prefix <rclone-path> --restic-prefix <name> [--out <file>]
#
# --remote-prefix is the bucket root (b2:<bucket>); a row's remote is that
# plus `/<remote_subpath>` unless the row says `-`. Every table row needs a
# --pgdata. Without --out the rendered text goes to stdout. All other
# arguments are required. Called by setup-backup.sh, which owns the interval.
set -euo pipefail

LIB_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=backup_clusters.sh
. "$LIB_DIR/backup_clusters.sh"

# Three missed pushes before offsite_wal_stale fires (D5).
STALE_AFTER_MULTIPLIER=3
# The push must finish before the next one is due.
PUSH_TIMEOUT_SLACK_MIN=1
MAX_INTERVAL_MIN=60
CLUSTER_BLOCKS_MARKER=@CLUSTER_BLOCKS@
# Every ${x//pat/rep} below quotes rep: bash 5.2+ turns an unquoted & in it into
# the matched text (patsub_replacement), and every job line holds 2>&1.
TABLE_NONE=-

usage() {
  echo "usage: $0 --template <file> --cluster-template <file> --clusters <table> --interval <minutes> --checkout <dir> --env-file <path> --host-root <dir> --cron-user <user> --pgdata <cluster>=<dir> [--pgdata ...] --keep-days <n> --remote-prefix <rclone-path> --restic-prefix <name> [--out <file>]" >&2
}
die() { echo "error: $*" >&2; exit "${2:-1}"; }

TEMPLATE=""; CLUSTER_TEMPLATE=""; CLUSTERS=""; INTERVAL=""; CHECKOUT=""; ENV_FILE=""
HOST_ROOT=""; CRON_USER=""; KEEP_DAYS=""; REMOTE_PREFIX=""; RESTIC_PREFIX=""; OUT=""
declare -A PGDATA_OF=()
while [ $# -gt 0 ]; do
  case "$1" in
    --template)         TEMPLATE="${2:-}"; shift 2 ;;
    --cluster-template) CLUSTER_TEMPLATE="${2:-}"; shift 2 ;;
    --clusters)         CLUSTERS="${2:-}"; shift 2 ;;
    --interval)         INTERVAL="${2:-}"; shift 2 ;;
    --checkout)         CHECKOUT="${2:-}"; shift 2 ;;
    --env-file)         ENV_FILE="${2:-}"; shift 2 ;;
    --host-root)        HOST_ROOT="${2:-}"; shift 2 ;;
    --cron-user)        CRON_USER="${2:-}"; shift 2 ;;
    --pgdata)
      case "${2:-}" in
        ?*=?*) PGDATA_OF["${2%%=*}"]="${2#*=}" ;;
        *) usage; die "--pgdata takes <cluster>=<dir>: ${2:-}" 2 ;;
      esac
      shift 2 ;;
    --keep-days)        KEEP_DAYS="${2:-}"; shift 2 ;;
    --remote-prefix)    REMOTE_PREFIX="${2:-}"; shift 2 ;;
    --restic-prefix)    RESTIC_PREFIX="${2:-}"; shift 2 ;;
    --out)              OUT="${2:-}"; shift 2 ;;
    *) usage; die "unknown argument: $1" 2 ;;
  esac
done
for pair in "--template:$TEMPLATE" "--cluster-template:$CLUSTER_TEMPLATE" "--clusters:$CLUSTERS" \
            "--interval:$INTERVAL" "--checkout:$CHECKOUT" "--env-file:$ENV_FILE" \
            "--host-root:$HOST_ROOT" "--cron-user:$CRON_USER" "--keep-days:$KEEP_DAYS" \
            "--remote-prefix:$REMOTE_PREFIX" "--restic-prefix:$RESTIC_PREFIX"; do
  [ -n "${pair#*:}" ] || { usage; die "${pair%%:*} is required" 2; }
done
[ -r "$TEMPLATE" ] || die "template not readable: $TEMPLATE"
[ -r "$CLUSTER_TEMPLATE" ] || die "cluster template not readable: $CLUSTER_TEMPLATE"
backup_clusters_load "$CLUSTERS" || die "cluster table rejected: $CLUSTERS"

case "$INTERVAL" in
  ''|*[!0-9]*) die "--interval must be a whole number of minutes: $INTERVAL" 2 ;;
esac
if [ "$INTERVAL" -eq "$MAX_INTERVAL_MIN" ]; then
  PUSH_SCHEDULE="0 * * * *"
elif [ "$INTERVAL" -ge 1 ] && [ "$INTERVAL" -lt "$MAX_INTERVAL_MIN" ]; then
  # `*/N` only lands on the same minutes every hour when N divides 60.
  [ $((MAX_INTERVAL_MIN % INTERVAL)) -eq 0 ] || die "--interval $INTERVAL does not divide $MAX_INTERVAL_MIN; cron */N cannot express it" 2
  PUSH_SCHEDULE="*/$INTERVAL * * * *"
else
  die "--interval must be between 1 and $MAX_INTERVAL_MIN minutes: $INTERVAL" 2
fi
STALE_AFTER=$((INTERVAL * STALE_AFTER_MULTIPLIER))
PUSH_TIMEOUT=$((INTERVAL - PUSH_TIMEOUT_SLACK_MIN))

# One block per table row.
CLUSTER_TEXT=$(grep -v '^##' "$CLUSTER_TEMPLATE")
BLOCKS=""
for i in "${!BC_CLUSTER[@]}"; do
  name=${BC_CLUSTER[$i]}
  [ -n "${PGDATA_OF[$name]:-}" ] || die "no --pgdata for cluster $name" 2
  remote=$REMOTE_PREFIX
  [ "${BC_REMOTE[$i]}" = "$TABLE_NONE" ] || remote="$REMOTE_PREFIX/${BC_REMOTE[$i]}"
  repl_arg=""
  [ "${BC_REPL_HOST[$i]}" = "$TABLE_NONE" ] || repl_arg=" --replication-host ${BC_REPL_HOST[$i]}"
  block=$CLUSTER_TEXT
  block=${block//@CLUSTER@/"$name"}
  block=${block//@CLUSTER_ROOT@/"${BC_ROOT[$i]}"}
  block=${block//@CLUSTER_REMOTE@/"$remote"}
  block=${block//@URL_KEY@/"${BC_URL_KEY[$i]}"}
  block=${block//@REPLICATION_HOST_ARG@/"$repl_arg"}
  block=${block//@METADATA_CRON@/"${BC_METADATA_CRON[$i]}"}
  block=${block//@WEEKLY_CRON@/"${BC_WEEKLY_CRON[$i]}"}
  block=${block//@PGDATA@/"${PGDATA_OF[$name]}"}
  BLOCKS+="${BLOCKS:+$'\n'}$block"
done

CONTENT=$(<"$TEMPLATE")
grep -qx "$CLUSTER_BLOCKS_MARKER" <<< "$CONTENT" || die "$TEMPLATE has no $CLUSTER_BLOCKS_MARKER line"
CONTENT=${CONTENT//"$CLUSTER_BLOCKS_MARKER"/"$BLOCKS"}
CONTENT=${CONTENT//@CHECKOUT@/"$CHECKOUT"}
CONTENT=${CONTENT//@ENV_FILE@/"$ENV_FILE"}
CONTENT=${CONTENT//@HOST_ROOT@/"$HOST_ROOT"}
CONTENT=${CONTENT//@CRON_USER@/"$CRON_USER"}
CONTENT=${CONTENT//@KEEP_DAYS@/"$KEEP_DAYS"}
CONTENT=${CONTENT//@RESTIC_PREFIX@/"$RESTIC_PREFIX"}
CONTENT=${CONTENT//@PUSH_SCHEDULE@/"$PUSH_SCHEDULE"}
CONTENT=${CONTENT//@STALE_AFTER@/"$STALE_AFTER"}
CONTENT=${CONTENT//@PUSH_TIMEOUT@/"$PUSH_TIMEOUT"}

if LEFT=$(grep -o '@[A-Z_]*@' <<< "$CONTENT" | sort -u | paste -sd ' ') && [ -n "$LEFT" ]; then
  die "unfilled placeholder(s) in $TEMPLATE or $CLUSTER_TEMPLATE: $LEFT"
fi
# A `%` not preceded by a backslash is a newline to cron(8).
if grep -qE '(^|[^\\])%' <<< "$CONTENT"; then
  die "unescaped % in rendered cron.d (cron treats it as a newline)"
fi

if [ -n "$OUT" ]; then
  printf '%s\n' "$CONTENT" > "$OUT"
else
  printf '%s\n' "$CONTENT"
fi
