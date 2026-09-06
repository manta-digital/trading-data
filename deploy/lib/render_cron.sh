#!/usr/bin/env bash
# render_cron.sh — render the backup cron.d template (slice 920, D7).
#
# Substitutes every @PLACEHOLDER@ in deploy/cron.d/manta-trading-backup and
# derives the three interval-dependent values from one number: the push
# schedule, the health check's --stale-after, and the push's --timeout.
# Refuses a template with an unfilled placeholder or an unescaped `%` (cron
# turns `%` into a newline) so a bad render can never reach /etc/cron.d.
#
# Usage:
#   render_cron.sh --template <file> --interval <minutes> --checkout <dir> \
#       --env-file <path> --backup-root <dir> --cron-user <user> --pgdata <dir> \
#       --keep-days <n> --remote-prefix <rclone-path> --restic-prefix <name> [--out <file>]
#
# Without --out the rendered text goes to stdout. All other arguments are
# required. Called by setup-backup.sh, which owns the interval constant.
set -euo pipefail

# Three missed pushes before offsite_wal_stale fires (D5).
STALE_AFTER_MULTIPLIER=3
# The push must finish before the next one is due.
PUSH_TIMEOUT_SLACK_MIN=1
MAX_INTERVAL_MIN=60

usage() {
  echo "usage: $0 --template <file> --interval <minutes> --checkout <dir> --env-file <path> --backup-root <dir> --cron-user <user> --pgdata <dir> --keep-days <n> --remote-prefix <rclone-path> --restic-prefix <name> [--out <file>]" >&2
}
die() { echo "error: $*" >&2; exit "${2:-1}"; }

TEMPLATE=""; INTERVAL=""; CHECKOUT=""; ENV_FILE=""; BACKUP_ROOT=""; CRON_USER=""
PGDATA=""; KEEP_DAYS=""; REMOTE_PREFIX=""; RESTIC_PREFIX=""; OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --template)      TEMPLATE="${2:-}"; shift 2 ;;
    --interval)      INTERVAL="${2:-}"; shift 2 ;;
    --checkout)      CHECKOUT="${2:-}"; shift 2 ;;
    --env-file)      ENV_FILE="${2:-}"; shift 2 ;;
    --backup-root)   BACKUP_ROOT="${2:-}"; shift 2 ;;
    --cron-user)     CRON_USER="${2:-}"; shift 2 ;;
    --pgdata)        PGDATA="${2:-}"; shift 2 ;;
    --keep-days)     KEEP_DAYS="${2:-}"; shift 2 ;;
    --remote-prefix) REMOTE_PREFIX="${2:-}"; shift 2 ;;
    --restic-prefix) RESTIC_PREFIX="${2:-}"; shift 2 ;;
    --out)           OUT="${2:-}"; shift 2 ;;
    *) usage; die "unknown argument: $1" 2 ;;
  esac
done
for pair in "--template:$TEMPLATE" "--interval:$INTERVAL" "--checkout:$CHECKOUT" \
            "--env-file:$ENV_FILE" "--backup-root:$BACKUP_ROOT" "--cron-user:$CRON_USER" \
            "--pgdata:$PGDATA" "--keep-days:$KEEP_DAYS" "--remote-prefix:$REMOTE_PREFIX" \
            "--restic-prefix:$RESTIC_PREFIX"; do
  [ -n "${pair#*:}" ] || { usage; die "${pair%%:*} is required" 2; }
done
[ -r "$TEMPLATE" ] || die "template not readable: $TEMPLATE"

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

CONTENT=$(<"$TEMPLATE")
CONTENT=${CONTENT//@CHECKOUT@/$CHECKOUT}
CONTENT=${CONTENT//@ENV_FILE@/$ENV_FILE}
CONTENT=${CONTENT//@BACKUP_ROOT@/$BACKUP_ROOT}
CONTENT=${CONTENT//@CRON_USER@/$CRON_USER}
CONTENT=${CONTENT//@PGDATA@/$PGDATA}
CONTENT=${CONTENT//@KEEP_DAYS@/$KEEP_DAYS}
CONTENT=${CONTENT//@REMOTE_PREFIX@/$REMOTE_PREFIX}
CONTENT=${CONTENT//@RESTIC_PREFIX@/$RESTIC_PREFIX}
CONTENT=${CONTENT//@PUSH_SCHEDULE@/$PUSH_SCHEDULE}
CONTENT=${CONTENT//@STALE_AFTER@/$STALE_AFTER}
CONTENT=${CONTENT//@PUSH_TIMEOUT@/$PUSH_TIMEOUT}

if LEFT=$(grep -o '@[A-Z_]*@' <<< "$CONTENT" | sort -u | paste -sd ' ') && [ -n "$LEFT" ]; then
  die "unfilled placeholder(s) in $TEMPLATE: $LEFT"
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
