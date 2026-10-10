#!/usr/bin/env bash
# Run the repository's pre-commit hooks over the agent's change, on the same
# runner the agent worked on, so failures go back to the same session.
#
# usage: run_hooks.sh <config-file> <log-file>
#
# <config-file> is the hook config as it was BEFORE the agent ran. Two passes:
#   1. every hook except Gatehouse, with the reviewer key absent from the
#      environment and from disk. These hooks run repository code.
#   2. Gatehouse alone, with the key on disk for that one command. Its entry
#      pipes the staged diff to a container and runs no repository code.
set -uo pipefail

config="$1"
log="$2"
: > "$log"

key="${OPENROUTER_API_KEY:-}"
unset OPENROUTER_API_KEY

git add -A
if [ ! -f "$config" ] || [ -z "$(git diff --cached --name-only)" ]; then
  echo "passed=true" >> "$GITHUB_OUTPUT"
  exit 0
fi

SKIP=gatehouse timeout 900 pre-commit run --config "$config" >> "$log" 2>&1
status=$?
git add -A   # formatters rewrite files in place

if [ -n "$key" ] && grep -q 'id: gatehouse' "$config"; then
  keyfile="$HOME/.config/mcp-env/gatehouse.env"
  trap 'rm -f "$keyfile"' EXIT
  mkdir -p "$(dirname "$keyfile")"
  (umask 077 && printf 'OPENROUTER_API_KEY=%s\n' "$key" > "$keyfile")
  timeout 600 pre-commit run gatehouse --config "$config" >> "$log" 2>&1 || status=1
  rm -f "$keyfile"
fi

if [ "$status" -eq 0 ]; then
  echo "passed=true" >> "$GITHUB_OUTPUT"
else
  echo "passed=false" >> "$GITHUB_OUTPUT"
  echo "::group::pre-commit output"; tail -n 200 "$log"; echo "::endgroup::"
fi
