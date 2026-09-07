#!/usr/bin/env bash
# restic_repo.sh — the one place the restic repository and its credentials
# are assembled from the env file (slice 920, D9).
#
# Repository: s3:<MT_BACKUP_S3_ENDPOINT>/<MT_BACKUP_S3_BUCKET>/<prefix>, the
# same bucket-scoped key the WAL/base/metadata tiers use, with the password
# from MT_BACKUP_RESTIC_PASSWORD. Values are grep'd from the env file, never
# sourced, and reach restic only through its environment.
#
# Usage:
#   restic_repo.sh --env-file <path> --prefix <name> check
#   restic_repo.sh --env-file <path> --prefix <name> init
#   restic_repo.sh --env-file <path> --prefix <name> run -- <restic arguments...>
#
# check: OK restic-repo <repo> | MISSING restic-repo <repo> (not initialised)
#        | MISSING restic-password ... | MISSING restic-env <key>; exit 1 on MISSING.
# init:  as check, then `restic init` when the repository is absent (APPLIED).
# run:   exec restic with the environment set (for the cron job).
set -euo pipefail

S3_KEYS=(MT_BACKUP_S3_ENDPOINT MT_BACKUP_S3_KEY_ID MT_BACKUP_S3_APPLICATION_KEY MT_BACKUP_S3_BUCKET)
PASSWORD_KEY=MT_BACKUP_RESTIC_PASSWORD

usage() {
  echo "usage: $0 --env-file <path> --prefix <name> (check|init|run -- <restic args>)" >&2
}
die() { echo "error: $*" >&2; exit "${2:-1}"; }

ENV_FILE=""; PREFIX=""; COMMAND=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --prefix)   PREFIX="${2:-}"; shift 2 ;;
    check|init) COMMAND="$1"; shift ;;
    run)        COMMAND=run; shift; [ "${1:-}" = "--" ] && shift; break ;;
    *) usage; die "unknown argument: $1" 2 ;;
  esac
done
[ -n "$ENV_FILE" ] || { usage; die "--env-file is required" 2; }
[ -n "$PREFIX" ] || { usage; die "--prefix is required" 2; }
[ -n "$COMMAND" ] || { usage; die "one of check, init, run is required" 2; }
[ -r "$ENV_FILE" ] || die "env file not readable: $ENV_FILE"

# shellcheck source=env_value.sh
. "$(cd "$(dirname "$0")" && pwd)/env_value.sh"

declare -A ENV_VALUES=()
for key in "${S3_KEYS[@]}"; do
  ENV_VALUES[$key]=$(env_value "$ENV_FILE" "$key")
  [ -n "${ENV_VALUES[$key]}" ] || { echo "MISSING restic-env $key not in $ENV_FILE"; exit 1; }
done
PASSWORD=$(env_value "$ENV_FILE" "$PASSWORD_KEY")
[ -n "$PASSWORD" ] || { echo "MISSING restic-password $PASSWORD_KEY not in $ENV_FILE"; exit 1; }
REPO="s3:${ENV_VALUES[MT_BACKUP_S3_ENDPOINT]%/}/${ENV_VALUES[MT_BACKUP_S3_BUCKET]}/$PREFIX"

restic_env() {
  env RESTIC_REPOSITORY="$REPO" RESTIC_PASSWORD="$PASSWORD" \
      AWS_ACCESS_KEY_ID="${ENV_VALUES[MT_BACKUP_S3_KEY_ID]}" \
      AWS_SECRET_ACCESS_KEY="${ENV_VALUES[MT_BACKUP_S3_APPLICATION_KEY]}" \
      restic "$@"
}

if [ "$COMMAND" = run ]; then
  exec env RESTIC_REPOSITORY="$REPO" RESTIC_PASSWORD="$PASSWORD" \
      AWS_ACCESS_KEY_ID="${ENV_VALUES[MT_BACKUP_S3_KEY_ID]}" \
      AWS_SECRET_ACCESS_KEY="${ENV_VALUES[MT_BACKUP_S3_APPLICATION_KEY]}" \
      restic "$@"
fi

command -v restic >/dev/null || { echo "MISSING restic-repo $REPO (restic not installed)"; exit 1; }

if restic_env cat config >/dev/null 2>&1; then
  echo "OK restic-repo $REPO"
  exit 0
fi
if [ "$COMMAND" = check ]; then
  echo "MISSING restic-repo $REPO (not initialised)"
  exit 1
fi
restic_env init >/dev/null
echo "APPLIED restic-repo $REPO"
restic_env cat config >/dev/null 2>&1 || { echo "MISSING restic-repo $REPO (init ran but cat config still fails)"; exit 1; }
echo "OK restic-repo $REPO"
