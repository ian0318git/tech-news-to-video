# CLAUDE.md — tech-news-to-video

自動化「科技新聞 → AI 排名 → NotebookLM 影片 → YouTube 上傳」的每日 pipeline(作品集專案)。
本 repo = notebooklm-py 函式庫 fork + `pipeline/`(本專案主體)。

## 專案佈局

- `pipeline/scripts/` — 每日流程:`fetch_news` → `rank_news`(Gemini 排名 + 7 天話題去重)→ `collect_sources` → `run_video_pipeline` / `run_shorts_pipeline`(NotebookLM 生成,旁白用 `SIMPLE_EN_STYLE` A2 基礎英文)→ `brand_video` + `youtube_upload`(品牌片頭/片尾拼接、公開上傳)
- `pipeline/config/channels.json` — 頻道設定(embedded / tech,含 style_prompt)
- `pipeline/docs/` — design-decisions.md、master-token-auth.md
- `src/`、根目錄 `docs/` — 上游 notebooklm-py 函式庫(勿改,上游指南保留在 git 歷史與 `docs/`)

## 每日排程(VM cron,墨爾本時間 08:00)

- `run_daily_cron.sh`:3 支/天(2 長片 + 1 Short),flock 防並發;全成功才寫 done marker
- catch-up `*/15 8-14`:VM 休眠錯過後自動補跑
- 手動執行:`cd pipeline/scripts && python run_daily.py --channel tech`

## 同步與提交(雙副本 by-design)

- 營運目錄(VM 實際執行、非 git):`<VM 上 clone 本 repo 的目錄>`(例如 `~/tech-news-to-video/`)
- 改完營運目錄 → `cp` 到 `pipeline/` → commit → push 到 `ian`(ian0318git/tech-news-to-video)
- 上游 `origin`(teng-lin/notebooklm-py)只收函式庫 issue 回報,不直接 push

## 測試

- `pytest`(45 tests:facade / CLI contract / orchestrator / rank_news 去重)
- `ruff check .`

## 安全(不可違反)

- `.env`、master_token.json、任何 key 一律 gitignore + chmod 600,永不 push

## 已知坑(詳見 pipeline/docs/design-decisions.md)

- NotebookLM 並發生成會失敗 → 全程 flock
- 品牌拼接須保留音訊;Gemini 結束卡靠尾靜音偵測裁切
- Gemini 429 常見 → gemini_json 內建重試
- `.env` 內路徑必須絕對(cron cwd 下相對路徑失效)
