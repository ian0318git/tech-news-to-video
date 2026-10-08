# Design Decisions — Tech News to Video

> 中文為主;這份文件記錄 pipeline 的設計決策、里程碑與已知修正。
> (Design decisions, milestones, and known fixes for the pipeline. See the README for a bilingual overview.)

## 背景(任務錨點)

POC(notebooklm-py 影片流程)驗證通過後,實作正式系統:
① Google News RSS 抓取 → ② Gemini 排名選 TOP 1 → ③ Source Collector 收集官方來源
→ ④ NotebookLM 生成影片 → ⑤ YouTube 上傳,每日 06:00(AEST) cron 排程。

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
| D12 | `privacyStatus` 可設(預設 private;`.env` 的 `UPLOAD_PRIVACY=public` 自動公開)+ `categoryId: 28` + `selfDeclaredMadeForKids: false` | 部署環境目前為 public 自動公開 |
| D13 | token 存 `output/youtube_token.json`(0600),refresh token 自動續用 | 與 master_token 相同安全原則 |
| D14 | 憑證放 `output/client_secret.json`(已 gitignore);**OAuth client 類型必須是「TVs and Limited Input devices」**(桌面應用程式類型不支援 device flow,會回 `invalid_client / Invalid client type`) | 實測踩過的坑 |
| D15 | **多頻道架構**: `config/channels.json` 定義頻道,每個頻道獨立 `output/<slug>/` 目錄,所有腳本吃 `--channel` | 產出不互相覆蓋;新增頻道只需改 config |
| D16 | `tech` 頻道關鍵字用 OR 語法(2026-08-26 擴充):`technology OR artificial intelligence OR smartphone OR apple OR samsung OR tesla OR spacex OR "electric vehicle"` | 一支影片涵蓋 AI + 消費電子 + 太空 + 電動車新聞(用戶要求納入創新科技範圍);Gemini 排名從候選池挑 TOP 1 |
| D17 | **冪等設計**: notebook 重用(`pipeline_state.json`)、來源 URL 去重、當天影片已存在即整個跳過、上傳記錄(`youtube_uploads.json`)防重複 | cron 無人監督環境,重跑必須安全 |
| D18 | Gemini 呼叫對暫時性錯誤(429/5xx)**自動重試 2 次**(backoff 5s/10s) | 實測遇過 503;cron 環境不能因暫時錯誤整日失敗 |
| D19 | cron 逐頻道 fail-fast:單一頻道失敗記錄 `[FAIL]` 並繼續其他頻道 | 一個頻道故障不拖垮當日全部產出 |
| D20 | **每日 Shorts**: `run_shorts_pipeline.py` 用 tech 頻道 TOP 1 製作 60 秒直式影片(`--format short`),cron 加在長片之後 | 每天 3 支(2 長片 + 1 Shorts);Shorts 為 Pro/Ultra 限定、英文、分階段開放,生成可能 30+ 分鐘(等待預算 3600s) |
| D21 | **自動刪除 NotebookLM 專案**: 每支影片下載成功後立即刪當天該 channel 的 notebook(`_orchestrator.run_video_flow` 尾部 hook)+ `cleanup_notebooks.py` 每日掃描舊專案(state 日期 < 今天 且 `done_<日期>.marker` 存在才刪,支援 `--dry-run`) | 影片下載後 notebook 不再被使用,不刪會永久堆積在帳號介面;安全原則: 失敗不刪、刪除失敗只警告絕不影響主流程;CLI `delete -n <id> -y --json` 冪等;`AUTO_DELETE_NOTEBOOKS` 開關(預設 true) |
| D22 | **唯讀權杖與上傳權杖分離**:`_youtube_read.py` 另存 `output/youtube_read_token.json`(scope `youtube.readonly`),`_youtube.py` 的權杖管理抽成通用 `ensure_token(path, scope, script)` 供兩者共用 | 重新授權生產中的上傳權杖會有失效窗口(2026-08-14 實測 refresh token 失效導致整日 pipeline 癱瘓);唯讀權杖獨立,壞了不影響上傳,也符合最小權限。**device flow 的 scope 限制(實測)**:`youtube.readonly` ✅、`youtube.upload` ✅(文件稱不在允許清單,實測可用)、`yt-analytics.readonly` ❌ `invalid_scope` → Analytics API(留存率/流量來源/CTR)無法用 device flow 取得 |
| D23 | **歷史去重記錄可從 YouTube 重建**(`backfill_history.py`):讀唯讀 API 的已上傳影片清單,反推每支影片對應的文章標題/日期,回填 `topic_history.json`;預設乾跑,`--write` 才寫入 | 去重狀態是**每台機器獨立**的檔案,而 `topic_history.json` 只從本機跑過的日子開始累積 — VPS 遷移後本機那份就停在遷移日,且早期資料沒有 `url` 欄位。這正是去重漏洞的溫床:狀態一旦落後,人工補跑 fallback 就會重製既有主題。以 YouTube 實際產出為真相來源重建,與哪台機器跑過無關。安全:預設乾跑、只增不減、`save_json` 原子寫入 |

| D24 | **選題準則:科技巨頭 / 商業衝突 / 突破性硬體,並由同一次 Gemini 呼叫產生爆款標題**(2026-10-08):`RANK_PROMPT_TEMPLATE` 加入明確編輯優先序(①主要科技公司 ②商業與競爭衝突 ③突破性硬體或 AI 產品),醫療臨床與純學術**降權但不剔除**;同時要求 Gemini 為 #1 產出兩條英文爆款標題(四公式擇一:數字與天價 / 對立與衝突 / 反直覺未來感 / 強烈懸念),寫入 `top1.json` 的 `video_title`(長片,≤60 字元)與 `shorts_title`(Shorts,≤50 字元) | **不另開 Gemini 呼叫是關鍵** — 免費層每日僅 20 次配額,多一次就是少一天的產能。兩者合併進既有呼叫 = 零額外成本。標題串接集中在 `_config.build_title()`(淨化 `\n`/`<`/`>`、`TITLE_MAX=95` 截斷、爆款標題從缺時退回新聞標題並警告);消費端是 `youtube_upload.build_metadata()`,Shorts 依檔名前綴取用專屬欄位。選題準則的取捨:頻道受眾是科技新聞,醫療/學術選題點閱與定位都不合。**已知未解**:`relevance to the topic` 被寫成 tie-breaker、embedded 頻道沒有對應的 PRIORITISE 類別 — 觀察數日再決定是否加 per-channel 準則 |
| D25 | **Gemini 配額保護三件套**(2026-10-08,接 D24 的 20 次/天前提):**A** `_gemini.daily_quota_hit()` 解析 429 body,`quotaId`/`quotaMetric` 含 `PerDay` → 立刻 fail 不重試;**B** `run_daily` 在 `top1.json` 的 `date == 今天` 時跳過 fetch/rank;**C** `sources.json` 的 `topic` 等於今天選題且 `sources` 非空時跳過 collect | 起因:cron 08:00 主 run + `*/15 8-14` 補跑 = 28 次/天,而唯一的冪等閘門是「今天的影片檔存在」— **失敗時該條件永遠不成立**,所以一次短暫故障會在兩個 tick 內燒光當日 20 次額度,之後整天 429(實測 274 筆同型)。**判準只看 `quotaId` 不看 `retryDelay`**:同一種 429 的 `retryDelay` 實測 32s–85477s 都有,拿它判斷會把每分鐘速率限制誤判成每日;而每分鐘版本的 `quotaMetric` 與每日版本逐字元相同,只有 `quotaId` 的 `PerMinute`/`PerDay` 不同(兩種情境各有測試釘住)。**C 的 `topic` 比對不是冗餘**:top1.json 被換題時,舊 sources.json 是別的題目的來源。判斷抽成純函式(`load_top1`/`locked_topic`/`sources_ready`/`skip_reason`)以便離線測試;畸形輸入一律當「沒跑過」重跑而非當「已完成」跳過,未知步驟一律執行。**未採用 D(補跑間隔 15→60 分)**:B/C 讓已完成時每 tick 零成本,15 分鐘密度反而是短暫故障能快速自癒的優點 |
| D26 | **模型世代切換 `gemini-2.5-flash` → `gemini-3.5-flash`**(2026-10-08):`_gemini.gemini_json` 預設值、`rank_news.DEFAULT_MODEL`、`collect_sources.DEFAULT_MODEL` 三處與兩台 `.env` 的 `GEMINI_MODEL` 同步 | **2.5 世代對新建立的 GCP 專案已下線**(404 `"no longer available to new users"`;`2.5-flash-lite`、`2.0-flash` 亦同),而舊專案僅因建立得早而沿用 — 換 key 就會踩到。**`ListModels` 仍列出這些模型,清單不反映生成權限,不能當判準**。3.6 / 3.7 / 3.8 在實測時段全部 503(3.8 甚至直接 429),3.5 能穩定吃下完整 prompt(20 則 / 14KB / 約 24s)。**不用 `gemini-flash-latest` 這類浮動別名** — 模型會在背後被換掉,回應風格與 JSON 契約跟著漂移 |
| D27 | **爆款標題改為「前三名各三條」+ `video_title_short` 備援**(2026-10-08,修正 D24):prompt 要求 Gemini 為**前三名**各寫三條標題(`video_title` 長片 ≤60 / `video_title_short` **同一篇**的較短寫法 ≤40 / `shorts_title` ≤50),**掛在前三名各自的 `ranking` 條目上**;`build_metadata` 取用順序為 `prefix + video_title` ≤95 → 超長改用 `prefix + video_title_short`(整條替換不截斷)→ 都不可用才退回原始新聞標題 | D24 上線首日就暴露兩個問題:①`pick_topic` 沿排名取第一則沒被去重封鎖的,而實測 **20 則候選有 18 則落在 90 天窗口內** → 改選幾乎是常態,而舊設計只為 #1 寫標題且一改選就清空 → 標題等於白做(首日 `top1.json` 兩個標題欄位都是空的);②爆款標題常寫到 60 字元,加 `title_prefix` 就超過 95,硬切砍掉的正是最有力的字尾。**掛 `ranking` 而非另開 `top3` 鍵的理由**:`apply_dedup_choice()` 簽章本來就收 `chosen_entry`(被選中那篇的 ranking 條目)→ 零介面變動就能取到正確那篇的標題。**`video_title_short` 不得從 fallback 路徑取用**:它是同一篇的另一種寫法,而 fallback 是別篇(改選到前三名外) — 混用就是「A 標題 + B 影片」;Shorts 亦不取此欄位(有自己的 `shorts_title`) |
| D28 | **爆款標題改用 CTR 規則集:內容標題去前綴、結果/數字先行、長片 ≤25 字元**(2026-10-08,使用者指示;取代 D24/D27 的**風格與長度**部分):prompt 標題段改為八條規則(①先給結果/衝突/數字,不鋪陳 ②只說發生什麼、**原因留給影片** ③直述句、**不用問號結尾** ④反差 ⑤**禁頻道/系列/固定前綴** ⑥數字放最前且必須真實 ⑦禁無法查證的誇大 ⑧不用第一人稱),長度改為長片 ≤25 字元(重點在前 15)、備援 ≤15、Shorts ≤15;`build_metadata` 改傳**空 head** → 內容標題不再加 `title_prefix`;**`filename_title()` 的降級路徑仍保留前綴** | 舊四公式(A 數字 / B 衝突 / C 反直覺 / D 強烈懸念)是被**取代而非補充** — 規則③直接推翻 D(其範例就是問句)。**前綴只在內容標題移除**:降級路徑(top1.json 不可用 / 跨日)本來就沒有內容可前置,拔掉前綴只剩裸的 `video 2026-10-08.branded`,連哪個頻道都認不出;前綴的用途是「辨識」而不是搶版面。**長度只有實測才收得住**:同一天用真實候選(20 則 / 15KB)實跑三輪 — ①只寫規則:合規 **0/18**(25 字上限回 37–56 字)②加「自己數字數」+ 改寫範例:合規 **18/18**,但**範例把模型教錯了方向** —— 範例示範「刪字」,模型就刪掉主體換長度(`Forcing EU Approval!`、`AI PC War Is On!`、`Linux At Risk!`,`Tesla`/`Microsoft`/`Apple` 全不見)③範例改成「**保留主體與數字,砍動詞/形容詞/鋪陳**」→ `$40B Nvidia Chip Bid`(20)、`EU Bows on Tesla FSD`(20)、`AI PC War on Apple`(18)、`AI Linux Implant Exposed`(24)。**教訓:對 LLM 下長度限制,只說「要短」它會刪掉最該留的字 — 必須同時指定「什麼不准刪」**。**未解 / 待觀察**:ⓐ「25 字」的單位按使用者原文是中文字數,而產出是英文,本輪讀作**英文字元**;若原意是「25 個中文字的資訊量」(≈40 英文字元),只要改 prompt 裡那兩個數字 ⓑ 15 字上限下 `video_title_short` 與 `shorts_title` 約半數情況收斂成同一條(單一事實型故事),目前視為可接受冗餘 ⓒ 今日已上傳的 3 支公開影片仍是舊標題(要改需 `youtube.force-ssl` 重新授權)ⓓ **去前綴的隱形連帶損害**:`backfill_history` 原本靠標題的 `"<prefix> - "` 歸屬節目,而去前綴後標題沒有任何節目線索 —— 兩個節目**共用同一個 YouTube 頻道的上傳清單**(實測 187 支影片:tech 122 / embedded 63 / 其他 2),所以「無法歸屬」等於整個重建工具對新影片失效(2026-09-16 的重複上傳事件就是靠這支工具補救的)。修法是上傳時在說明寫一行 `program: <slug>`(產生端 `build_description`、消費端 `parse_program_marker`,兩者共用 `_config` 的同一組函式),**舊影片沒有標記,所以標題前綴與影片 ID 白名單兩條路徑都保留**;兩者衝突時以標記為準並發 WARN(靜默選一個正是這支工具存在的理由) |

**前置(一次性,使用者操作)**: Google Cloud 專案 → 啟用 YouTube Data API v3 →
OAuth 同意畫面(External,加入測試使用者)→ 建立 OAuth 用戶端 ID(**TVs and Limited Input devices**)
→ 下載 JSON 存成 `output/client_secret.json`。

## 測試

`pipeline/tests/` — **317 個單元測試**(pytest,mock 不連網):
RSS 解析(標題/來源/摘要/上限/壞 XML)、`flag_value` 參數解析、頻道解析、來源過濾
(Google 轉址排除、去重、非 http 排除)、選題去重(`title_key` / `url_key` / `pick_topic`)、
權杖管理(refresh 重試、缺 refresh_token、原子寫入)、`channel_stats` 品牌帳號
fallback(`mine=true` 空 → `forHandle`)、`backfill_history` 標題解析(含 `\xa0\xa0`
分隔的來源名剝離)與乾跑/寫入行為。

2026-10-08 追加:排名 prompt 的護欄(prompt 必須渲染得出來、必須要求每一則都排名、
標題風格規則集與欄位名契約)、`build_title`(爆款標題取用 / 型別與淨化後為空的
退回 / 截斷上限 / YouTube 非法字元)、`apply_dedup_choice`(index 一致保留、改選覆寫、
畸形 index、缺 key)、`entry_index` 與畸形 ranking 跳過、`youtube_upload.build_metadata`
(真的決定 YouTube 標題的地方 — 長片/Shorts 各取專屬欄位、跨日 top1 的日期閘門、
news 欄位畸形)、resumable 續傳決策、Gemini 每日配額判準(`daily_quota_hit`)、
補跑冪等(`load_top1` / `locked_topic` / `sources_ready` / `skip_reason`)。

2026-10-08 追加(D27 前三名各三條):欄位名契約改為檢查**前三條 ranking 範例各自
帶齊三欄位**且 `top1` 那行不得出現標題欄位(兩處都寫會讓 Gemini 兩邊都填,而程式
只讀 ranking)、契約文字與三個長度上限、`apply_dedup_choice` 攜帶被選中那篇的標題
(改選到前三名內 vs 前三名外 vs 自己的 #1 缺欄位,三種路徑的訊息分級)、備援短標題的
四條路徑(超長換掉、備援也塞不下就走截斷、**fallback 路徑不得取用備援**、型別錯誤)。

2026-10-08 追加(D28 標題規則集):八條風格規則逐條釘住(少了任一條,Gemini
就會漂回原本的寫法)、**舊公式 `SUSPENSE` 必須不存在**(它與新規則「不用問號
結尾」互斥,留著模型會挑它)、prompt 裡要明文出現使用者點名的兩個前綴字串、
「自己數字數」與結尾複查指令 (**這兩句是長度從 0/18 合規變成 18/18 合規的
關鍵**,拿掉等於讓長度回到失控)、`build_title` 空 head 不得拼出前導 `" - "`
(三條分支各自驗),`build_metadata` 不加前綴 / `filename_title` **保留**前綴
(兩者互為守門人:把前綴全拔或全留,都會有一條紅)。**複審後再實測兩輪**:reviewer
指出四組改寫範例有三組以公司名開頭、第一組又把數字放在第二個字(違反自己寫的規則
①⑥),改成 `$40B Nvidia Chip Bid`(20)/`AI PCs Challenge Apple`(22)/
`$5.7B Fine For Chip Giant`(25)/`Free Linux Beats Paid`(21)並補一句「主體要留,
但不必佔開頭那個位置」→ 第 4 輪合規仍 **18/18**,然而**人眼看出第 2 輪的病灶半回來**:
embedded 把具體名稱換成泛稱(`U-Boot` CVE → `Bug Exposes Linux Gear`、`Broadcom RedC2`
→ `AI Implant Targets Linux`)。第 5 輪再加一句「具體名稱勝過泛稱:`U-Boot Bug Exposes
Gear` 勝過 `Bug Exposes Linux Gear`;`Linux`/`AI`/`chip`/`tool` 不是名稱」→ 合規仍
**18/18**,U-Boot 那則保住主體,但 Broadcom 那則仍寫泛稱、tech 的 SpaceX 那則反而退成
`40 Billion Chip Bid`(掉了 `$` 與兩個主體名)。**結論:`temperature=0.2` 下長度合規容易
達成(第 3/4/5 輪都是 18/18,但第 4、5 輪各自都動過 prompt,**不是同一設定的重複**,只能
說「改動後重跑仍合規」),實體保留率則逐輪浮動(同一份 prompt 4–6/6)** —— 不可拿單輪
結果宣稱「修好了」,這也是「合規率用程式量、產出用人眼看、且要看多輪」的具體案例。
**第 6 輪**(修掉「說明句把 rule 1 窄化成 number/result 開頭」與舉例不符之後)重跑:
長度仍 **18/18**,實體保留 **3/6**(`Speed Up Kria Dev` 保住 Kria、`$40B Nvidia Chip Bid`
保住數字與主體;`Bug Exposes Linux`(17 字,離上限還有 8 字)、`AI Malware Hits Linux`、
`Self-Drive Forced On EU` 則把 U-Boot / Broadcom / Tesla 換成泛稱)—— 與上方結論一致:
**長度可控、主體保留不可控**,而後者是 `temperature=0.2` 下的取樣變異,不是 prompt 缺陷。

2026-10-08 追加(D28 節目標記):`program_marker` / `parse_program_marker` 的往返
契約、非字串與空白 slug 不得寫出半截 `program: `、**整行比對**(摘要裡**行內**提到
`program:`/`reprogram:` 不算標記 —— 誤判會把影片歸給錯的節目)、**寫不回來的 slug
一律不寫**(產生端與解析端共用同一個 `_PROGRAM_SLUG_RE`,含空白/句點的 slug 會寫出
一行解析成空字串的標記,而且沒有錯誤訊息)+ 一條「用 `config/channels.json` 的每個
實際 slug 做往返」的守門測試,以及 `build_description` 對這種 slug **必須發警告**;
**取最後一個匹配**(行錨擋不住「摘要自己有一整行 `program: embedded`」,取最左會讓
偽造值蓋過管線寫的真值,而新標題沒有前綴可衝突 → 連 WARN 都發不出來;真值永遠是
說明最後一行)。
`build_description` **在超長摘要下仍保得住標記**(截斷只砍可變前段,寫成整串
`[:4900]` 就會把尾端標記裁掉而不報錯)、沒有 slug 時不留半截標記也不誤發警告、
`build_metadata` 兩條路徑(正常 / 沒有 top1.json 的降級)都帶標記;
`backfill_history.parse_video` 的歸屬順序 — **說明標記 → 標題前綴 → 影片 ID**,
無前綴的新標題靠標記歸屬、沒有標記的舊影片仍靠前綴、兩者衝突時以標記為準且必須
留下 WARN。

2026-10-08 追加(D25):`_gemini.daily_quota_hit` 的判準 — 真實的每日配額 payload、
每分鐘速率限制(quotaMetric 相同、只有 quotaId 不同)必須**不**被誤判、非 JSON /
JSON 陣列 / `error` 是字串 / `details` 畸形等一律回空字串(→ 照常重試),以及兩個
行為測試(每日配額 → 只打 1 次 HTTP、不 sleep;每分鐘 → 照舊 3 次、sleep 5/10);
`run_daily` 的冪等判斷 — `load_top1` 對損壞/非物件 JSON 回空 dict、`locked_topic`
只認今天的 date 且容忍畸形的 `news`、`sources_ready` 拒絕空的/壞的來源清單、
`skip_reason` 在換題後必須重收來源、未知步驟一律執行。

執行: `cd pipeline && pytest tests -q`

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
- [x] cron 每日 08:00(TZ=Australia/Sydney)+ `auth refresh` + 逐頻道 fail-fast
- [x] 冪等強化: notebook 重用、來源去重、影片存在跳過、上傳防重複
- [x] 真實文章 URL 解析(playwright 跟隨 Google News 轉址)
- [x] 單元測試 144 個(pytest,不連網)+ 文件
- [x] 唯讀工具: `youtube_read_auth.py`(device flow,scope `youtube.readonly`)+ `channel_stats.py`(頻道數據;品牌帳號需 `forHandle` fallback)+ `backfill_history.py`(從 YouTube 重建去重歷史)+ `deploy.sh`

## 部署佈局(by-design)

VM 營運目錄 `/home/ian/github-project/notebooklm-py/`(非 git)是**實際執行**的副本,
cron 指向這裡;GitHub repo 的 `pipeline/` 是**作品集快照**。兩者需同步 — 每次改腳本
都要 `cp` 到 repo 並 commit(已建立此習慣)。已知影響:雙副本,改動時需記得同步。

**VPS 部署**:`./deploy.sh`(在營運目錄,不進本 repo — 內含主機資訊)。
流程為 比對兩端 md5 → 只列變更計畫 →(`-y` 或確認後)備份即將被覆蓋的遠端檔案
→ `rsync` → 重讀遠端 md5 逐檔驗證 → 遠端 `compileall` → 遠端 `pytest`。
只同步 `scripts/` 與 `tests/`,**刻意不同步** `output/`(各機器狀態:token、
`topic_history.json`、`youtube_uploads.json`)、`logs/`、`.env`;`config/` 只偵測差異
並警告,不自動覆蓋。

這支腳本的存在理由:VPS 沒有 git、沒有自動部署,改動只能手動 scp,很容易漏 —
2026-09-16 就是這樣(選題去重修好了但 VPS 跑舊版,重複上傳照樣發生;`_youtube.py`
的權杖重構也同樣沒部署)。**`--dry-run` 可隨時確認兩端差異。**
## 已知修正紀錄

- `flag_value()` helper: 原本 `--channel` 等旗標解析回傳旗標本身而非下一個值,6 支腳本皆受影響,已統一改用 helper
- resumable upload 需 `part=snippet,status` query;streaming 回應需先 `read()` 再 `json()`
- OAuth client 類型必須是「TVs and Limited Input devices」,桌面應用程式類型被 device flow 拒絕
- Google News RSS 的 description 第一個 href 也是轉址,真實文章網址需瀏覽器跟隨 JS 重導
- pipeline 的「今天」以**雪梨當地日期**為準(`today_str()` + cron 用 `TZ=Australia/Sydney date`)— 若用 UTC 日期,06:00 AEST(前一日 20:00 UTC)產出的檔名會落在「昨天」,重跑時誤判已存在而跳過
- 7 天選題去重失效(2026-08-10 重構後 `pick_topic` 未被 `main()` 呼叫、歷史檔無人寫入)→ embedded 連日選中同一篇 FIT 新聞;已接回去重呼叫 + 回寫 `topic_history.json`(同日 catch-up 重跑不封鎖,維持當天主題穩定)
- 2026-08-13 code-review 批量強化: 零位元影片殘骸不再被當完成品(刪除重跑)、新鮮度閘門移到存在跳過之後、cron 主 run 自行持 flock 與 catch-up 互斥、ffmpeg 加 timeout + temp 檔原子替換、token/secret 權限收緊 0600 + 原子寫入、suggested 非物件元素防呆、全部來源 error 時提早停、run_cli 預設 timeout
- **2026-08-14 flock 雙鎖教訓(修正 08-13 的「cron 主 run 自行持 flock」)**: code-review #4 是假警報 — reviewer 只看腳本、沒看 crontab;crontab 主 run 本來就用 `flock -n pipeline.lock` 包住 run_daily_cron.sh。08-13 誤加 cron 內層鎖後,crontab 外層持鎖 → 內層 flock 失敗 → 主 run 自己 SKIP 自己(08-14 實測 08:00 連兩次 `[SKIP]`,只能靠 08:15 catch-up 才跑)。已回退 cron 內層鎖與 catchup 的直接呼叫,互斥回到原始設計:單一 `pipeline.lock`,crontab(08:00 主 run)與 catchup 外層各持一把 — 主 run 持鎖時 catchup 預檢查失敗跳出;catchup 持鎖執行時 08:00 主 run 被 crontab 層 flock -n 擋下,永遠不會並發
- **2026-09-16 選題去重三個漏洞**: 用 YouTube 唯讀 API 反查 135 支歷史影片,發現 47 組重複故事、17 組跨日重複(同一篇文章重製後再次上傳),占上傳 12.6% — 同一篇 Phoronix 文章被製作了 14 次。三個獨立漏洞:①去重窗口只有 14 天,而文章會在 RSS feed 存活數週,撐過窗口即可重新選中;②`title_key` 的來源名尾綴剝離只認單一 token,多字來源名與其網域寫法因此產生不同鍵(有記錄卻封鎖不住);③全部候選被封鎖時靜默退回第一名。已修:改以文章 URL 為主要去重鍵(標題為輔,兼顧舊資料)、窗口拉長至 90 天、尾綴一律剝除(留下限防過度剝離)、無可用主題時明確失敗並在候選過少時預警。教訓:**「有寫入去重記錄」不等於「去重有效」** — 只有從外部(實際產出)反查才看得出來
- **2026-09-16 唯讀授權 + `channel_stats.py` 品牌帳號坑**: 新增唯讀授權(device flow,scope `youtube.readonly`)作為量測工具,才得以從 YouTube 反查歷史影片、發現上表的去重漏洞。`channels.list(mine=true)` 對**品牌帳號(Brand Account)**回 `items=0` — 授權明明成功卻查不到頻道;改為 `mine` 空時 fallback `forHandle=@handle`(可用 `YOUTUBE_CHANNEL_HANDLE` 覆寫)。同時發現 `_youtube.py` 的權杖重構一直沒部署到 VPS(本機改了、正式環境還是舊版)—— 這正是 `deploy.sh` 存在的理由
- **2026-10-08 換 Gemini key:權限綁「專案」、額度綁「專案 × 模型」,而 2.5 世代對新專案已下線**:新 key 對 2.5 世代一律 404,`ListModels` **仍列出**它們(清單不等於權限)。換 3.x 之後,第一支 key 全數 503 或永不回應(curl / httpx、本機 / VPS、`v1beta` / `v1` / Interactions API 六種組合一致,180s 逾時),**第二支 key 的 3.5 與 3.1-lite 卻正常** → 有一部分是「專案運氣」,不能只看一支 key 就下「Google 掛了」的結論。三個必須記住的觀念:**① 模型權限綁專案**,新專案拿不到 2.5 世代,換 key 沒用,要換的是專案;**② 額度綁「專案 × 模型」各 20 次/天**(由 3.8-flash 的 429 證實:`quotaId=GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue=20`)—— 同專案新增 key 不會增加額度,但**換模型會換到一份新的 20 次**;**③ 配額在太平洋午夜重置 = 墨爾本 18:00**,遠早於隔天 08:00 的 cron,所以每天早上都是滿額;一天正常只需 4 次。**兩個程序上的教訓**:更換 key **前必須先備份舊 key**(當時直接覆寫兩台 `.env`,舊 key 就此失去);驗證順序應是「**先拿新 key 打真實 prompt,確認可用,再寫入 `.env`**」— 第一次反過來做,把可用狀態換成不可用狀態。第二次用真實 prompt(20 則 / 14KB)才發現**玩具 prompt 過得了、真實 prompt 會被 503 擋下 — 測試資料的規模本身就是一個變因**

- **2026-10-08 爆款標題接錯線(C1,reviewer 查出)**: 初版把爆款標題接到 `run_video_pipeline.py` / `run_shorts_pipeline.py` 的 `title` 變數,而那個 title 只流向 `cli.ensure_notebook()` — 是 NotebookLM 的**專案名稱**,影片下載後專案立刻被自動刪除,**成品完全沒套用**,整個功能等於白做。真正決定 YouTube 標題的是 `youtube_upload.build_metadata()`(讀 `top1.json`)。已修正並補回歸測試(`tests/test_youtube_upload.py` 釘住「上傳標題的來源是 build_metadata」),兩支 pipeline 檔改回原樣並加註解標明「這裡的 title 不是 YouTube 標題」。**教訓:接好線之後要一路追到「誰消費這個值、使用者最後看到什麼」才算完成 — 變數存在且測試綠燈 ≠ 功能生效**
- **2026-10-08 同批加固(與 D24 同一輪 review)**: ①`build_title` 把「可用性判斷」移到淨化**之後** — 舊寫法下 `viral="<>"` 會被判定有值、淨化後成空字串、fallback 被跳過,產出尾端懸空的 `"prefix - "` 直接上傳;②`pick_topic` 對 ranking 條目 index 全壞時改報專屬訊息(舊版一律報「全部落在 N 天去重窗口內」,會把排查帶去翻沒問題的 `topic_history.json`);③`youtube_upload` 加上傳端**日期閘門**(`dates_agree`):`top1.json` 的 `date` 與影片檔名日期不符 → 標題與說明改用檔名並警告。這是「A 的標題出現在 B 的影片上」唯一殘存路徑(跨日補傳),正常流程不受影響,任一邊取不到日期時放行以免誤擋手動補傳;④`news.title` 為 `null` 或淨化後成空時仍會產生懸空標題 → fallback 改為「保證非空」(兩條 fallback 路徑共用 `stem_label`,避免同一件事有兩種輸出);⑤畸形 ranking 條目不再丟未捕捉例外(`entry_index`)。測試 144 → **203 passed**
- **2026-10-08 Gemini 免費配額被 429 打爆(A+B+C 已實作,見 D25)**: 症狀「早上跑得動、10:00 之後整天 429」。根因是配額算術:免費層 **20 次/天**;一次完整 run 要 4 次呼叫(2 頻道 × (`rank_news` + `collect_sources`)),`_gemini.py` 對 429/5xx 重試 2 次(每次呼叫最多 3 個 HTTP 請求);cron 08:00 主 run + `*/15 8-14` 補跑 = **28 次/天**,而唯一的冪等閘門是「今天的影片檔存在」,失敗時該條件永不成立 → 一次短暫故障就在約兩個 tick 內燒光當日額度。**教訓:重試機制在有限配額下會把單次失敗放大成整日癱瘓**。**已實作 A+B+C**(不重試每日配額型 429 / 補跑冪等 / `collect_sources` 同樣處理,見 D25);**D(補跑間隔 15→60 分)已評估後不採用** — A+B+C 讓已完成時每個 tick 零成本;**E(付費 key)仍為備案**,目前免費層 20 次/天對「2 頻道 × 2 次呼叫 + 餘裕」足夠
- **2026-10-08 爆款標題「只有 #1 有、且一改選就清空」(D27)**: D24 上線**首日**的 `top1.json` 兩個標題欄位都是空的 — 不是 Gemini 沒回(它回了),而是它排 #1 的那篇被去重擋掉、`pick_topic` 改選了別篇,而 `apply_dedup_choice` 依設計清空標題。去重擋掉的比率是 **18/20**,所以「改選」是常態而非例外 → 只為 #1 寫標題等於白做。**教訓:LLM 回了值 ≠ 這個值會走到使用者眼前 — 中間每一段都可能把它丟掉,而丟掉的方式是靜默的**(清空 + 下游退回,只有 WARN)。同輪加上 `video_title_short` 備援,並以 1 次真實 Gemini 呼叫驗證契約(20 則 / 16.6KB → 恰好 3 條帶標題、欄位齊全、`top1` 不殘留)。測試 246 → **265 passed**
- **2026-10-08 標題長度改了三次才對,而問題出在範例教錯方向(D28)**: 使用者給的 CTR 規則集把長片從 60 字元壓到 25。第一輪照抄規則進 prompt → 18 條標題**全數超標**(25 上限實回 37–56,15 上限實回 30–41),即模型對「MAXIMUM N characters」幾乎不設防。第二輪補上「輸出前自己數字數、超過就刪字再數」與五組「太長 → 改寫」範例 → **18/18 合規**,但檢查產出才發現:範例教的是「刪字」,於是模型刪的是 **Tesla / Microsoft / Apple 這些主體**(`Forcing EU Approval!`、`AI PC War Is On!`)—— 短了,卻變成沒有主詞的標題,**比超長更糟**。第三輪把範例改成保留主體與數字、只砍動詞形容詞 → 才同時得到合規與可用的標題。**教訓:對 LLM 下數值限制時,限制本身只決定「多長」,範例決定「犧牲什麼」;只驗合規率會讓「刪掉重點」的解法全綠過關,必須同時人眼看產出**。這也是本專案一貫的做法:契約用測試釘住,但**遵從度只能靠真實呼叫驗證**(測試只證明 prompt 裡有那句話,不證明模型照做)
- **2026-10-08 拿掉標題前綴的同時,弄瞎了另一支工具(D28)**: 前綴不只是版面裝飾,它同時是 `backfill_history` 判斷「這支影片屬於哪個節目」的**唯一識別鍵** —— 兩個節目共用同一個 YouTube 頻道的上傳清單(`forHandle` 實測 187 支影片混在一起)。拔掉前綴後,那支工具會把所有新影片歸成「無法歸屬」**安靜跳過**,只在幾個月後需要重建去重歷史時才發現「怎麼補 0 筆」。這是 reviewer 在程式碼裡追出來的,不是執行時報的錯。**教訓:要移除的欄位若可能被下游當成識別鍵,先問「誰在讀它」——而且在 YouTube 這種外部系統上,唯一的驗證方式是去把真實資料撈回來看**,不能只看自己的程式(標題格式是上傳時自己寫的,但頻道上混了 187 支歷史影片,只有列出來看才知道)。修法用**新增**訊號(說明裡的 `program:` 標記)而不是恢復舊行為,兩條舊路徑都保留 → 舊影片不會因此歸屬不了
- **2026-10-08 測試會汙染正式 log(已修)**: 步驟腳本在 import 時就呼叫 `setup_logging()`,而它建的 `FileHandler` 在**建構時**就 `open()` 了 `logs/<step>.log` → 光是匯入就寫正式 log。`deploy.sh` 每次都在 VPS 跑遠端 pytest,於是測試 WARN 落進正式 log;實測 `logs/youtube_upload.log` 出現「top1.json 是 2026-10-08 的選題,影片檔名日期是 2026-10-07 — 兩者不同天」,**看起來就像真的跨日補傳事件**(同日 01:05 的部署也留了 6 筆)。修在 `tests/conftest.py`(唯一保證先於任何 `test_*` 模組執行的位置;fixture 來不及,檔案早開了):匯入 `_base` 後把 `LOGS_DIR` 指向 `mkdtemp()` 並 `atexit` 清除,手動 `pytest` 也涵蓋。**驗證**:`touch` 蓋章 → 跑 pytest → `find logs -name '*.log' -newer <stamp>` 修好前有、修好後 0 筆。既有的 17 筆汙染行不自行改寫(log 是事實紀錄)
