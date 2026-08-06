#!/usr/bin/env bash
# 每日 07:00 自動執行(crontab 呼叫):
#   auth refresh(保 cookie 新鮮)→ 每個頻道: run_daily(新聞→選題→來源)→ 影片 → YouTube 上傳
# 逐頻道 fail-fast: 單一頻道失敗不影響其他頻道,全部記錄在 logs/daily_cron.log
set -u

POC=/home/ian/github-project/notebooklm-py
LOG="$POC/logs/daily_cron.log"
PY="$POC/.venv/bin/python"
mkdir -p "$POC/logs"

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') 開始 ====="
  "$POC/.venv/bin/notebooklm" auth refresh --quiet && echo "[OK] auth refresh" || echo "[FAIL] auth refresh"

  for CH in embedded tech; do
    echo "----- 頻道: $CH -----"
    "$PY" "$POC/scripts/run_daily.py" --channel "$CH" \
      || { echo "[FAIL] run_daily $CH"; continue; }
    "$PY" "$POC/scripts/run_video_pipeline.py" --channel "$CH" \
      || { echo "[FAIL] video_pipeline $CH"; continue; }
    "$PY" "$POC/scripts/youtube_upload.py" --channel "$CH" \
      || { echo "[FAIL] youtube_upload $CH"; continue; }
    echo "[OK] 頻道 $CH 完成"
  done

  echo "----- Shorts(tech 頻道 TOP 1)-----"
  "$PY" "$POC/scripts/run_shorts_pipeline.py" --channel tech \
    || { echo "[FAIL] shorts_pipeline"; exit 1; }
  # 注意: run_shorts_pipeline.py 用 UTC 日期命名檔案 — cron 也必須用 UTC,否則 07:00(23:00 UTC)必然對不上
  SHORTS_FILE="$POC/output/tech/shorts_$(date -u +%Y-%m-%d).mp4"
  "$PY" "$POC/scripts/youtube_upload.py" --channel tech --file "$SHORTS_FILE" \
    || { echo "[FAIL] youtube_upload shorts"; exit 1; }
  echo "[OK] Shorts 完成"
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') 結束 ====="
} >> "$LOG" 2>&1
