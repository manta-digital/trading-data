#!/usr/bin/env bash
# add-tick-env.sh — add the tick pass's settings to the production env file
# (slice 234, TD3). Run once, as root, before the 234 cutover:
#
#   sudo deploy/add-tick-env.sh --from /home/manta/source/repos/manta/trading-data/.env
#
# Copies exactly the five keys the tick pass needs from the checkout's .env.
# Check-then-act: it refuses, naming the missing keys, if any is absent from
# the source, and then writes nothing. A key already in the target is never
# changed (env_add_keys). MT_TICK_MAINTENANCE_URL is never copied: the
# maintenance credential stays out of the service environment (913). Values
# are never printed or logged; only key names and a count.
#
# Test hooks (never set in production): MT_ADD_TICK_ENV_TARGET,
# MT_ADD_TICK_ENV_OWNER, MT_ADD_TICK_ENV_GROUP, MT_ADD_TICK_ENV_LOG_DIR, and
# MT_ADD_TICK_ENV_PRIV_PREFIX, a command that prefixes the root-only chown and
# chmod so a test can see them issued.
set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)"
# shellcheck source=SCRIPTDIR/lib/env_value.sh
. "$LIB_DIR/env_value.sh"
# shellcheck source=SCRIPTDIR/lib/env_add_keys.sh
. "$LIB_DIR/env_add_keys.sh"

TARGET="${MT_ADD_TICK_ENV_TARGET:-/etc/manta-trading.env}"
OWNER="${MT_ADD_TICK_ENV_OWNER:-root}"
GROUP="${MT_ADD_TICK_ENV_GROUP:-manta-trading}"
LOG_DIR="${MT_ADD_TICK_ENV_LOG_DIR:-/var/log}"
PRIV="${MT_ADD_TICK_ENV_PRIV_PREFIX:-}"
# What the service user may read; env_add_keys leaves 0600.
MODE="0640"
KEYS=(
  MT_TICK_DB_URL
  MT_DATABENTO_API_KEY
  MT_TICK_SPEND_CEILING_USD
  MT_TICK_SPEND_30D_CEILING_USD
  MT_TICK_ARCHIVE_DIR
)

die() { echo "ERROR: $*" >&2; exit 1; }

[ "${1:-}" = "--from" ] && [ -n "${2:-}" ] || die "usage: $0 --from <checkout .env>"
SOURCE="$2"
[ -r "$SOURCE" ] || die "$SOURCE is not readable"
[ -f "$TARGET" ] || die "$TARGET does not exist; run install-production.sh first"

LOG="$LOG_DIR/manta-add-tick-env-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1
echo "log: $LOG"

pairs=()
missing=()
for key in "${KEYS[@]}"; do
  value="$(env_value "$SOURCE" "$key")"
  if [ -z "$value" ]; then missing+=("$key"); continue; fi
  pairs+=("$key=$value")
done
[ "${#missing[@]}" -eq 0 ] || die "missing from $SOURCE: ${missing[*]} (nothing written)"

added="$(env_add_keys "$TARGET" "$OWNER" "${pairs[@]}")" || die "could not write $TARGET"
$PRIV chown "$OWNER:$GROUP" "$TARGET"
$PRIV chmod "$MODE" "$TARGET"
echo "$added keys added to $TARGET (${KEYS[*]}); mode $MODE $OWNER:$GROUP"
