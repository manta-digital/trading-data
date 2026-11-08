#!/usr/bin/env bash
# set-tick-spend-ceilings.sh — set the tick pass's two spend ceilings in the
# production env file (slice 304 D4). Run as root:
#
#   sudo deploy/set-tick-spend-ceilings.sh --per-pass 7 --cap-30d 16
#
# Check-then-act: it refuses, writing nothing, unless both values are
# non-negative decimals, the file exists and both keys are already in it (this
# script replaces values; add-tick-env.sh adds keys). The file is replaced
# atomically with its mode and owner copied over. Ceilings are not secrets: it
# prints the old and new value of each key and no other line of the file.
#
# Deliberately absent from deploy/sudoers.d/manta-ops: only the Project
# Manager's root can raise a ceiling, never an agent.
#
# Test hook (never set in production): MT_CEILINGS_ENV_TARGET, the file to edit.
set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)"
# shellcheck source=SCRIPTDIR/lib/env_value.sh
. "$LIB_DIR/env_value.sh"

TARGET="${MT_CEILINGS_ENV_TARGET:-/etc/manta-trading.env}"
PER_PASS_KEY="MT_TICK_SPEND_CEILING_USD"
CAP_30D_KEY="MT_TICK_SPEND_30D_CEILING_USD"
DECIMAL='^[0-9]+(\.[0-9]+)?$'

die() { echo "ERROR: $*" >&2; exit 1; }

USAGE="usage: $0 --per-pass USD --cap-30d USD"
[ "$#" -eq 4 ] && [ "$1" = "--per-pass" ] && [ "$3" = "--cap-30d" ] || die "$USAGE"
per_pass="$2"
cap_30d="$4"
[[ "$per_pass" =~ $DECIMAL ]] || die "--per-pass '$per_pass' is not a non-negative decimal"
[[ "$cap_30d" =~ $DECIMAL ]] || die "--cap-30d '$cap_30d' is not a non-negative decimal"
[ -f "$TARGET" ] || die "$TARGET does not exist"
for key in "$PER_PASS_KEY" "$CAP_30D_KEY"; do
  grep -q "^$key=" "$TARGET" || die "$key is not in $TARGET; add-tick-env.sh adds keys"
done

old_per_pass="$(env_value "$TARGET" "$PER_PASS_KEY")"
old_cap_30d="$(env_value "$TARGET" "$CAP_30D_KEY")"
# Build the new file beside the old one, give it the old one's mode and owner,
# then rename over it: an interrupted run leaves the original untouched.
# awk sample: `MT_TICK_SPEND_CEILING_USD=5` with --per-pass 7 becomes
# `MT_TICK_SPEND_CEILING_USD=7`; every other line passes through unchanged.
tmp="$(mktemp "$TARGET.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
chmod --reference="$TARGET" "$tmp"
chown --reference="$TARGET" "$tmp"
awk -v k1="$PER_PASS_KEY" -v v1="$per_pass" -v k2="$CAP_30D_KEY" -v v2="$cap_30d" '
  index($0, k1 "=") == 1 && !s1 { print k1 "=" v1; s1 = 1; next }
  index($0, k2 "=") == 1 && !s2 { print k2 "=" v2; s2 = 1; next }
  { print }' "$TARGET" > "$tmp"
mv "$tmp" "$TARGET"

echo "$PER_PASS_KEY: $old_per_pass -> $per_pass"
echo "$CAP_30D_KEY: $old_cap_30d -> $cap_30d"
