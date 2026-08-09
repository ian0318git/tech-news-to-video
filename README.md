# Tech News to Video — 每日自動影片 pipeline

自動完成 **News → AI 選題 → NotebookLM 影片 → YouTube 上傳** 的每日影片流水線。
在純命令列 headless Ubuntu VM 上運行(不需桌面/瀏覽器)。

## 目錄結構

```
/home/ian/github-project/notebooklm-py/   目前目錄(即本 repo 所在工作目錄)
├── .venv/                  # Python 3.12 venv(notebooklm-py + playwright + pytest)
├── config/
│   └── channels.json       # 頻道定義(關鍵字 / 影片標題前綴 / 語系)
├── scripts/
│   ├── _common.py          # 共用: 日誌、CLI 執行器、Gemini 呼叫、頻道解析
│   ├── _youtube.py         # YouTube OAuth(device flow)+ token 管理
│   ├── fetch_news.py       # Google News RSS 抓取
│   ├── rank_news.py        # Gemini 排名選 TOP 1
│   ├── collect_sources.py  # 官方來源收集 + 可達性驗證(含轉址解析)
│   ├── run_daily.py        # 每日選題 pipeline(上述三步,per-channel)
│   ├── run_video_pipeline.py  # 端到端: notebook → 來源 → 影片 → 下載(冪等)
│   ├── youtube_auth.py     # YouTube 首次授權(device flow)
│   ├── youtube_upload.py   # YouTube 上傳(private,防重複)
│   ├── run_daily_cron.sh   # cron 入口(08:00 呼叫)
│   └── check_auth.py       # master-token 認證檢查
├── tests/                  # 單元測試(pytest,16 個,mock 不連網)
├── output/
│   ├── embedded/           # 每頻道獨立目錄
│   │   ├── news_raw.json / top1.json / ranking.json / sources.json
│   │   ├── video_<日期>.mp4
│   │   ├── youtube_uploads.json   # 上傳記錄(防重複)
│   │   └── pipeline_state.json    # notebook 記錄(冪等)
│   └── tech/               # 同上
├── logs/                   # 每步驟 log + daily_cron.log
├── .env                    # 機密(GEMINI_API_KEY;chmod 600,已 gitignore)
└── AUTH_MASTER_TOKEN.md    # NotebookLM headless 認證步驟
```

## 執行

```bash
source /home/ian/github-project/notebooklm-py/.venv/bin/activate
cd /home/ian/github-project/notebooklm-py

# 選題 pipeline(所有頻道,或 --channel tech)
python scripts/run_daily.py                 # 所有頻道
python scripts/run_daily.py --channel tech  # 單一頻道

# 端到端(notebook → 影片 → 下載;冪等,可安全重跑)
python scripts/run_video_pipeline.py --channel tech

# YouTube 上傳(private,防重複;--force 強制重傳)
python scripts/youtube_upload.py --channel tech

# 測試
.venv/bin/pytest tests/
```

**冪等設計**: 重跑不會重複建 notebook、重複加來源、重複生成影片(當天影片已存在即跳過)、重複上傳。失敗後直接重跑即可從斷點繼續。

## 每日排程(cron)

已安裝:`0 8 * * *`(墨爾本/雪梨時間 08:00,依 crontab 頂部 `TZ=Australia/Sydney`,自動處理夏令時):

```
auth refresh(保 cookie)→ 每個頻道: run_daily → 長片 → 上傳
→ Shorts(tech 頻道 TOP 1,60 秒直式)→ 上傳
```

每日產出 **3 支影片**:2 支長片(embedded + tech)+ 1 支 Shorts(tech 新聞)。

- 每頻道獨立 fail-fast:單一頻道失敗不影響其他;全部記錄在 `logs/daily_cron.log`
- 檢查前一天的結果:`grep -E "\[OK\]|\[FAIL\]" logs/daily_cron.log | tail`
- 上傳隱私由 `.env` 的 `UPLOAD_PRIVACY` 控制(目前 public,自動公開)

## YouTube 上傳

- **認證**:device flow(首次在 VM 執行 `python scripts/youtube_auth.py`,用任何裝置瀏覽器到 google.com/device 輸入代碼);token 存 `output/youtube_token.json`(0600),自動 refresh
- **憑證**:`output/client_secret.json`(Google Cloud → OAuth 用戶端 ID → **TVs and Limited Input devices** — 桌面應用程式類型不支援 device flow)
- 上傳為 **private**(可 `--privacy unlisted/public` 覆寫),標題自動帶頻道前綴 + 今日新聞

## 頻道(可自行增減,改 config/channels.json)

| slug | 關鍵字 | 標題前綴 |
|---|---|---|
| embedded | embedded linux | Embedded Linux Daily |
| tech | technology OR artificial intelligence | TechSnack Daily |

## 額度限制(2026-08 現況)

| 工具 | 限制 | 我們的用量 |
|---|---|---|
| NotebookLM Pro(你的方案) | 20 支影片/天(滾動 24h) | 3/天 |
| Gemini API 免費層 | ~1,500 請求/天 | 4/天 |
| YouTube Data API | videos.insert 100 次/天 | 3/天 |

## 正式系統架構(目標)

```
News → AI → NotebookLM → Video → YouTube
                         DAILY 08:00 AEST
                              │
                              ▼
                     ┌────────────────┐
                     │ Google News    │
                     │ RSS / Search   │
                     └───────┬────────┘
                             │
                             ▼
                     ┌────────────────┐
                     │   Gemini Pro   │
                     │                │
                     │ News Ranking   │
                     │ Deep Research  │
                     └───────┬────────┘
                             │
                         TOP 1 NEWS
                             │
                             ▼
                  ┌──────────────────────┐
                  │   Source Collector   │
                  │                      │
                  │ Official docs        │
                  │ Kernel.org           │
                  │ GitHub               │
                  │ Vendor               │
                  │ Technical media      │
                  └──────────┬───────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  notebooklm-py  │
                    │                 │
                    │ Create Notebook │
                    │ Add Sources     │
                    │ Video Overview  │
                    │ Download MP4    │
                    └────────┬────────┘
                             │
                             ▼
                         video.mp4
                             │
                             ▼
                    ┌─────────────────┐
                    │  YouTube API    │
                    │                 │
                    │ Upload Private  │
                    └────────┬────────┘
                             │
                             ▼
                    Your YouTube Channel
```

設計決策與里程碑見 `DECISIONS.md`。
