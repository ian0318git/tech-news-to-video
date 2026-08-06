# DECISIONS — News → AI → Video 正式系統(階段 1: 新聞抓取 + Gemini 選題)

## 背景(任務錨點)

POC(notebooklm-py 影片流程)已驗證通過。本階段實作正式系統的
「News → AI」前半段: ① Google News RSS 抓取 → ② Gemini 排名選 TOP 1
→ ③ Source Collector 收集官方來源。YouTube 上傳與 cron 排程留待下階段。

## 設計決策

| # | 決策 | 理由 |
|---|---|---|
| D1 | 新聞來源用 **Google News RSS**(`news.google.com/rss/search`)| 免費、不需 API key、符合架構圖的 "RSS / Search";關鍵字可經 `.env` 調整 |
| D2 | Gemini 用**直接 REST 呼叫**(`generativelanguage.googleapis.com`),不新增 SDK | venv 已有 `httpx`(notebooklm-py 依賴),零新依賴 |
| D3 | 模型預設 `gemini-2.5-flash`,可經 `GEMINI_MODEL` 覆寫 | flash 便宜快;有 Pro 需求再換 |
| D4 | 排名/收集皆要求 **JSON 結構化輸出**(`responseMimeType=application/json`)並驗證 schema | 下游 pipeline 可直接吃,避免解析自由文字 |
| D5 | 每步獨立腳本、可單獨重跑、fail-fast | 沿用 POC 哲學 — 失敗停住並留 log,不猜測 |
| D6 | 祕密(API key)放 `.env`(chmod 600),`load_env()` 載入 | 不進 repo、不進 env 歷史 |
| D7 | `collect_sources.py` 對每個建議 URL 做 **HTTP 可達性檢查** | 避免把壞連結餵給 NotebookLM 當來源 |
| D8 | 原文新聞 URL 永遠是第一個來源 | 保證至少有 1 個可加來源 |

## 無測試聲明

本階段**無自動化測試**。手動驗證方式: 依序執行 `run_daily.py` 各步,
檢查 `logs/*.log` 的 `[PASS]`/`[FAIL]` 與 `output/` 的 JSON 內容
(`news_raw.json` → `ranking.json`/`top1.json` → `sources.json`)。
正式上線前應補: fetch 解析單元測試(mock RSS)、Gemini 回應 schema 測試、URL 檢查測試。

## 設計決策(續) — YouTube 上傳

| # | 決策 | 理由 |
|---|---|---|
| D9 | OAuth 認證用 **device flow**(`/device/code` + 輪詢 `/token`),純 httpx | VM 無瀏覽器;使用者用任何裝置開網址輸入代碼即可,不需在 VM 上跑瀏覽器,也不需在 Windows 上裝 SDK |
| D10 | scope 用最小權限 `youtube.upload` | 只上傳,不讀取/修改頻道其他資料 |
| D11 | 上傳用 **resumable upload**(`uploadType=resumable`,session URI + PUT 串流) | Google 官方建議的上傳方式;session URI 可續傳;4 MB 分塊串流避免整檔載入記憶體 |
| D12 | `privacyStatus: private`(可 CLI 覆寫)+ `categoryId: 28`(Science & Technology)+ `selfDeclaredMadeForKids: false` | 依架構圖 "Upload Private";測試期避免公開曝光 |
| D13 | token 存 `output/youtube_token.json`(0600),refresh token 自動續用;refresh 失敗自動清除並要求重授權 | 與 master_token 相同安全原則;避免靜默失效 |
| D14 | 憑證放 `output/client_secret.json`(已 gitignore) | 不進 repo |
| D15 | **多頻道架構**: `config/channels.json` 定義頻道(keyword / title_prefix / 語系),每個頻道獨立 `output/<slug>/` 目錄,所有腳本吃 `--channel` | 支援每日多支影片;產出不互相覆蓋;新增頻道只需改 config |
| D16 | 第二頻道 `tech` 關鍵字用 Google News OR 語法:`technology OR artificial intelligence` | 一支影片涵蓋科技 + AI 新聞 |
| D17 | **每日 Shorts**: `run_shorts_pipeline.py` 用 tech 頻道 TOP 1 製作 60 秒直式影片(`--format short`),cron 加在長片之後 | 每天 3 支(2 長片 + 1 Shorts);Shorts 為 Pro/Ultra 限定、英文、生成可能 30+ 分鐘(等待預算 3600s) |

**前置(一次性,使用者操作)**: Google Cloud 專案 → 啟用 YouTube Data API v3 →
OAuth 同意畫面(External,加入測試使用者)→ 建立 OAuth 用戶端 ID(桌面應用程式)
→ 下載 JSON 存成 `output/client_secret.json`。

## 里程碑

- [x] fetch_news.py — Google News RSS 抓取 + XML 解析
- [x] rank_news.py — Gemini 排名 + TOP 1
- [x] collect_sources.py — 官方來源收集 + 可達性驗證
- [x] run_daily.py — pipeline 串接
- [x] run_video_pipeline.py — 端到端(新聞 → NotebookLM 影片)實測通過
- [x] youtube_auth.py + youtube_upload.py — device flow + resumable 上傳
- [x] 首次授權實測(device flow + 測試使用者 + channel 建立)
- [x] 上傳實測(tech: 2oN6Z-4Oi2U;embedded: kFttyvxMZFw,兩支皆已回填記錄)
- [x] 多頻道: embedded + tech(AI),`config/channels.json` + `--channel`
- [x] cron 每日 06:00(TZ=Australia/Sydney)+ `auth refresh` + 逐頻道 fail-fast
- [x] 冪等強化: notebook 重用(pipeline_state.json)、來源去重、影片存在跳過、上傳防重複(youtube_uploads.json)
- [x] 真實文章 URL 解析(playwright 跟隨 Google News 轉址)
- [x] 單元測試 16 個(pytest,不連網)+ README 更新

## 已知修正紀錄

- `flag_value()` helper: 原本 `--channel` 等旗標解析回傳旗標本身而非下一個值,6 支腳本皆受影響,已統一改用 helper(fetch/rank/collect/run_daily/run_video_pipeline/youtube_upload)
- resumable upload 需 `part=snippet,status` query;streaming 回應需先 `read()` 再 `json()`
