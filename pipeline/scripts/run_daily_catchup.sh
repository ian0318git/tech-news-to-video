#!/usr/bin/env bash
# 錯過排程時的補跑(VM 暫停恢復後自動救回當天影片):
#   今天已完成(done_<date>.marker)→ 直接跳出
#   今天未完成 → 執行完整 pipeline(冪等,安全重跑)
# 與進行中的主 run 以 flock 互斥,不會並發執行。
# crontab 搭配: */15 6-12 * * * run_daily_catchup.sh
set -u

POC=/home/ian/github-project/notebooklm-py
TODAY=$(TZ=Australia/Sydney date +%Y-%m-%d)
MARKER="$POC/logs/done_${TODAY}.marker"
LOCK="$POC/logs/pipeline.lock"

[ -f "$MARKER" ] && exit 0          # 今天已完成
flock -n "$LOCK" true 2>/dev/null || exit 0   # 有執行中 → 跳出

echo "$(date '+%Y-%m-%d %H:%M:%S') [CATCHUP] 錯過排程,開始補跑..." >> "$POC/logs/catchup.log"
# 主 run 由 run_daily_cron.sh 自己持鎖 — 這裡不要再 flock 包一層(會與 cron 內部的鎖互斥而自鎖死)
"$POC/scripts/run_daily_cron.sh" >> "$POC/logs/catchup.log" 2>&1
