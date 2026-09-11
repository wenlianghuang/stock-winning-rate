#!/bin/zsh
# 週五 17:30 後產市場週報（給 cron 呼叫）。
# macOS cron 常讀不到外接碟上的 .sh；crontab 請指向內建碟副本，例如：
#   ~/.local/bin/run_market_weekly.sh
set -u

export TZ=Asia/Taipei
export PATH="/Users/matthuang/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

ROOT="/Volumes/T7_SSD/stock-winning-rate"
UV="/Users/matthuang/.local/bin/uv"
TMPLOG="/tmp/stock-weekly.log"
REPOLOG="${ROOT}/reports/market/_weekly.log"

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
  if ! cd "$ROOT"; then
    echo "cd failed: $ROOT"
    echo "Volumes:"
    ls -la /Volumes 2>&1 || true
    exit 2
  fi
  "$UV" run --extra ui --extra stock python main.py market-weekly
  echo "exit: $?"
} >> "$TMPLOG" 2>&1

if [[ -d "$ROOT/reports" ]]; then
  mkdir -p "$(dirname "$REPOLOG")"
  cp "$TMPLOG" "$REPOLOG" 2>/dev/null || true
fi
