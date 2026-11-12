#!/usr/bin/env bash
# Full local quality gate: format check, lint, typecheck, harness guard, unit+property tests.
# Usage: scripts/check.sh            all offline checks
#        scripts/check.sh --e2e      additionally Playwright E2E (app must be startable)
#        scripts/check.sh --all      additionally slow perf tests
set -euo pipefail
cd "$(dirname "$0")/.."
B=.venv/bin
step() { printf '\n\033[1;34m== %s ==\033[0m\n' "$*"; }

step "ruff format --check";  "$B/ruff" format --check .
step "ruff check";           "$B/ruff" check .
step "mypy --strict";        "$B/mypy"
step "harness guard";        "$B/python" scripts/guard.py
step "pytest (unit+property)"; "$B/pytest" -q

case "${1:-}" in
  --e2e) step "pytest e2e"; "$B/pytest" -q -m e2e ;;
  --all) step "pytest e2e + slow"; "$B/pytest" -q -m "e2e or slow" ;;
esac
printf '\n\033[1;32mALL CHECKS PASSED\033[0m\n'
