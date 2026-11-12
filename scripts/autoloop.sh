#!/usr/bin/env bash
# Optional headless loop: one feature per Claude session, until done, FAIL or limit.
# Usage: scripts/autoloop.sh [MAX_ITERATIONS]   (default 5)
#
# Safety rules:
# - Permissions come ONLY from .claude/settings.json (no --dangerously-skip-permissions).
#   Anything not allow-listed is denied in headless mode.
# - Immediate stop on: dirty worktree, red init.sh, missing/FAIL LOOP_STATUS, no progress.
# - Never pushes. Logs per iteration in logs/autoloop/ (gitignored).
set -euo pipefail
cd "$(dirname "$0")/.."

MAX="${1:-5}"
[[ "$MAX" =~ ^[0-9]+$ && "$MAX" -ge 1 ]] || { echo "MAX_ITERATIONS muss eine positive Zahl sein" >&2; exit 2; }
LOG_DIR=logs/autoloop
mkdir -p "$LOG_DIR"
command -v claude >/dev/null || { echo "claude CLI nicht gefunden" >&2; exit 2; }

PROMPT='Arbeite eine Session nach dem Session-Protokoll in CLAUDE.md ab: Skill start-session, genau EIN Feature umsetzen inkl. Tests, dann Skill finish-feature (Evaluator, Reviewer, Commit, progress.md). Keine Rückfragen – du läufst headless; wenn du blockiert bist, dokumentiere es in progress.md, committe einen lauffähigen Stand und beende mit LOOP_STATUS: FAIL <id>. Die letzte Zeile deiner Antwort muss LOOP_STATUS: PASS <id> oder LOOP_STATUS: FAIL <id> sein.'

stop() { echo "[autoloop] STOPP: $*" | tee -a "$LOG_DIR/summary.log" >&2; exit 1; }
open_count() { jq '[.[] | select(.passes|not)] | length' feature_list.json; }

# Preflight: project allowlist is ignored in untrusted workspaces (headless can't show the dialog)
trusted="$(jq -r --arg p "$PWD" '.projects[$p].hasTrustDialogAccepted // false' ~/.claude.json 2>/dev/null || echo false)"
[[ "$trusted" == "true" ]] || stop "Workspace nicht vertraut - Projekt-Allowlist würde ignoriert.
  Einmalig interaktiv 'claude' in $PWD starten und den Trust-Dialog bestätigen."

for i in $(seq 1 "$MAX"); do
  ts="$(date +%Y%m%d-%H%M%S)"
  log="$LOG_DIR/iter-$i-$ts.jsonl"
  echo "[autoloop] Iteration $i/$MAX ($ts)" | tee -a "$LOG_DIR/summary.log"

  [[ -z "$(git status --porcelain)" ]] || stop "uncommittete Änderungen vor Iteration $i"
  ./init.sh >"$LOG_DIR/init-$i-$ts.log" 2>&1 || stop "init.sh rot (siehe $LOG_DIR/init-$i-$ts.log)"
  before="$(open_count)"
  [[ "$before" -gt 0 ]] || { echo "[autoloop] Keine offenen Features – fertig." | tee -a "$LOG_DIR/summary.log"; exit 0; }
  head_before="$(git rev-parse HEAD)"

  claude -p "$PROMPT" --output-format stream-json --verbose >"$log" 2>&1 \
    || stop "claude beendet mit Fehler (siehe $log)"

  # stream-json may be preceded by plain warning lines -> parse JSON lines only
  result_json="$(grep '^{' "$log" | jq -c 'select(.type=="result")' 2>/dev/null | tail -1)"
  denials="$(jq -r '[.permission_denials[]? | .tool_name + ": " + ((.tool_input.command // .tool_input.file_path // "") | .[0:120])] | .[]' <<<"$result_json" 2>/dev/null || true)"
  [[ -z "$denials" ]] || { echo "[autoloop] $i: verweigerte Aufrufe:"; sed 's/^/    /' <<<"$denials"; } | tee -a "$LOG_DIR/summary.log"
  status="$(jq -r '.result // empty' <<<"$result_json" 2>/dev/null | grep -oE 'LOOP_STATUS: (PASS|FAIL) [A-Za-z0-9-]+' | tail -1 || true)"
  echo "[autoloop] $i: ${status:-kein LOOP_STATUS}" | tee -a "$LOG_DIR/summary.log"

  [[ -z "$(git status --porcelain)" ]] || stop "uncommittete Änderungen nach Iteration $i"
  [[ "$status" == LOOP_STATUS:\ PASS* ]] || stop "Iteration $i ohne PASS: ${status:-kein Status}"
  [[ "$(git rev-parse HEAD)" != "$head_before" ]] || stop "PASS gemeldet, aber kein neuer Commit"
  [[ "$(open_count)" -lt "$before" ]] || stop "PASS gemeldet, aber kein Feature auf passes=true"
  .venv/bin/python scripts/guard.py --base "$head_before" >>"$LOG_DIR/summary.log" 2>&1 \
    || stop "guard verletzt gegenüber Stand vor Iteration $i"
done
echo "[autoloop] Iterationslimit $MAX erreicht." | tee -a "$LOG_DIR/summary.log"
