# CLAUDE.md — tech-news-to-video

自動化「科技新聞 → AI 排名 → NotebookLM 影片 → YouTube 上傳」的每日 pipeline(作品集專案)。
本 repo = notebooklm-py 函式庫 fork + `pipeline/`(本專案主體)。

## 專案佈局

- `pipeline/scripts/` — 每日流程:`fetch_news` → `rank_news`(Gemini 排名 + 90 天話題去重 + **前三名各三條**爆款標題,風格規則見 D28)→ `collect_sources` → `run_video_pipeline` / `run_shorts_pipeline`(NotebookLM 生成,旁白用 `SIMPLE_EN_STYLE` A2 基礎英文)→ `brand_video` + `youtube_upload`(品牌片頭/片尾拼接、公開上傳;**YouTube 標題在此由 `build_metadata` 從 `top1.json` 的爆款標題產生、不加頻道前綴**,影片階段的 title 只是 NotebookLM 專案名稱)
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

- `pytest`(306 tests:facade / CLI contract / orchestrator / rank_news 去重 / 標題規則與組合(含備援短標題、去前綴)/ 上傳 metadata 與說明的節目標記 / backfill_history 的影片歸屬 / Gemini 配額判準 / 補跑冪等)
- `ruff check .`

## 安全(不可違反)

- `.env`、master_token.json、任何 key 一律 gitignore + chmod 600,永不 push

## 已知坑(詳見 pipeline/docs/design-decisions.md)

- NotebookLM 並發生成會失敗 → 全程 flock
- 品牌拼接須保留音訊;Gemini 結束卡靠尾靜音偵測裁切
- Gemini 429 常見 → gemini_json 內建重試;**但每日配額耗盡(quotaId 含 `PerDay`)不重試**,直接 fail(免費層 20 次/天,重試只是白等 — 判準見 D25)
- Gemini 免費層配額是**每專案 × 每模型** 20 次/天(同專案換 key 不加額度,換**模型**才換到新的一份),太平洋午夜重置 = 墨爾本 18:00;補跑靠 `top1.json`/`sources.json` 冪等,不靠影片檔
- Gemini 模型世代:2.5 系列對**新** GCP 專案已下線(404),`ListModels` 仍會列出但不代表能用;現用 `gemini-3.5-flash`(3.6/3.7/3.8 實測常 503)。換 key 前先備份舊 key,並用**真實大小的 prompt** 驗證過再寫入 `.env`
- 爆款標題只為**前三名**而寫(prompt 契約),且掛在各自的 `ranking` 條目上 — 去重改選到前三名之外時就沒有標題可用,`build_title` 會退回原始新聞標題並發 WARN。長片超長時改用 `video_title_short`(同一篇的較短寫法),**不可拿它補 fallback 路徑**(那是別篇)
- 標題長度限制(prompt 的 25/15 字元)**模型不會自動遵守**:實測只寫「MAXIMUM N characters」時 18 條全數超標(回 37–56 字)。prompt 因此明講「自己數字數」+ 結尾複查 + 給改寫範例。**改動 prompt 標題段後一定要用真實候選實跑一次**並人眼看產出 —— 只驗合規率會漏掉「為了變短而刪掉主體」這種全綠的壞解(見 D28)
- 內容標題**不加頻道前綴**(`build_metadata` 傳空 head);`filename_title()` 的降級路徑**仍保留前綴** — 那條路徑沒有內容可前置,前綴是唯一認得出頻道的資訊。拔前綴時別把兩者一起拔
- 影片說明**必須帶 `program: <slug>` 標記**(`build_description` 寫、`backfill_history.parse_program_marker` 讀):兩個節目共用同一個 YouTube 頻道的上傳清單(實測 187 支混在一起),標題去前綴後這是「這支影片屬於哪個節目」的唯一線索。說明的截斷只能砍可變前段 —— 寫成整串 `[:4900]` 會把尾端標記一起裁掉,而且**不會報錯**,只會讓幾個月後的歷史重建「補 0 筆」安靜漏掉
- `.env` 內路徑必須絕對(cron cwd 下相對路徑失效)
