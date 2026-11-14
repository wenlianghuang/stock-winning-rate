# 排程：開盤前、收盤、週報

三份報告由 macOS 登入階段的 LaunchAgent 到點執行。不要再寫進 crontab。

到點時這台 Mac 要處於已登入狀態（螢幕鎖著可以，登出到登入畫面不行）。`agy` 的登入在鑰匙圈裡，只有這個階段讀得到。

---

## 現在的時間

`Weekday`：1＝週一，2＝週二，…，6＝週六。0 和 7 都是週日。小時是 24 小時制，時區 `Asia/Taipei`。

| 工作 | 時間 | plist | 腳本 | 日誌 |
|---|---|---|---|---|
| 開盤前 brief | 週二～週六 06:00 | `~/Library/LaunchAgents/com.stock.premarket-schedule.plist` | `~/.local/bin/run_premarket_schedule.sh` | `/tmp/stock-schedule.log` |
| 收盤日報 | 週一～週五 17:30 | `~/Library/LaunchAgents/com.stock.market-close.plist` | `~/.local/bin/run_market_close.sh` | `/tmp/stock-close.log` |
| 市場週報 | 週五 17:30 | `~/Library/LaunchAgents/com.stock.market-weekly.plist` | `~/.local/bin/run_market_weekly.sh` | `/tmp/stock-weekly.log` |

倉庫裡的對應腳本在 `scripts/run_premarket_schedule.sh`、`scripts/run_market_close.sh`、`scripts/run_market_weekly.sh`。LaunchAgent 跑的是 `~/.local/bin` 那份。改了倉庫腳本後，要再複製過去：

```bash
cp scripts/run_premarket_schedule.sh ~/.local/bin/run_premarket_schedule.sh
cp scripts/run_market_close.sh ~/.local/bin/run_market_close.sh
cp scripts/run_market_weekly.sh ~/.local/bin/run_market_weekly.sh
chmod +x ~/.local/bin/run_premarket_schedule.sh ~/.local/bin/run_market_close.sh ~/.local/bin/run_market_weekly.sh
```

---

## 手動跑一次

在終端機執行。成功時日誌最後一行是 `exit: 0`。

```bash
~/.local/bin/run_premarket_schedule.sh
~/.local/bin/run_market_close.sh
~/.local/bin/run_market_weekly.sh
```

網站只把有 Markdown 的報告當成已產出。只有 `facts.json`、沒有 `.md`，頁面仍會顯示尚未產生。

| 工作 | 成功時應出現的檔 |
|---|---|
| 開盤前 | `reports/market/{交易日}/tw_market_daily.md` |
| 收盤 | `reports/market/{交易日}/tw_market_close.md` |
| 週報 | `reports/market/{週最後交易日}/tw_market_weekly.md` |

---

## 改日期或時間

改對應 plist 裡的 `Weekday`、`Hour`、`Minute`。一天一組 `<dict>`。開盤前、收盤有多組；週報只有一組。

存檔不會生效。系統裡還掛著舊時間，必須先卸下再載入。把標籤換成要改的那一個：

```bash
uid=$(id -u)
launchctl bootout gui/$uid/com.stock.premarket-schedule
launchctl bootstrap gui/$uid ~/Library/LaunchAgents/com.stock.premarket-schedule.plist
```

另外兩個標籤是 `com.stock.market-close`、`com.stock.market-weekly`。

確認系統吃到的是新時間（不要只看檔案）：

```bash
launchctl print gui/$uid/com.stock.premarket-schedule | grep -E 'Weekday|Hour|Minute|state ='
```

`state = not running` 是在等下次時間，不是故障。

兩種常見錯誤：

| 訊息 | 意思 | 下一步 |
|---|---|---|
| `Boot-out failed: 3: No such process` | 這個標籤目前沒載入，沒有東西可卸 | 直接做下一行 `bootstrap` |
| `Bootstrap failed: 5: Input/output error` | 這個標籤已經載入，不能再載一次 | 先 `bootout`，再 `bootstrap`。不用 sudo |

---

## 看有沒有載入

```bash
uid=$(id -u)
launchctl print gui/$uid/com.stock.premarket-schedule
launchctl print gui/$uid/com.stock.market-close
launchctl print gui/$uid/com.stock.market-weekly
```

`Could not find service` 代表還沒載入。plist 還在時，用上一節的 `bootstrap` 載入。

---

## 為什麼不用 crontab

2026-11-13～11-14 的紀錄。crontab 有準時把腳本叫起來（cron 信件的時間對得上），報告仍沒寫出。

1. **收盤腳本沒有執行權。** 週五 17:30，`~/.local/bin/run_market_close.sh` 是 `-rw-r--r--`，zsh 回 `permission denied`。更早幾天路徑還寫成 `/User/matthuang/...`（少了 `s`），那些天是 `no such file or directory`。
2. **`agy` 讀不到鑰匙圈。** 11 月 11 日 06:00 的開盤前排程還成功。從 11 月 12 日 06:00 起，同一條 crontab 裡的 `agy` 回 `authentication required`。終端機手動跑可以，因為那個視窗在圖形登入階段；cron 是背景計時器，不在這個階段。週五 17:45 的週報有抓完籌碼、寫出 `tw_market_weekly.facts.json`，敘事沒寫出，網站因此仍顯示尚未產生。
3. **`launchctl asuser` 不能從 cron 裡跳進登入階段。** 11 月 13 日 18:28 曾加進三支腳本。週五 18:30 的週報、週六 06:00 的開盤前都停在 `Could not switch to audit session ... Operation not permitted`，Python 和 `agy` 都還沒執行。這段已從腳本移除。

因此改由 LaunchAgent 在登入階段自己到點啟動，並刪掉 crontab 裡這三行。crontab 現在只留環境變數與一行註解。
