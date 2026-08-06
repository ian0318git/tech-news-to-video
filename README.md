# 🎬 Tech News to Video

> **Daily automated video pipeline: news → AI → NotebookLM → YouTube.**
> Powered by [**notebooklm-py**](https://github.com/teng-lin/notebooklm-py) — the unofficial Google NotebookLM Python API by [Teng Lin](https://github.com/teng-lin).

**[English](#english) | [繁體中文](#zh-tw)**

---

## English <a id="english"></a>

### Why I built this

I'm an **embedded software engineer**. This project started with two goals:

1. **Staying sharp on technology.** Building it forces me to read what matters in tech every day — embedded Linux, semiconductors, AI hardware, and beyond. The pipeline is a daily habit that keeps my technical awareness high, and the ranking step makes me think about *why* a story matters, not just that it happened.
2. **Sharing information in a different way.** Instead of forwarding links and articles, the system turns the day's most important tech story into a **short narrated video** — an easy-to-digest summary that anyone can watch. It's a more engaging way to share technical news with colleagues and the community.

This project is also part of my portfolio: it demonstrates building a **fully automated, headless, self-healing pipeline** — OAuth device-flow authentication, third-party API integration, scheduled execution, idempotent retries, and failure logging, all on a command-line-only server.

### What it does

Every day at **07:00 (Asia/Taipei)**, completely unattended:

1. **Fetch** — pulls the latest news from **Google News RSS** for two channels: `embedded linux` and `technology OR artificial intelligence`.
2. **Rank** — **Gemini** scores each story (relevance, recency, technical depth, source authority) and picks the **TOP 1** with a written rationale.
3. **Collect** — gathers authoritative sources for the story (official docs, GitHub, vendor pages) and verifies each URL is reachable.
4. **Generate** — [notebooklm-py](https://github.com/teng-lin/notebooklm-py) creates a NotebookLM notebook, adds the sources, and generates a **Video Overview** (Explainer format).
5. **Upload** — downloads the MP4 and uploads it to my YouTube channel via the **YouTube Data API v3** (privacy configurable via `UPLOAD_PRIVACY`, currently `public`).
6. **Shorts** — a 60-second vertical Short is also generated daily from the Tech & AI channel's top story.

### Architecture

```
News → AI → NotebookLM → Video → YouTube
                         DAILY 07:00
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
                    │ Download MP4    │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  YouTube API    │
                    │ Upload Private  │
                    └────────┬────────┘
                             │
                             ▼
                    Your YouTube Channel
```

### Tech stack & credits

| Component | Tool |
|---|---|
| News feed | [Google News RSS](https://news.google.com/rss) (no API key required) |
| News ranking & source collection | [Gemini API](https://ai.google.dev/) (`gemini-2.5-flash`) |
| NotebookLM automation | **[notebooklm-py](https://github.com/teng-lin/notebooklm-py)** by Teng Lin — async Python client for Google NotebookLM |
| Video upload | YouTube Data API v3 (resumable upload, `private`) |
| Headless browser | Playwright (device-flow auth, redirect resolution) |
| Scheduling | cron |

**Special thanks to [Teng Lin](https://github.com/teng-lin)** — this project is built on [notebooklm-py](https://github.com/teng-lin/notebooklm-py) (MIT licensed), an unofficial Python client that drives Google NotebookLM programmatically. Note: NotebookLM was rebranded to **Gemini Notebook** in July 2026; the library works unchanged. See the [original README](https://github.com/teng-lin/notebooklm-py) for details.

### Key features

- **Fully headless** — runs on a command-line-only Ubuntu VM; no desktop or browser needed (master-token auth + OAuth device flow)
- **Multi-channel** — channels are config-driven (`config/channels.json`); adding one is a one-line change
- **Idempotent** — safe to re-run at any point: no duplicate notebooks, sources, videos, or uploads
- **Fail-fast with logs** — each step stops with a clear diagnosis; per-step logs make failures easy to trace
- **Privacy configurable** — upload as `private` by default; set `UPLOAD_PRIVACY=public` in `.env` to publish automatically
- **Tested** — 16 unit tests covering RSS parsing, argument handling, and source filtering

### Repository layout

```
├── src/            # notebooklm-py (vendored dependency, by Teng Lin)
└── pipeline/       # this project's pipeline (self-contained)
    ├── scripts/    # fetch_news, rank_news, collect_sources, run_daily,
    │               #   run_video_pipeline, youtube_auth / youtube_upload
    ├── config/     # channels.json (channel definitions)
    ├── tests/      # unit tests (pytest)
    └── .env.example  # environment template (GEMINI_API_KEY)
```

### Quick start

```bash
git clone https://github.com/ian0318git/tech-news-to-video.git
cd tech-news-to-video
python3 -m venv .venv && .venv/bin/pip install ".[headless,browser]"
cp pipeline/.env.example .env   # fill in GEMINI_API_KEY

# news → top story → sources
.venv/bin/python pipeline/scripts/run_daily.py --channel tech

# notebook → video → MP4 (idempotent, safe to re-run)
.venv/bin/python pipeline/scripts/run_video_pipeline.py --channel tech

# upload to YouTube (private)
.venv/bin/python pipeline/scripts/youtube_upload.py --channel tech
```

> Operations docs: [master-token auth](pipeline/docs/master-token-auth.md) · [design decisions](pipeline/docs/design-decisions.md)

### License

- [notebooklm-py](https://github.com/teng-lin/notebooklm-py): **MIT** © 2026 Teng Lin
- Pipeline scripts in `scripts/` / `config/`: © 2026 Ian (ian0318git)

---

## 繁體中文 <a id="zh-tw"></a>

### 為什麼做這個專案

我是一名**嵌入式軟體工程師**。這個專案有兩個初衷:

1. **保持對科技的敏感度。** 建置這個系統讓我每天都要讀科技新聞 — 嵌入式 Linux、半導體、AI 硬體乃至整個科技圈。pipeline 是每天的習慣,而「排名選題」這一步逼我思考**為什麼**這則新聞重要,而不只是「發生了什麼」。
2. **用另一種方式分享訊息。** 與其轉貼連結和文章,這個系統把當天最重要的科技新聞變成**一支有旁白的短影片** — 任何人都能輕鬆看完。這是以更吸引人的方式,把技術新聞分享給同事與社群。

這個專案同時也是我的作品集項目:展示如何建置**全自動、無頭(headless)、可自我修復的 pipeline** — OAuth device-flow 認證、第三方 API 整合、排程執行、冪等重試與失敗記錄,全部跑在一台只有命令列的伺服器上。

### 系統運作

每天 **07:00(台北時間)**,完全無人值守:

1. **抓取** — 從 **Google News RSS** 拉取兩個頻道的新聞:`embedded linux` 與 `technology OR artificial intelligence`。
2. **排名** — **Gemini** 依相關性、新鮮度、技術深度、來源權威性評分,選出 **TOP 1** 並附理由。
3. **收集來源** — 為這則新聞收集權威來源(官方文件、GitHub、vendor 官網),並逐一驗證網址可達。
4. **生成影片** — 用 [notebooklm-py](https://github.com/teng-lin/notebooklm-py) 建立 NotebookLM notebook、加入來源、生成 **Video Overview**(Explainer 格式)。
5. **上傳** — 下載 MP4,透過 **YouTube Data API v3** 上傳到我的 YouTube 頻道(隱私由 `UPLOAD_PRIVACY` 控制,目前 `public` 自動公開)。
6. **Shorts** — 另外從 Tech & AI 頻道的 TOP 1 新聞,每天製作一支 60 秒直式 Short。

### 技術棧與致謝

| 元件 | 工具 |
|---|---|
| 新聞來源 | [Google News RSS](https://news.google.com/rss)(不需 API key) |
| 新聞排名與來源收集 | [Gemini API](https://ai.google.dev/)(`gemini-2.5-flash`) |
| NotebookLM 自動化 | **[notebooklm-py](https://github.com/teng-lin/notebooklm-py)** by Teng Lin — NotebookLM 的非官方 Python client |
| 影片上傳 | YouTube Data API v3(resumable upload,`private`) |
| 無頭瀏覽器 | Playwright(device-flow 認證、轉址解析) |
| 排程 | cron |

**特別感謝 [Teng Lin](https://github.com/teng-lin)** — 本專案建立在 [notebooklm-py](https://github.com/teng-lin/notebooklm-py)(MIT 授權)之上,這是以程式方式驅動 Google NotebookLM 的非官方 Python client。附註:NotebookLM 已於 2026 年 7 月更名為 **Gemini Notebook**,函式庫照常運作。細節見[原作者 README](https://github.com/teng-lin/notebooklm-py)。

### 主要特色

- **完全 headless** — 跑在純命令列 Ubuntu VM 上,不需要桌面或瀏覽器(master-token 認證 + OAuth device flow)
- **多頻道** — 頻道以設定檔驅動(`config/channels.json`),新增頻道只要改一行
- **冪等** — 任何階段重跑都安全:不會重複建 notebook、重複加來源、重複生成影片或重複上傳
- **Fail-fast + 完整 log** — 每步失敗即停並附診斷,每步 log 獨立,方便追查
- **上傳隱私可設定** — 預設 `private`;在 `.env` 設 `UPLOAD_PRIVACY=public` 即可自動公開上傳
- **16 個單元測試** — RSS 解析、參數處理、來源過濾

### 快速開始

```bash
git clone https://github.com/ian0318git/tech-news-to-video.git
cd tech-news-to-video
python3 -m venv .venv && .venv/bin/pip install ".[headless,browser]"
cp pipeline/.env.example .env   # 填入 GEMINI_API_KEY

# 新聞 → TOP 1 → 來源
.venv/bin/python pipeline/scripts/run_daily.py --channel tech

# notebook → 影片 → MP4(冪等,可安全重跑)
.venv/bin/python pipeline/scripts/run_video_pipeline.py --channel tech

# 上傳 YouTube(private)
.venv/bin/python pipeline/scripts/youtube_upload.py --channel tech
```

> 營運文件:[master-token 認證](pipeline/docs/master-token-auth.md) · [設計決策](pipeline/docs/design-decisions.md)

### 授權

- [notebooklm-py](https://github.com/teng-lin/notebooklm-py):**MIT** © 2026 Teng Lin
- 本專案 pipeline 腳本(`scripts/`、`config/`):© 2026 Ian(ian0318git)
