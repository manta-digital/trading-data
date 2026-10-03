#!/usr/bin/env bash
# log_scrub.sh — refuse a log that holds a credential (slice 226, TD1).
#
# Source it, then: log_scrub <log> [secret ...]
# Fails (return 1) when <log> contains any given secret verbatim, or any
# postgres URL carrying a password (`postgres[ql]://user:password@`). Prints
# `LEAK <log>:<line>` for each hit, never the matched text. Returns 0 on a
# clean log. Empty secrets are ignored.
log_scrub() {
  local log="$1"; shift
  local secret hits="" lines
  for secret in "$@"; do
    [ -n "$secret" ] || continue
    lines="$(grep -nF -- "$secret" "$log" | cut -d: -f1 || true)"
    hits+="${lines:+$lines$'\n'}"
  done
  lines="$(grep -nE 'postgres(ql)?://[^:/@[:space:]]+:[^@[:space:]]+@' "$log" | cut -d: -f1 || true)"
  hits+="${lines:+$lines$'\n'}"
  if [ -n "$hits" ]; then
    sort -un <<< "$hits" | sed '/^$/d' | while read -r line; do echo "LEAK $log:$line"; done
    return 1
  fi
  return 0
}
