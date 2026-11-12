#!/usr/bin/env bash
# PostToolUse(Edit|Write|MultiEdit): format + lint the changed Python file.
# Exit 2 feeds remaining lint errors back to Claude.
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"
file="$(.venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); print(d.get("tool_input",{}).get("file_path",""))' 2>/dev/null)"
[[ "$file" == *.py && -f "$file" ]] || exit 0
case "$(realpath "$file")" in "$(pwd)"/*) ;; *) exit 0 ;; esac

.venv/bin/ruff format -q "$file"
if ! out="$(.venv/bin/ruff check --fix -q "$file" 2>&1)"; then
  printf 'ruff findings in %s – please fix:\n%s\n' "$file" "$out" >&2
  exit 2
fi
exit 0
