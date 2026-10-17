#!/usr/bin/env bash
# Print the CHANGELOG.md body for one version (argument: tag such as v0.24.1).
# Fails if the version has no section or the section is empty.
set -euo pipefail
ver="${1#v}"
body="$(awk -v ver="$ver" '
  /^## \[/ { if (on) exit; on = index($0, "[" ver "]") > 0; next }
  on { print }
' CHANGELOG.md)"
if [ -z "$(printf '%s' "$body" | tr -d '[:space:]')" ]; then
  echo "changelog_section: no CHANGELOG section for $ver" >&2
  exit 1
fi
printf '%s\n' "$body"
