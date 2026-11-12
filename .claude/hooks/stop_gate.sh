#!/usr/bin/env bash
# Stop hook: before Claude ends its turn, run typecheck + guard + unit/property tests
# if code changed since HEAD. Exit 2 blocks the stop and feeds the failure back.
# Blocks at most once in a row (stop_hook_active) to avoid endless loops.
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"
input="$(cat)"
active="$(printf '%s' "$input" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin).get("stop_hook_active", False))' 2>/dev/null)"

# Only when Python code, tests or feature_list changed (staged, unstaged or untracked)
changed="$(git status --porcelain -- src app tests scripts feature_list.json 2>/dev/null)"
[[ -n "$changed" ]] || exit 0

out="$( { .venv/bin/mypy 2>&1 | tail -20; exit "${PIPESTATUS[0]}"; } )"; rc_mypy=$?
out_guard="$(.venv/bin/python scripts/guard.py 2>&1)"; rc_guard=$?
out_test="$(.venv/bin/pytest -q -x 2>&1 | tail -25; exit "${PIPESTATUS[0]}")"; rc_test=$?

if (( rc_mypy || rc_guard || rc_test )); then
  {
    echo "Stop gate RED (mypy=$rc_mypy guard=$rc_guard pytest=$rc_test). Fix before finishing:"
    (( rc_mypy )) && echo "--- mypy ---" && echo "$out"
    (( rc_guard )) && echo "--- guard ---" && echo "$out_guard"
    (( rc_test )) && echo "--- pytest ---" && echo "$out_test"
  } >&2
  [[ "$active" == "True" ]] && exit 0   # already blocked once; let the user see it
  exit 2
fi
exit 0
