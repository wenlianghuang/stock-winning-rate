#!/bin/zsh
# 交易日 17:30 後產台股收盤日報。
# 由登入階段的 LaunchAgent 呼叫內建碟副本：
#   ~/Library/LaunchAgents/com.stock.market-close.plist
#   ~/.local/bin/run_market_close.sh
set -u

export TZ=Asia/Taipei
export PATH="/Users/matthuang/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

ROOT="/Volumes/T7_SSD/stock-winning-rate"
UV="/Users/matthuang/.local/bin/uv"
TMPLOG="/tmp/stock-close.log"
REPOLOG="${ROOT}/reports/market/_close.log"

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
  if ! cd "$ROOT"; then
    echo "cd failed: $ROOT"
    echo "Volumes:"
    ls -la /Volumes 2>&1 || true
    exit 2
  fi
  "$UV" run --extra ui --extra stock python main.py market-close
  echo "exit: $?"
} >> "$TMPLOG" 2>&1

if [[ -d "$ROOT/reports" ]]; then
  mkdir -p "$(dirname "$REPOLOG")"
  cp "$TMPLOG" "$REPOLOG" 2>/dev/null || true
fi
