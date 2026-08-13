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

from _common import (
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

DEFAULT_MODEL = "gemini-2.5-flash"

RANK_PROMPT_TEMPLATE = """You are a news editor for an audience interested in the topic "{topic}".
Rank the following news items by: (1) relevance to the topic, (2) recency,
(3) technical depth, (4) source authority.
Return JSON only, with this exact shape:
{{
  "ranking": [
    {{"index": 0, "title": "...", "score": 8.5, "reason": "one short line"}}
  ],
  "top1": {{"index": 0, "title": "...", "url": "...", "headline": "one-sentence summary", "why_top": "2-3 sentence rationale"}}
}}

News items:
{items_json}
"""


HISTORY_FILE_NAME = "topic_history.json"
HISTORY_DAYS = 7


def title_key(title: str) -> str:
    """主題正規化: 小寫 + 去非字母數字 — 供去重比對。"""
    return re.sub(r"[^a-z0-9]", "", title.lower())


def parse_history_date(entry: dict) -> date | None:
    """歷史條目日期解析 — 壞資料回 None,不讓選題崩潰。"""
    raw = entry.get("date")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def pick_topic(
    items: list, ranking: list, history: list, today: str
) -> tuple[dict, dict]:
    """依排名挑選,跳過 HISTORY_DAYS 天內已選過的主題。

    ranking: Gemini 的 [{index, title, ...}] 列表(已依分數排序)。
    全部重複時退回第一名(極端情況)。回傳 (chosen_item, chosen_ranking_entry)。

    當天(同日 catch-up 重跑)已記錄的主題不封鎖 —
    否則重跑會改選別的主題,造成同日主題翻轉。
    """
    cutoff = date.fromisoformat(today) - timedelta(days=HISTORY_DAYS - 1)
    recent: set[str] = set()
    for h in history:
        d = parse_history_date(h)
        if d is not None and h.get("date") != today and d >= cutoff:
            recent.add(title_key(h.get("title", "")))
    for entry in ranking:
        item = items[int(entry["index"])]
        if title_key(item["title"]) not in recent:
            return item, entry
    # 全部都是近期主題(極端) → 退回第一名
    return items[int(ranking[0]["index"])], ranking[0]


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

    # Gemini 的 top1 理由屬於它自己選的 #1;若去重改選了別則,
    # 改用該候選的 reason,避免張冠李戴
    try:
        top1_index = int(top1.get("index"))
    except (TypeError, ValueError):
        top1_index = None
    if top1_index is not None and top1_index != idx:
        top1 = {
            **top1,
            "why_top": chosen_entry.get("reason", ""),
            "headline": chosen.get("summary", ""),
        }

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
    # 回寫歷史:同日(catch-up 重跑)不重複記錄;只保留 7 天窗口
    key = title_key(chosen["title"])
    if not any(
        h.get("date") == today and title_key(h.get("title", "")) == key
        for h in history
    ):
        history.append({"date": today, "title": chosen["title"]})
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
