#!/usr/bin/env bash
# env_value.sh — the one way the backup tier reads a key from an env file.
#
# Source it, then: env_value <file> <KEY>. Prints the value of the first line
# `KEY=value` (anchored: `KEY2=` does not match `KEY`), with surrounding
# double quotes stripped; prints nothing when the key is absent. Never
# `source`s the file: a `$` in a password would be shell-expanded (runbook 200).
env_value() {
  { grep "^$2=" "$1" || true; } | head -1 | sed 's/^[^=]*=//' | tr -d '"'
}
