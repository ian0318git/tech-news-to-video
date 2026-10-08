#!/usr/bin/env python3
"""Step 2 — Gemini 排名選題(per-channel)。

用法: python scripts/rank_news.py [--channel <slug>]
讀取 output/<slug>/news_raw.json,依 Gemini 排名選出 TOP 1,
但跳過 topic_history.json 中 7 天內已選過的主題(去重),
寫出 output/<slug>/ranking.json、top1.json 並回寫 topic_history.json。
需要 .env 的 GEMINI_API_KEY。
"""

import json
import os
import re
import sys
from datetime import date, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit

from _common import (
    SHORTS_TITLE_FIELD,
    VIDEO_TITLE_FIELD,
    VIDEO_TITLE_SHORT_FIELD,
    channel_dir,
    fail,
    flag_value,
    gemini_json,
    load_env,
    resolve_channel,
    save_json,
    setup_logging,
    today_str,
)

logger = setup_logging("rank_news")

# Gemini 回在**前三名 ranking 條目**上的爆款標題欄位。
# 放同一條清單,避免覆寫時漏掉一個欄位而留下前一篇的殘值(那會是最糟的
# 「A 標題 + B 內容」)。
VIRAL_TITLE_FIELDS = (VIDEO_TITLE_FIELD, VIDEO_TITLE_SHORT_FIELD, SHORTS_TITLE_FIELD)

# 2026-10-08: 由 gemini-2.5-flash 換成 3.5-flash。2.5 世代對**新建立的**
# GCP 專案已下線(回應 404 "no longer available to new users"),舊專案雖仍可
# 沿用但無法據此換 key。3.6/3.7/3.8 在多數時段回 503,3.5 實測可穩定處理
# 完整 prompt(20 則 / 14KB / 約 24s)。**配額是「每專案 × 每模型」各 20 次/天**,
# 不是整個專案共用 20 次 — 所以換模型也會換到一份新額度。詳見 DECISIONS.md。
DEFAULT_MODEL = "gemini-3.5-flash"

# 2026-10-07: 加入編輯立場。先前只給中性的四個維度(relevance/recency/depth/
# authority),Gemini 於是把「技術上新鮮」與「產業上重要」視為等價,導致醫療研究
# 與純學術論文長期佔走 TOP 1(使用者反應)。現在明列優先序與降權類別。
#
# 注意:「降權」不等於「排除」— 被降權的項目仍必須留在 ranking 裡(排在後面)。
# pick_topic 是沿著 ranking 依序取第一則沒被去重窗口封鎖的,若 Gemini 把降權項目
# 直接省略,候選池會縮小,最壞情況會 fail 掉整天的選題(寧可當天不產片)。
RANK_PROMPT_TEMPLATE = """You are a news editor for an audience interested in the topic "{topic}".

Rank the following news items using these editorial priorities.

PRIORITISE, roughly in this order:
1. Major technology companies — Apple, Google, Microsoft, Amazon, Meta, NVIDIA,
   OpenAI, Anthropic, Samsung, Tesla, SpaceX, Intel, AMD, Qualcomm, Arm, TSMC
   and comparable industry players.
2. Business and competitive conflict — lawsuits, antitrust and regulation,
   licensing disputes, corporate strategy clashes, market competition.
3. Breakthrough hardware or AI products — new chips, devices, models or
   platforms that materially change what is possible.

DE-PRIORITISE (rank these lower — do NOT drop them):
- Medical, clinical and health-adjacent stories, unless a major technology
  company or a shipping product is central to the story.
- Pure academic work — papers, preprints and university lab announcements with
  no company, product or industry dimension.

Break ties by recency, technical depth, source authority and relevance to the
topic.

You MUST include every news item in the ranking, including the ones you
de-prioritise — they still belong at the bottom of the list.

Finally, for your TOP 3 picks — the first three entries of your ranking — write
THREE English headlines each. These headlines ARE the video's YouTube title, so
every one of them must obey ALL of these rules:

1. Lead with the most interesting thing: the RESULT, the CONFLICT, the number or
   the surprise. Never lead with the company name, product name or backstory.
2. Say what happened and withhold WHY. That gap is what makes people click.
3. Declarative sentences only. An exclamation mark is fine; a question mark is
   not — a question is the weakest way to open the same gap.
4. Contrast is the strongest hook: expectation vs reality, cost vs payoff,
   free vs paid, small input vs huge output.
5. No channel name, no series name, no fixed prefix. Never write
   "TechSnack Daily -" or "Embedded Linux Daily -" in front of a headline —
   the very first words must be the story itself.
6. If you use a number, put it FIRST and use only figures that appear in the
   story. Never invent one.
7. No hype that cannot be checked. Banned: "shocking", "insane", "you won't
   believe", "nobody knows", "99% of people" and anything like them.
8. Never first person. This is news reporting, not personal experience.

Keep the wording plain and concrete — an everyday word beats a clever one.
Never state a fact, number or quote that is not in the story.

THE LENGTH LIMITS ARE HARD, AND THEY ARE THE POINT. A short headline hits harder
than a long one, so a long answer is a WRONG answer even if every word is true.
Before you output a headline, count its characters (spaces count too). Over the
limit? Cut words and count again. Do not output anything over the limit.

But cut the words AROUND the important ones — never the important ones. The name
of the company or product, and the number, are what make people stop scrolling.
Drop the verbs, adjectives, filler and backstory instead, and drop a name only if
there is genuinely no room for it. A specific name always beats a vague category:
"U-Boot Bug Exposes Gear" (23) says which devices are in danger, while "Bug
Exposes Linux Gear" (22) could be any Linux story. Keep the product or company
name even when a category word is shorter — "Linux", "AI", "chip" and "tool" are
not names. Note where the names sit: a name matters, but it does not have to be
the first word — the opening slot belongs to the number, the result or the
conflict (rule 1):

  "SpaceX Seeks $40 Billion To Buy Nvidia Chips"  (44) → "$40B Nvidia Chip Bid"      (20)
  "Microsoft Takes On Apple With New AI PCs"      (40) → "AI PCs Challenge Apple"    (22)
  "EU Fines Chip Giant $5.7 Billion For Monopoly" (45) → "$5.7B Fine For Chip Giant" (25)
  "Free Linux Tools That Beat Paid Rivals"        (38) → "Free Linux Beats Paid"     (21)

A headline with no actor, no number and no outcome ("A Big Change Is Coming!") is
short but says nothing — it is NOT what these limits are for.

Put all three title fields on each of those top 3 entries inside "ranking":
  "video_title"       — long-form video. MAXIMUM 25 characters (about 4 short
                        words). The most important words must be inside the
                        FIRST 15 characters — the opening is all that shows on
                        a phone.
  "video_title_short" — the SAME story, even shorter: the backup used only if
                        the main one cannot be used. Same angle — never a
                        different story. MAXIMUM 15 characters (about 3 words).
  "shorts_title"      — 60-second vertical short. Hit the pain, the contrast or
                        the result head-on. MAXIMUM 15 characters (about 3 words).

Final check before you return: is EVERY one of those nine headlines within its
character limit? If not, shorten it now.

Every news item must still appear in "ranking" — only your top 3 carry the
three title fields. The other entries keep just index / title / score / reason.

Return JSON only, with this exact shape:
{{
  "ranking": [
    {{"index": 0, "title": "...", "score": 8.5, "reason": "one short line", "video_title": "...", "video_title_short": "...", "shorts_title": "..."}},
    {{"index": 4, "title": "...", "score": 8.1, "reason": "one short line", "video_title": "...", "video_title_short": "...", "shorts_title": "..."}},
    {{"index": 7, "title": "...", "score": 7.8, "reason": "one short line", "video_title": "...", "video_title_short": "...", "shorts_title": "..."}},
    {{"index": 2, "title": "...", "score": 6.0, "reason": "one short line"}}
  ],
  "top1": {{"index": 0, "title": "...", "url": "...", "headline": "one-sentence summary", "why_top": "2-3 sentence rationale"}}
}}

News items:
{items_json}
"""


HISTORY_FILE_NAME = "topic_history.json"
# 08-19: 7→14 — FIT spec 於 08-10 選過,9 天後(08-19)視窗已過又被撿回(霸榜頭條),
# 使用者反應短期重複。14 天窗口讓熱門舊聞退場前不會立刻重複。
#
# 09-16: 14→90 — 14 天仍然不夠。由 YouTube 歷史反查證實:同一篇文章會在
# RSS feed 裡存活數週(FIT spec 08-06 起被選中,09-02 又中;U-Boot CVE
# 08-31 選過,09-15 又中),只要撐過窗口就重新可選,等於整支影片重製。
# 窗口拉長後,候選池理論上可能被全部封鎖 → pick_topic 會明確 fail(見該函式),
# 寧可當天不產片也不要重複上傳。
HISTORY_DAYS = 90

# URL 比對前剝除的追蹤參數 — 與文章身分無關。
# 只剝這些,不整段丟棄 query:部分網站的 query 就是文章身分(如 ?id=123)。
TRACKING_PARAMS = frozenset(
    {
        "oc",
        "hl",
        "gl",
        "ceid",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
        "fbclid",
        "gclid",
    }
)

# 尾綴剝離後至少要有這麼多個正規化字元,否則視為過度剝離 → 保留原標題。
# 避免「Foo - Bar」這類短標題被剝成「Foo」而在去重時與其他主題誤撞。
MIN_BASE_LEN = 20

# 可用候選低於此數就發警告(但不失敗)— 讓候選池被去重吃乾前先看到徵兆
LOW_POOL_WARN = 5


def normalize_title(text: str) -> str:
    """只做字元正規化: 小寫 + 去非字母數字。"""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def title_key(title: str) -> str:
    """主題正規化 — 供去重比對。

    先剝掉「 - 來源名」尾綴再正規化。**一律剝掉最後一個「 - 」之後的整段**,
    不去猜來源名有幾個字:來源名寫法與長度都會變 —
    「- Phoronix」/「- phoronix.com」/「- Embedded Computing Design」。

    舊版用 `[^-\\s]+$` 只剝得掉「單一 token」的來源名,多字來源名原樣留著,
    於是同一篇文章「…Platforms - Embedded Computing Design」與
    「…Platforms - embeddedcomputing.com」產生**不同 key** → 去重靜默失效
    (2026-09-16 由 YouTube 歷史反查證實:同一篇文被重製 14 次)。
    注意:部分剝離比完全不剝離更糟 — 它讓「本該相符」的兩者錯開。

    只認「空白-空白」的破折號,所以「Linux-Based」「GPT-6」這類連字號不受影響。
    """
    base = re.sub(r"\s+-\s+.*$", "", title).strip()
    if len(normalize_title(base)) >= MIN_BASE_LEN:
        return normalize_title(base)
    return normalize_title(title)


def url_key(url: str) -> str:
    """文章 URL 正規化 — 供去重比對。

    去 scheme / www. / fragment / 追蹤參數,其餘原樣保留。
    2026-09-16 實證:同一篇文章的 Google News 轉址 URL 在相隔 26 天的
    兩次抓取中**逐字元相同**(14 次重複上傳的 URL 全等),而標題尾端的
    來源名寫法會變 — 所以 URL 是比標題更可靠的鍵。
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return normalize_title(raw)
    host = parts.netloc.lower().removeprefix("www.")
    kept = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    )
    query = urlencode(kept)
    return f"{host}{parts.path}" + (f"?{query}" if query else "")


def topic_keys(title: str = "", url: str = "") -> set[str]:
    """主題比對鍵集合 — 任一鍵相符即視為同一主題。

    同時算 URL 與標題兩種鍵:
    - **URL 是主要訊號**(穩定,見 url_key)。
    - **標題保留為次要訊號** — 舊版 history 沒有 url 欄位,且文章可能
      以新的 URL 重新入池。兩者取聯集,比單用其一更不容易漏封鎖。
    """
    keys = set()
    if title:
        keys.add(title_key(title))
    if url:
        keys.add(url_key(url))
    keys.discard("")  # 全標點/空字串不該變成萬用鍵
    return keys


def parse_history_date(entry: dict) -> date | None:
    """歷史條目日期解析 — 壞資料回 None,不讓選題崩潰。"""
    raw = entry.get("date")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def entry_index(entry: object, n_items: int) -> int | None:
    """ranking 條目的 index 解析 — 畸形(非 dict / 缺欄位 / 非數字 / 越界)回 None。

    呼叫端跳過 None 並計數警告,不讓 Gemini 的畸形輸出變成未捕捉例外
    (IndexError / KeyError / ValueError),那會蓋掉真正的原因。
    """
    if not isinstance(entry, dict):
        return None
    try:
        idx = int(entry["index"])
    except (KeyError, TypeError, ValueError):
        return None
    return idx if 0 <= idx < n_items else None


def pick_topic(
    items: list, ranking: list, history: list, today: str
) -> tuple[dict, dict]:
    """依排名挑選,跳過 HISTORY_DAYS 天內已選過的主題。

    ranking: Gemini 的 [{index, title, ...}] 列表(已依分數排序)。
    回傳 (chosen_item, chosen_ranking_entry)。

    當天(同日 catch-up 重跑)已記錄的主題不封鎖 —
    否則重跑會改選別的主題,造成同日主題翻轉。

    若**全部候選都在去重窗口內**,直接 fail 而不是退回第一名:
    重複主題 = 重製一支既有影片(浪費 NotebookLM 配額 + 頻道多一支重複),
    比當天不產片更糟,而且使用者無從得知。舊版靜默退回第一名正是
    2026-09 重複上傳的成因之一。
    """
    cutoff = date.fromisoformat(today) - timedelta(days=HISTORY_DAYS - 1)
    recent: set[str] = set()
    recent_titles: list[str] = []
    for h in history:
        d = parse_history_date(h)
        if d is not None and h.get("date") != today and d >= cutoff:
            recent |= topic_keys(h.get("title", ""), h.get("url", ""))
            if h.get("title"):
                recent_titles.append(h["title"])
    available = []
    invalid = 0
    for e in ranking:
        idx = entry_index(e, len(items))
        if idx is None:
            invalid += 1
            continue
        item = items[idx]
        if topic_keys(item.get("title", ""), item.get("url", "")) & recent:
            continue
        available.append(e)
    if invalid:
        # Gemini 回傳畸形條目(缺 index / 非數字 / 越界)不該變成一條 traceback —
        # 計數發警告後跳過。2026-10-08:新 prompt 要求「每一則都要排名」,
        # ranking 變長、出現壞 index 的機會上升。
        logger.warning(
            f"[WARN] ranking 有 {invalid} 則條目 index 無效(缺欄位/非數字/越界),已跳過"
        )
    if not available:
        if not ranking:
            # 空陣列時 invalid 也是 0,會落進下面的「全部 index 無效」而語意
            # 不通(0 則都不合格?)— 先攔下來說清楚(2026-10-08 reviewer F2)。
            fail(
                logger,
                "Gemini 回傳的 ranking 是空陣列,沒有任何候選可挑",
                "prompt 要求「每一則新聞都要排名」— 空陣列代表回應不符契約,"
                "檢查 GEMINI_MODEL 與回應格式",
            )
        if invalid == len(ranking):
            # 全部條目的 index 都壞 → 真因是 Gemini 回傳格式,不是去重窗口。
            # 舊版會把這種情況報成「全部落在去重窗口內」,事故時查錯方向
            # (2026-10-08 reviewer R2)。
            fail(
                logger,
                f"ranking 的 {len(ranking)} 則條目 index 全部無效"
                "(缺欄位/非數字/越界),沒有任何可用候選",
                "這不是去重造成的 — 檢查 Gemini 回傳格式與 prompt 契約"
                "(ranking[].index 必須是 0-based 且小於新聞則數)",
            )
        fail(
            logger,
            f"{len(ranking)} 則候選全部落在 {HISTORY_DAYS} 天去重窗口內,沒有新主題可選"
            + (f"(另有 {invalid} 則條目 index 無效,已跳過)" if invalid else ""),
            "新聞池可能過期或去重窗口過長。寧可當天不產片,也不要重複上傳既有主題;"
            f"最近的已選主題: {recent_titles[:3]}",
        )
    if len(available) <= LOW_POOL_WARN:
        # 先預警再失敗:窗口拉長後候選池會逐步被吃掉,這裡讓使用者提早看到。
        # 帶上 invalid 計數 — 否則壞條目會讓「新聞池過窄」的判斷失準
        # (2026-10-08 reviewer R2)。
        logger.warning(
            f"[WARN] 可用主題僅剩 {len(available)} 則(候選 {len(ranking)} 則"
            + (f",其中 {invalid} 則 index 無效" if invalid else "")
            + f",窗口 {HISTORY_DAYS} 天)— 新聞池可能過窄"
        )
    entry = available[0]
    return items[int(entry["index"])], entry


def apply_dedup_choice(
    top1: dict, chosen: dict, chosen_entry: dict, idx: int, logger
) -> dict:
    """去重改選後,把 top1 的識別欄位換成真正選中的那篇。

    Gemini 的 top1 描述的是它自己排的 #1;pick_topic 若因去重窗口改選了別則,
    沿用會產生「A 的標題 + B 的內容」混合檔(2026-08-14 實測:top1.json 出現
    「FIT 標題 + ELBE news」)。

    top1.index 缺失或畸形時無法證明兩者一致 — **一律視為不一致並覆寫**。
    覆寫用的資訊全部來自 chosen(實際選中的文章),必然正確;不覆寫則有機率
    張冠李戴且完全無聲,兩害相權取其輕(2026-10-08 reviewer 指出原設計
    只在 `top1_index is not None` 時覆寫,None 會整段跳過)。

    爆款標題欄位(VIRAL_TITLE_FIELDS)**一律**取自 chosen_entry,兩個分支都取:
    Gemini 現在把標題寫在前三名各自的 ranking 條目上(單一來源),不再寫在
    top1 裡 — 所以就算 top1.index 與去重結果一致,top1 本身也沒有標題可留。
    chosen_entry 拿不到欄位時(去重改選到前三名之外)清成空字串,
    build_title 會退回原始新聞標題,不會留下前一篇的殘值。
    """
    try:
        top1_index = int(top1.get("index"))
    except (TypeError, ValueError):
        top1_index = None
    matched = top1_index == idx
    if matched:
        base = dict(top1)  # 一致 — 保留 Gemini 自己的 why_top/headline
    else:
        if top1_index is None:
            logger.warning(
                "[WARN] Gemini 的 top1 缺少有效 index,無法確認與去重結果一致 —"
                " 一律以實際選中的文章覆寫 top1"
            )
        base = {
            **top1,
            "index": idx,
            # 一律 .get:同一輪已把 pick_topic 的取用改成容錯,這裡若用 [] 就只做了
            # 一半 — 缺 key 一樣是未捕捉的 KeyError(2026-10-08 reviewer R4)。
            "title": chosen.get("title", ""),
            "url": chosen.get("url", ""),
            "why_top": chosen_entry.get("reason", ""),
            "headline": chosen.get("summary", ""),
        }
    for field in VIRAL_TITLE_FIELDS:
        value = chosen_entry.get(field)
        base[field] = value if isinstance(value, str) else ""
    if not any(base[f] for f in VIRAL_TITLE_FIELDS):
        if matched:
            # 選中的就是 Gemini 自己的 #1,卻一個標題欄位都沒有 → 它沒照 prompt
            # 的契約寫(前三名必然含 #1)。這是契約違反,不是預期路徑。
            logger.warning(
                "[WARN] 選中的是 Gemini 的 #1,但它的 ranking 條目沒有任何爆款標題"
                "欄位 — 回應不符 prompt 契約;本輪將退回原始新聞標題"
            )
        else:
            # 去重改選到前三名之外 — 那些條目本來就沒有標題,屬預期情形。
            # 記 info:下游 build_title 另有 WARN,不會靜默。
            logger.info(
                f"[INFO] 去重改選到前三名之外的條目(index {idx}),該條目沒有爆款"
                "標題 — 本輪將退回原始新聞標題"
            )
    return base


def main() -> None:
    load_env()
    args = sys.argv[1:]
    slug = flag_value(args, "--channel")
    channel = resolve_channel(slug, logger)
    cdir = channel_dir(channel)

    raw_path = cdir / "news_raw.json"
    if not raw_path.exists():
        fail(
            logger,
            f"找不到 {raw_path} — 請先執行 scripts/fetch_news.py --channel {channel['slug']}",
        )
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    items = raw["items"]
    if not items:
        fail(logger, "news_raw.json 沒有任何新聞項目")

    items_for_prompt = [
        {
            "index": i,
            "title": it["title"],
            "url": it["url"],
            "source": it["source"],
            "summary": it["summary"],
        }
        for i, it in enumerate(items)
    ]
    prompt = RANK_PROMPT_TEMPLATE.format(
        topic=channel["keyword"],
        items_json=json.dumps(items_for_prompt, ensure_ascii=False, indent=2),
    )
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)  # load_env 之後才讀
    result = gemini_json(prompt, logger, model=model)

    ranking = result.get("ranking")
    top1 = result.get("top1")
    if not isinstance(ranking, list) or not isinstance(top1, dict):
        fail(
            logger,
            "Gemini 回傳缺少 ranking/top1 欄位",
            json.dumps(result, ensure_ascii=False)[:2000],
        )

    today = today_str()
    # 7 天去重 — 讀歷史 → pick_topic 跳過近期主題 → 之後回寫本次選題
    history_path = cdir / HISTORY_FILE_NAME
    history = []
    if history_path.exists():
        history = json.loads(history_path.read_text(encoding="utf-8"))

    chosen, chosen_entry = pick_topic(items, ranking, history, today)
    # LLM 可能把 index 回成字串 "0" — 強制轉 int 再檢查範圍
    try:
        idx = int(chosen_entry["index"])
    except (TypeError, ValueError):
        idx = -1
    if not (0 <= idx < len(items)):
        fail(
            logger,
            f"ranking 條目 index 無效: {chosen_entry.get('index')!r}"
            f"(有效範圍 0-{len(items) - 1})",
            json.dumps(chosen_entry, ensure_ascii=False),
        )

    # Gemini 的 top1 描述的是它自己排的 #1;若去重改選了別則,改用真正選中的那篇
    top1 = apply_dedup_choice(top1, chosen, chosen_entry, idx, logger)

    top1_full = {
        **top1,
        "channel": channel["slug"],
        "date": today,  # 供下游檢查新聞新鮮度(避免用昨天的新聞)
        "news": {
            "title": chosen["title"],
            "url": chosen["url"],  # Google News 轉址 — collect 階段會解析成真實網址
            "source": chosen["source"],
            "summary": chosen["summary"],
            "published": chosen["published"],
        },
    }
    # 回寫歷史:同日(catch-up 重跑)不重複記錄;只保留 HISTORY_DAYS 天窗口。
    # 一併寫入 url — 之後的比對才有穩定的鍵可用(舊條目沒有 url,靠標題鍵涵蓋)。
    keys = topic_keys(chosen["title"], chosen.get("url", ""))
    if not any(
        h.get("date") == today
        and topic_keys(h.get("title", ""), h.get("url", "")) & keys
        for h in history
    ):
        history.append(
            {"date": today, "title": chosen["title"], "url": chosen.get("url", "")}
        )
    cutoff = date.fromisoformat(today) - timedelta(days=HISTORY_DAYS - 1)
    pruned: list[dict] = []
    for h in history:
        d = parse_history_date(h)
        if d is not None and d >= cutoff:
            pruned.append(h)
    save_json(pruned, history_path)
    save_json(result, cdir / "ranking.json")
    save_json(top1_full, cdir / "top1.json")
    logger.info(f"[PASS] TOP 1 選出: {chosen['title']}")
    logger.info(f"      來源: {chosen['source']}  {chosen['url']}")
    logger.info(f"      理由: {top1.get('why_top', '')}")


if __name__ == "__main__":
    main()
