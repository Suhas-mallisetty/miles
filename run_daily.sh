#!/usr/bin/env bash
# Daily price check. Schedule with cron or launchd (see README).
# Runs twice a day during the Black Friday -> Cyber Monday window if you schedule it that way;
# tracker.py itself refuses to run after Dec 1 (use --force to override).
set -euo pipefail
cd "$(dirname "$0")"
# Optional: Amazon prices via Keepa. Put `export KEEPA_API_KEY=...` in ~/.gift_tracker_env
[ -f "$HOME/.gift_tracker_env" ] && . "$HOME/.gift_tracker_env"
mkdir -p logs
python3 tracker.py check >> "logs/$(date +%F).log" 2>&1
# macOS: pop a notification when anything hits its target price
if command -v osascript >/dev/null && python3 tracker.py report | grep -q "DEAL"; then
  osascript -e 'display notification "A gift hit its target price, open dashboard.html" with title "Gift tracker"'
fi
