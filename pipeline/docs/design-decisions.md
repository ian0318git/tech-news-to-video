# Design Decisions — Tech News to Video

> 中文為主;這份文件記錄 pipeline 的設計決策、里程碑與已知修正。
> (Design decisions, milestones, and known fixes for the pipeline. See the README for a bilingual overview.)

## 背景(任務錨點)

POC(notebooklm-py 影片流程)驗證通過後,實作正式系統:
① Google News RSS 抓取 → ② Gemini 排名選 TOP 1 → ③ Source Collector 收集官方來源
→ ④ NotebookLM 生成影片 → ⑤ YouTube 上傳,每日 07:00 cron 排程。

## 設計決策

| # | 決策 | 理由 |
|---|---|---|
| D1 | 新聞來源用 **Google News RSS**(`news.google.com/rss/search`) | 免費、不需 API key;關鍵字與語系經 `config/channels.json` 調整 |
| D2 | Gemini 用**直接 REST 呼叫**(`generativelanguage.googleapis.com`),不新增 SDK | venv 已有 `httpx`(notebooklm-py 依賴),零新依賴 |
| D3 | 模型預設 `gemini-2.5-flash`,可經 `GEMINI_MODEL` 覆寫 | flash 便宜快;有 Pro 需求再換 |
| D4 | 排名/收集皆要求 **JSON 結構化輸出**(`responseMimeType=application/json`)並驗證 schema | 下游 pipeline 可直接吃,避免解析自由文字 |
| D5 | 每步獨立腳本、可單獨重跑、fail-fast | 失敗停住並留 log,不猜測 |
| D6 | 祕密(API key)放 `.env`(chmod 600),`load_env()` 載入 | 不進 repo、不進 env 歷史 |
| D7 | `collect_sources.py` 對每個建議 URL 做 **HTTP 可達性檢查** | 避免把壞連結餵給 NotebookLM 當來源 |
| D8 | 原文新聞 URL 用 **playwright 跟隨 Google News 轉址**解析成真實文章網址;解析失敗則略過原文來源 | Google News 轉址是 JS 重導,HTTP 層解析不到,且 NotebookLM 抓不到轉址頁;保證至少保留 Gemini 建議來源 |
| D9 | YouTube OAuth 用 **device flow**(`/device/code` + 輪詢 `/token`),純 httpx | VM 無瀏覽器;任何裝置開網址輸入代碼即可 |
| D10 | scope 用最小權限 `youtube.upload` | 只上傳,不讀取/修改頻道其他資料 |
| D11 | 上傳用 **resumable upload**(`uploadType=resumable`,session URI + PUT 串流) | Google 官方建議;4 MB 分塊串流避免整檔載入記憶體 |
| D12 | `privacyStatus: private`(可 CLI 覆寫)+ `categoryId: 28` + `selfDeclaredMadeForKids: false` | 依架構圖 "Upload Private" |
| D13 | token 存 `output/youtube_token.json`(0600),refresh token 自動續用;refresh 失敗自動清除並要求重授權 | 與 master_token 相同安全原則 |
| D14 | 憑證放 `output/client_secret.json`(已 gitignore);**OAuth client 類型必須是「TVs and Limited Input devices」**(桌面應用程式類型不支援 device flow,會回 `invalid_client / Invalid client type`) | 實測踩過的坑 |
| D15 | **多頻道架構**: `config/channels.json` 定義頻道,每個頻道獨立 `output/<slug>/` 目錄,所有腳本吃 `--channel` | 產出不互相覆蓋;新增頻道只需改 config |
| D16 | `tech` 頻道關鍵字用 OR 語法:`technology OR artificial intelligence` | 一支影片涵蓋科技 + AI 新聞 |
| D17 | **冪等設計**: notebook 重用(`pipeline_state.json`)、來源 URL 去重、當天影片已存在即整個跳過、上傳記錄(`youtube_uploads.json`)防重複 | cron 無人監督環境,重跑必須安全 |
| D18 | Gemini 呼叫對暫時性錯誤(429/5xx)**自動重試 2 次**(backoff 5s/10s) | 實測遇過 503;cron 環境不能因暫時錯誤整日失敗 |
| D19 | cron 逐頻道 fail-fast:單一頻道失敗記錄 `[FAIL]` 並繼續其他頻道 | 一個頻道故障不拖垮當日全部產出 |

## 測試

`pipeline/tests/` — **16 個單元測試**(pytest,mock 不連網):
RSS 解析(標題/來源/摘要/上限/壞 XML)、`flag_value` 參數解析、頻道解析、來源過濾
(Google 轉址排除、去重、非 http 排除)。

手動驗證方式: 依序執行 `run_daily.py` 各步,檢查 `logs/*.log` 的 `[PASS]`/`[FAIL]`
與 `output/<slug>/` 的 JSON 內容(`news_raw.json` → `ranking.json`/`top1.json` → `sources.json`)。

## 里程碑

- [x] fetch_news.py — Google News RSS 抓取 + XML 解析
- [x] rank_news.py — Gemini 排名 + TOP 1
- [x] collect_sources.py — 官方來源收集 + 可達性驗證
- [x] run_daily.py — pipeline 串接
- [x] run_video_pipeline.py — 端到端(新聞 → NotebookLM 影片)實測通過
- [x] youtube_auth.py + youtube_upload.py — device flow + resumable 上傳
- [x] 首次授權實測(device flow + 測試使用者 + channel 建立)
- [x] 上傳實測(tech: `2oN6Z-4Oi2U`;embedded: `kFttyvxMZFw`)
- [x] 多頻道: embedded + tech(AI),`config/channels.json` + `--channel`
- [x] cron 每日 07:00(TZ=Asia/Taipei)+ `auth refresh` + 逐頻道 fail-fast
- [x] 冪等強化: notebook 重用、來源去重、影片存在跳過、上傳防重複
- [x] 真實文章 URL 解析(playwright 跟隨 Google News 轉址)
- [x] 單元測試 16 個(pytest,不連網)+ 文件

## 已知修正紀錄

- `flag_value()` helper: 原本 `--channel` 等旗標解析回傳旗標本身而非下一個值,6 支腳本皆受影響,已統一改用 helper
- resumable upload 需 `part=snippet,status` query;streaming 回應需先 `read()` 再 `json()`
- OAuth client 類型必須是「TVs and Limited Input devices」,桌面應用程式類型被 device flow 拒絕
- Google News RSS 的 description 第一個 href 也是轉址,真實文章網址需瀏覽器跟隨 JS 重導
