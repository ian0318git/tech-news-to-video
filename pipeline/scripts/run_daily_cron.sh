#!/usr/bin/env bash
# 每日 08:00 自動執行(crontab 呼叫,墨爾本/雪梨時間 AEST):
#   auth refresh(保 cookie 新鮮)→ 每個頻道: run_daily(新聞→選題→來源)→ 影片 → YouTube 上傳
# 頻道清單與 Shorts 來源由 config/channels.json 驅動(單一真相,加頻道不需改 cron)。
# 逐頻道 fail-fast: 單一頻道失敗不影響其他頻道,全部記錄在 logs/daily_cron.log
set -u

POC=/home/ian/github-project/notebooklm-py
LOG="$POC/logs/daily_cron.log"
PY="$POC/.venv/bin/python"
CONFIG="$POC/config/channels.json"
mkdir -p "$POC/logs"

FAILED=0
{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') 開始 ====="
  if "$POC/.venv/bin/notebooklm" auth refresh --quiet; then
    echo "[OK] auth refresh"
  else
    echo "[FAIL] auth refresh"
    FAILED=1
  fi

  # 頻道與 Shorts 來源從 config 讀取
  CHANNELS=$("$PY" -c "import json; print(' '.join(c['slug'] for c in json.load(open('$CONFIG'))['channels']))")
  SHORTS_CH=$("$PY" -c "import json; print(json.load(open('$CONFIG')).get('shorts_channel', 'tech'))")
  echo "[INFO] 頻道清單: $CHANNELS | Shorts 來源: $SHORTS_CH"

  SHORTS_OK=0
  for CH in $CHANNELS; do
    echo "----- 頻道: $CH -----"
    "$PY" "$POC/scripts/run_daily.py" --channel "$CH" \
      || { echo "[FAIL] run_daily $CH"; FAILED=1; continue; }
    "$PY" "$POC/scripts/run_video_pipeline.py" --channel "$CH" \
      || { echo "[FAIL] video_pipeline $CH"; FAILED=1; continue; }
    # 長片加品牌片頭/片尾,上傳品牌版
    TODAY_FILE="$POC/output/$CH/video_$(TZ=Australia/Sydney date +%Y-%m-%d).mp4"
    "$PY" "$POC/scripts/brand_video.py" --file "$TODAY_FILE" \
      || { echo "[FAIL] brand_video $CH"; FAILED=1; continue; }
    "$PY" "$POC/scripts/youtube_upload.py" --channel "$CH" \
      --file "$POC/output/$CH/video_$(TZ=Australia/Sydney date +%Y-%m-%d).branded.mp4" \
      || { echo "[FAIL] youtube_upload $CH"; FAILED=1; continue; }
    echo "[OK] 頻道 $CH 完成"
    [ "$CH" = "$SHORTS_CH" ] && SHORTS_OK=1
  done

  echo "----- Shorts($SHORTS_CH 頻道 TOP 1)-----"
  if [ "$SHORTS_OK" = "1" ]; then
    "$PY" "$POC/scripts/run_shorts_pipeline.py" --channel "$SHORTS_CH" \
      || { echo "[FAIL] shorts_pipeline"; FAILED=1; }
    # 注意: pipeline 的「今天」= 雪梨當地日期 — cron 必須用同一時區,否則 06:00(前一日 20:00 UTC)必然對不上
    SHORTS_FILE="$POC/output/$SHORTS_CH/shorts_$(TZ=Australia/Sydney date +%Y-%m-%d).mp4"
    "$PY" "$POC/scripts/youtube_upload.py" --channel "$SHORTS_CH" --file "$SHORTS_FILE" \
      || { echo "[FAIL] youtube_upload shorts"; FAILED=1; }
    if [ "$FAILED" = "0" ]; then
      echo "[OK] Shorts 完成"
    fi
  else
    echo "[FAIL] $SHORTS_CH 頻道未成功,跳過 Shorts(避免用昨天的舊新聞)"
    FAILED=1
  fi
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') 結束 ====="
} >> "$LOG" 2>&1

# 完成 marker 只在「全部成功」時寫 — 有失敗則讓補跑機制重試
if [ "$FAILED" = "0" ]; then
  touch "$POC/logs/done_$(TZ=Australia/Sydney date +%Y-%m-%d).marker"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') [CATCHUP] 本次有失敗,不寫 marker(每 15 分鐘的補跑會重試)" >> "$LOG"
  exit 1
fi
