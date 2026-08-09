# 🎬 Tech News to Video

> **A fully automated, headless pipeline: news → AI → NotebookLM → YouTube.** Three videos published daily, zero human intervention.
> Powered by [**notebooklm-py**](https://github.com/teng-lin/notebooklm-py) — the unofficial Google NotebookLM Python API by [Teng Lin](https://github.com/teng-lin).

![Python](https://img.shields.io/badge/Python-3.12-blue)
![Status](https://img.shields.io/badge/status-live%20%E2%80%94%203%20videos%2Fday-green)
![Tests](https://img.shields.io/badge/tests-26%20unit%20tests-passing-green)

**[English](#english) | [繁體中文](#zh-tw)**

---

## English <a id="english"></a>

### Overview

An unattended daily system that turns tech news into published YouTube videos. Every day at **06:00 (Australia/Sydney)**, it fetches the day's news, uses Gemini to pick the most important story, gathers authoritative sources, generates a NotebookLM video (long-form + 60-second Short), and uploads both — **publicly, automatically, on a command-line-only VM with no desktop and no browser**.

Built by an **embedded software engineer** as both a working product and a portfolio piece: it demonstrates headless authentication, third-party API integration, scheduled execution, idempotent retry design, and disciplined failure handling.

### Highlights

| Metric | Value |
|---|---|
| Daily output | **3 videos** (2 long-form + 1 Short) |
| Human intervention | **0** — scheduled, self-monitoring |
| Runtime environment | Headless Ubuntu VM (no GUI, no browser) |
| Test coverage | **26 unit tests** (contract-tested CLI seam) |
| Idempotency | Safe to re-run at any point — no duplicates ever |
| Uploads | Private by default; this deployment publishes automatically |

### Why I built this

1. **Staying sharp on technology.** As an embedded engineer, the pipeline forces me to read what matters every day — embedded Linux, semiconductors, AI hardware — and to think about *why* a story matters, not just that it happened.
2. **Sharing information in a different way.** Instead of forwarding links, the system turns the day's most important story into a short narrated video anyone can watch.

### What it does

1. **Fetch** — pulls the latest news from Google News RSS for two channels: `embedded linux` and `technology OR artificial intelligence`.
2. **Rank** — Gemini scores each story (relevance, recency, depth, authority) and picks **TOP 1** with a written rationale.
3. **Collect** — gathers authoritative sources (official docs, GitHub, vendors), verifies reachability, and resolves Google News redirects to the real article URL.
4. **Generate** — notebooklm-py creates a notebook, adds the sources, and generates a **Video Overview** (Explainer) plus a **60-second vertical Short**.
5. **Upload** — publishes both to YouTube via the Data API v3 (resumable upload), privacy controlled by `UPLOAD_PRIVACY`.

### System design

| Decision | How it works | Why it matters |
|---|---|---|
| **Headless-first auth** | NotebookLM: durable *master token* minted once, sessions re-minted per run. YouTube: OAuth **device flow** (no browser needed on the server), refresh-token auto-renewal | Runs on a server with no GUI — the whole product depends on this |
| **Idempotent pipeline** | State files per channel (`pipeline_state.json`, `shorts_state.json`) + upload records (`youtube_uploads.json`); notebooks/sources/videos/uploads are deduplicated | Safe to re-run after any failure — no duplicate videos on the channel |
| **Fail-fast with logs** | Each step stops with a typed diagnosis; every step has its own log file | A cron run at 06:00 must be diagnosable at 06:01 |
| **Resilience** | Gemini calls retry 429/5xx with backoff; YouTube token is preserved on transient errors; CLI timeouts are enforced with process kill (no hung cron) | Unattended operation survives network blips without losing credentials or hanging overnight |
| **Freshness gates** | Pipeline "today" = Sydney local date (matches the schedule); stale news (yesterday's TOP 1) is rejected | Prevents publishing yesterday's story after a failure |
| **Contracted CLI seam** | Typed wrappers (`notebook_create`, `source_list`, `generate_video`…) behind a facade; envelope parsing lives in one place, pinned by contract tests | If the underlying CLI changes shape, tests fail loudly with the exact difference |
| **Secrets hygiene** | `.env` (0600) + gitignored tokens/credentials; API keys never in the repo | The repo is public — this is non-negotiable |

### Architecture

```
News → AI → NotebookLM → Video → YouTube
                         DAILY 06:00 AEST
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
                     │  News Ranking  │
                     └───────┬────────┘
                             │
                         TOP 1 NEWS
                             │
                             ▼
                  ┌──────────────────────┐
                  │   Source Collector   │
                  │  (docs / GitHub /    │
                  │   vendor / media)    │
                  └──────────┬───────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  notebooklm-py  │
                    │ Create Notebook │
                    │ Add Sources     │
                    │ Video Overview  │
                    │ + 60s Short     │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  YouTube API    │
                    │  Auto-publish   │
                    └────────┬────────┘
                             │
                             ▼
                    Your YouTube Channel
```

### Problems solved along the way

1. **Timezone/day-boundary bug** — at 06:00 AEST (20:00 UTC the previous day), UTC-based filenames landed on "yesterday" and the idempotency check silently skipped the day's videos. Fixed by defining pipeline "today" as the Sydney date in one place.
2. **Google News redirects are JS-only** — the feed's article links resolve client-side; NotebookLM can't fetch them. Solved with a headless-browser resolver (Playwright) that follows the redirect and falls back gracefully.
3. **OAuth device flow rejects "Desktop app" clients** — Google returns `invalid_client / Invalid client type`; the client must be created as *TVs and Limited Input devices*. One console setting, hours of debugging.
4. **Resumable upload + httpx chunked encoding** — streaming a generator sends `Transfer-Encoding: chunked` without Content-Length, which the upload endpoint doesn't guarantee to accept. Fixed by sending bytes with an explicit `Content-Length`.

### Tech stack & credits

| Component | Tool |
|---|---|
| News feed | Google News RSS (no API key) |
| Ranking & source collection | [Gemini API](https://ai.google.dev/) (`gemini-2.5-flash`) |
| NotebookLM automation | **[notebooklm-py](https://github.com/teng-lin/notebooklm-py)** by Teng Lin (MIT) |
| Video upload | YouTube Data API v3 (resumable) |
| Headless browser | Playwright |
| Scheduling | cron (`Australia/Sydney`) |
| Quality gates | pytest (26 tests) · ruff |

**Special thanks to [Teng Lin](https://github.com/teng-lin)** — built on [notebooklm-py](https://github.com/teng-lin/notebooklm-py) (MIT), an unofficial Python client for Google NotebookLM. Note: NotebookLM was rebranded to **Gemini Notebook** in July 2026; the library works unchanged. See the [original README](https://github.com/teng-lin/notebooklm-py).

### What we built (from scratch)

Everything above the vendored library is original work — the library itself was used as-is, untouched.

| # | Feature | Files |
|---|---|---|
| 1 | News fetching — Google News RSS (configurable keywords & languages) | `fetch_news.py` |
| 2 | AI story selection — Gemini ranks 20 stories, picks TOP 1 with rationale | `rank_news.py` |
| 3 | Source collector — authoritative sources + reachability checks + redirect resolution | `collect_sources.py` |
| 4 | End-to-end video — notebook → sources → Video Overview → MP4 | `run_video_pipeline.py` |
| 5 | Daily Shorts — 60-second vertical video | `run_shorts_pipeline.py` |
| 6 | YouTube upload — device-flow auth, resumable upload, dedup, privacy config | `youtube_auth.py` · `youtube_upload.py` |
| 7 | Multi-channel architecture — one-line config change adds a channel | `config/channels.json` |
| 8 | Idempotent pipeline — re-runs never duplicate anything | `_cli.py` (state files) |
| 9 | Daily 06:00 automation — cron + auth refresh + per-channel fail-fast | `run_daily_cron.sh` |
| 10 | 26 unit tests, contract-tested CLI seam, bilingual docs | `tests/` · `docs/` |

### Repository layout

```
├── src/            # notebooklm-py (vendored dependency, by Teng Lin)
└── pipeline/       # this project's pipeline (self-contained)
    ├── scripts/    # _base / _config / _cli / _gemini (deep modules) + facade
    │               #   + step scripts + cron entry
    ├── config/     # channels.json (channel definitions)
    ├── tests/      # 26 unit tests (pytest, contract-tested)
    └── docs/       # auth guide · design decisions
```

### Quick start

```bash
git clone https://github.com/ian0318git/tech-news-to-video.git
cd tech-news-to-video
python3 -m venv .venv && .venv/bin/pip install ".[headless,browser]"
cp pipeline/.env.example pipeline/.env   # fill in GEMINI_API_KEY (load_env reads pipeline/.env)

# news → top story → sources
.venv/bin/python pipeline/scripts/run_daily.py --channel tech

# notebook → video → MP4 (idempotent, safe to re-run)
.venv/bin/python pipeline/scripts/run_video_pipeline.py --channel tech

# upload to YouTube (default private; UPLOAD_PRIVACY=public to auto-publish)
.venv/bin/python pipeline/scripts/youtube_upload.py --channel tech
```

> Operations docs: [master-token auth](pipeline/docs/master-token-auth.md) · [design decisions](pipeline/docs/design-decisions.md)

### Future work

- Notification of daily results (email/Telegram)
- Gemini **Deep Research** mode for the source-collection stage
- More channels (Pro tier allows 20 videos/day; we use 3)

### Contact

📧 Portfolio & LinkedIn: [linkedin.com/in/ian-chang-56136479](https://www.linkedin.com/in/ian-chang-56136479) — happy to discuss the design.

### License

- [notebooklm-py](https://github.com/teng-lin/notebooklm-py): **MIT** © 2026 Teng Lin
- Pipeline (`pipeline/`): © 2026 Ian (ian0318git)

---

## 繁體中文 <a id="zh-tw"></a>

### 專案簡介

一個**全自動、無人值守**的每日系統:把科技新聞變成已發佈的 YouTube 影片。每天 **06:00(雪梨時間)**,系統抓取當天新聞 → Gemini 選出最重要的故事 → 收集權威來源 → 用 NotebookLM 生成影片(長片 + 60 秒 Shorts)→ **自動公開上傳** — 全程跑在一台**只有命令列、沒有桌面也沒有瀏覽器**的 VM 上。

作者是**嵌入式軟體工程師**,這個專案同時是實際運作的產品與作品集:展示了無頭(headless)認證、第三方 API 整合、排程執行、冪等重試設計與嚴謹的失敗處理。

### 量化成果

| 指標 | 數值 |
|---|---|
| 每日產出 | **3 支影片**(2 長片 + 1 Shorts) |
| 人為介入 | **0** — 排程執行、自動監控 |
| 執行環境 | 無頭 Ubuntu VM(無 GUI、無瀏覽器) |
| 測試覆蓋 | **26 個單元測試**(CLI 契約測試) |
| 冪等性 | 任何時點重跑都安全 — 永不重複 |
| 上傳隱私 | 預設 private;目前部署為自動公開 |

### 為什麼做這個專案

1. **保持對科技的敏感度** — 身為嵌入式工程師,pipeline 逼我每天讀重要的科技新聞(嵌入式 Linux、半導體、AI 硬體),並思考「為什麼這則新聞重要」,而不只是「發生了什麼」。
2. **用另一種方式分享訊息** — 與其轉貼連結,系統把當天最重要的故事變成任何人能輕鬆看完的短影片。

### 系統運作

1. **抓取** — Google News RSS(兩個頻道:`embedded linux` 與 `technology OR artificial intelligence`)。
2. **排名** — Gemini 依相關性/新鮮度/深度/權威性評分,選出 **TOP 1** 並附理由。
3. **收集來源** — 收集官方文件/GitHub/vendor 來源、驗證可達性、把 Google News 轉址解析成真實文章網址。
4. **生成影片** — notebooklm-py 建 notebook、加來源、生成 **Video Overview(Explainer)** + **60 秒直式 Short**。
5. **上傳** — 透過 YouTube Data API v3(resumable)上傳,隱私由 `UPLOAD_PRIVACY` 控制。

### 系統設計

| 決策 | 做法 | 為什麼重要 |
|---|---|---|
| **Headless 認證** | NotebookLM 用持久 master token、每次執行重 mint;YouTube 用 OAuth **device flow**(伺服器不需瀏覽器)+ 自動 refresh | 整台伺服器沒有 GUI — 這是整個產品成立的前提 |
| **冪等 pipeline** | 每頻道 state 檔 + 上傳記錄;notebook/來源/影片/上傳全部去重 | 任何失敗後重跑都安全 — 頻道上不會出現重複影片 |
| **Fail-fast + 完整 log** | 每步失敗即停並附診斷;每步獨立 log | 06:00 跑的 job,06:01 就要能查出問題 |
| **韌性** | Gemini 429/5xx 自動重試;YouTube token 在暫時性錯誤時保留;CLI 逾時會 kill 程序(不會掛整夜) | 無人值守必須經得起網路抖動,且不能掉憑證 |
| **新鮮度閘門** | pipeline 的「今天」= 雪梨當地日期(與排程一致);過期新聞直接拒絕 | 防止失敗後把昨天的新聞發出去 |
| **CLI 契約層** | typed wrappers(facade 之後)+ envelope 解析集中一處,由契約測試鎖定 | 底層 CLI 改形狀時,測試立刻紅並指出差異 |
| **機密衛生** | `.env`(0600)+ gitignored token/憑證;API key 永不進 repo | repo 是公開的 — 沒有妥協空間 |

### 架構

```
News → AI → NotebookLM → Video → YouTube
                         DAILY 06:00 AEST
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
                     │  News Ranking  │
                     └───────┬────────┘
                             │
                         TOP 1 NEWS
                             │
                             ▼
                  ┌──────────────────────┐
                  │   Source Collector   │
                  │  (docs / GitHub /    │
                  │   vendor / media)    │
                  └──────────┬───────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  notebooklm-py  │
                    │ Create Notebook │
                    │ Add Sources     │
                    │ Video Overview  │
                    │ + 60s Short     │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  YouTube API    │
                    │  Auto-publish   │
                    └────────┬────────┘
                             │
                             ▼
                    Your YouTube Channel
```

### 踩過的坑與解法

1. **時區/日期邊界 bug** — 06:00 AEST = 前一日 20:00 UTC,用 UTC 日期命名會落在「昨天」,被冪等誤判跳過。修法:pipeline 的「今天」統一以雪梨日期為準。
2. **Google News 轉址只有 JS 才能解析** — 文章連結要靠瀏覽器重導;用 Playwright 無頭瀏覽器解析,失敗則優雅略過。
3. **OAuth device flow 拒絕「桌面應用程式」client** — 回 `invalid_client / Invalid client type`;client 必須建為 *TVs and Limited Input devices*。Console 一個設定,debug 好幾小時。
4. **Resumable 上傳 + httpx chunked 編碼** — generator 串流會送 `Transfer-Encoding: chunked` 且無 Content-Length,上傳端不保證接受。修法:直接送 bytes + 明確 `Content-Length`。

### 技術棧與致謝

| 元件 | 工具 |
|---|---|
| 新聞來源 | Google News RSS(不需 API key) |
| 排名與來源收集 | [Gemini API](https://ai.google.dev/)(`gemini-2.5-flash`) |
| NotebookLM 自動化 | **[notebooklm-py](https://github.com/teng-lin/notebooklm-py)** by Teng Lin(MIT) |
| 影片上傳 | YouTube Data API v3(resumable) |
| 無頭瀏覽器 | Playwright |
| 排程 | cron(`Australia/Sydney`) |
| 品質門檻 | pytest(26 tests)· ruff |

**特別感謝 [Teng Lin](https://github.com/teng-lin)** — 本專案建立在 [notebooklm-py](https://github.com/teng-lin/notebooklm-py)(MIT 授權)之上。附註:NotebookLM 已於 2026 年 7 月更名為 **Gemini Notebook**,函式庫照常運作。細節見[原作者 README](https://github.com/teng-lin/notebooklm-py)。

### 我們從無到有新增的功能

架構圖中所有在函式庫**之上**的東西都是我們原創的 — 函式庫本身一行未改。

| # | 功能 | 對應檔案 |
|---|---|---|
| 1 | 新聞抓取 — Google News RSS(關鍵字與語系可設) | `fetch_news.py` |
| 2 | AI 選題 — Gemini 排名 20 則新聞選 TOP 1(附理由) | `rank_news.py` |
| 3 | Source Collector — 官方來源 + 可達性檢查 + 轉址解析 | `collect_sources.py` |
| 4 | 端到端影片 — notebook → 來源 → Video Overview → MP4 | `run_video_pipeline.py` |
| 5 | 每日 Shorts — 60 秒直式影片 | `run_shorts_pipeline.py` |
| 6 | YouTube 上傳 — device flow 認證、resumable 上傳、防重複、隱私可設 | `youtube_auth.py` · `youtube_upload.py` |
| 7 | 多頻道架構 — 改 config 一行就新增頻道 | `config/channels.json` |
| 8 | 冪等 pipeline — 重跑永不重複 | `_cli.py`(state 檔) |
| 9 | 每日 06:00 自動化 — cron + auth refresh + 逐頻道 fail-fast | `run_daily_cron.sh` |
| 10 | 26 個單元測試、契約測試、雙語文件 | `tests/` · `docs/` |

### Repository 佈局

```
├── src/            # notebooklm-py(原作者的 vendored 依賴)
└── pipeline/       # 本專案的 pipeline(自包含)
    ├── scripts/    # _base / _config / _cli / _gemini(deep 模組)+ facade + 步驟腳本 + cron
    ├── config/     # channels.json(頻道定義)
    ├── tests/      # 26 個單元測試(pytest,契約測試)
    └── docs/       # 認證指南 · 設計決策
```

### 快速開始

```bash
git clone https://github.com/ian0318git/tech-news-to-video.git
cd tech-news-to-video
python3 -m venv .venv && .venv/bin/pip install ".[headless,browser]"
cp pipeline/.env.example pipeline/.env   # 填入 GEMINI_API_KEY(load_env 讀 pipeline/.env)

# 新聞 → TOP 1 → 來源
.venv/bin/python pipeline/scripts/run_daily.py --channel tech

# notebook → 影片 → MP4(冪等,可安全重跑)
.venv/bin/python pipeline/scripts/run_video_pipeline.py --channel tech

# 上傳 YouTube(預設 private;UPLOAD_PRIVACY=public 自動公開)
.venv/bin/python pipeline/scripts/youtube_upload.py --channel tech
```

> 營運文件:[master-token 認證](pipeline/docs/master-token-auth.md) · [設計決策](pipeline/docs/design-decisions.md)

### 未來規劃

- 每日結果通知(email/Telegram)
- Gemini **Deep Research** 模式強化來源收集
- 更多頻道(Pro 方案 20 支/天,目前用 3)

### 聯絡

📧 作品集與 LinkedIn:[linkedin.com/in/ian-chang-56136479](https://www.linkedin.com/in/ian-chang-56136479)— 歡迎討論設計細節。

### 授權

- [notebooklm-py](https://github.com/teng-lin/notebooklm-py):**MIT** © 2026 Teng Lin
- Pipeline(`pipeline/`):© 2026 Ian(ian0318git)
