#!/usr/bin/env bash
# Idempotent setup: venv + pinned deps, Playwright browser check, Streamlit dev server, smoke test.
# Usage: ./init.sh          set up, (re)use running server, run smoke test
#        ./init.sh stop     stop the dev server started by this script
# Never calls sudo. Exits non-zero with a clear message if something is missing.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${EEG_PORT:-8501}"
VENV=.venv
PY="$VENV/bin/python"
LOG_DIR=logs
PID_FILE="$LOG_DIR/streamlit.pid"
URL="http://localhost:$PORT"

say()  { printf '\033[1;34m[init]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[init] FEHLER:\033[0m %s\n' "$*" >&2; exit 1; }

healthy() { curl -fsS --max-time 2 "$URL/_stcore/health" >/dev/null 2>&1; }

if [[ "${1:-}" == "stop" ]]; then
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    kill "$(cat "$PID_FILE")" && rm -f "$PID_FILE" && say "Server gestoppt."
  else
    say "Kein von init.sh gestarteter Server aktiv."
  fi
  exit 0
fi

# 1. System prerequisites (check only)
command -v python3.12 >/dev/null || fail "python3.12 fehlt. Installieren: sudo apt install python3.12 python3.12-venv"
command -v curl >/dev/null || fail "curl fehlt. Installieren: sudo apt install curl"
command -v git >/dev/null || fail "git fehlt. Installieren: sudo apt install git"

# 2. venv + dependencies (reinstall only when pyproject/lock changed)
[[ -x "$PY" ]] || { say "Erzeuge $VENV"; python3.12 -m venv "$VENV" || fail "venv fehlgeschlagen (python3.12-venv installiert?)"; }
STAMP="$VENV/.deps.sha"
deps_hash() { { cat pyproject.toml; [[ -f requirements.lock ]] && cat requirements.lock; true; } | sha256sum | cut -d' ' -f1; }
want="$(deps_hash)"
if [[ "$(cat "$STAMP" 2>/dev/null)" != "$want" ]]; then
  say "Installiere Abhängigkeiten"
  "$PY" -m pip install -q --upgrade pip
  if [[ -f requirements.lock ]]; then
    "$PY" -m pip install -q -r requirements.lock
    "$PY" -m pip install -q --no-deps -e ".[dev]"
  else
    "$PY" -m pip install -q -e ".[dev]"
    "$PY" -m pip freeze --exclude-editable > requirements.lock
    say "requirements.lock erzeugt"
  fi
  deps_hash > "$STAMP"
else
  say "Abhängigkeiten aktuell"
fi

# 3. Playwright browser (download is user-level, no sudo) + system-lib check
"$VENV/bin/playwright" install chromium >/dev/null 2>&1 || fail "Playwright-Chromium-Download fehlgeschlagen (Netzwerk?)"
if ! "$PY" - <<'EOF' >/dev/null 2>&1
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    p.chromium.launch().close()
EOF
then
  fail "Chromium startet nicht – Systembibliotheken fehlen. Einmalig selbst ausführen:
       sudo $VENV/bin/playwright install-deps chromium"
fi

# 4. Dev server (reuse if healthy)
mkdir -p "$LOG_DIR" data
if healthy; then
  say "Server läuft bereits auf $URL"
else
  say "Starte Streamlit auf $URL (Log: $LOG_DIR/streamlit.log)"
  nohup "$PY" -m streamlit run app/streamlit_app.py --server.port "$PORT" --server.headless true \
    >"$LOG_DIR/streamlit.log" 2>&1 &
  echo $! > "$PID_FILE"
  for _ in $(seq 1 60); do healthy && break; sleep 0.5; done
  healthy || fail "Server nicht erreichbar. Siehe $LOG_DIR/streamlit.log"
fi

# 5. Smoke test: fast unit tests + E2E smoke against the running server
say "Smoke-Test"
"$VENV/bin/pytest" -q tests/unit/test_package.py || fail "Unit-Smoke-Test rot"
EEG_PORT="$PORT" "$VENV/bin/pytest" -q -m e2e tests/e2e/test_smoke.py || fail "E2E-Smoke-Test rot"

say "OK – App: $URL"
