#!/bin/bash
# Test Intelligence Platform — macOS one-click launcher (double-click in Finder).
set -e
cd "$(dirname "$0")"

if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck source=/dev/null
  source ".venv/bin/activate"
elif [[ -f "venv/bin/activate" ]]; then
  # shellcheck source=/dev/null
  source "venv/bin/activate"
else
  echo "[start_app] No .venv or venv found. Create one and install deps, e.g.:"
  echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  echo "[start_app] Trying system Python..."
fi

if command -v streamlit &>/dev/null; then
  streamlit run app.py
else
  python3 -m streamlit run app.py || python -m streamlit run app.py
fi

echo ""
echo "[start_app] Process ended. Press Enter to close this window."
read -r _
