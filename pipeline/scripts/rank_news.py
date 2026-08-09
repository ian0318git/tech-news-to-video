#!/usr/bin/env python3
"""Step 2 — Gemini 排名選題(per-channel)。

用法: python scripts/rank_news.py [--channel <slug>]
讀取 output/<slug>/news_raw.json,選出 TOP 1,
寫出 output/<slug>/ranking.json 與 output/<slug>/top1.json。
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


def pick_topic(
    items: list, ranking: list, history: list, today: str
) -> tuple[dict, dict]:
    """依排名挑選,跳過 HISTORY_DAYS 天內已選過的主題。

    ranking: Gemini 的 [{index, title, ...}] 列表(已依分數排序)。
    全部重複時退回第一名(極端情況)。回傳 (chosen_item, chosen_ranking_entry)。
    """
    cutoff = date.fromisoformat(today) - timedelta(days=HISTORY_DAYS - 1)
    recent = {
        title_key(h["title"])
        for h in history
        if date.fromisoformat(h["date"]) >= cutoff
    }
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

    # LLM 可能把 index 回成字串 "0" — 強制轉 int 再檢查範圍
    try:
        idx = int(top1.get("index"))
    except (TypeError, ValueError):
        idx = -1
    if not (0 <= idx < len(items)):
        fail(
            logger,
            f"top1.index 無效: {top1.get('index')!r}(有效範圍 0-{len(items) - 1})",
            json.dumps(top1, ensure_ascii=False),
        )

    chosen = items[idx]
    top1_full = {
        **top1,
        "channel": channel["slug"],
        "date": today_str(),  # 供下游檢查新聞新鮮度(避免用昨天的新聞)
        "news": {
            "title": chosen["title"],
            "url": chosen["url"],  # Google News 轉址 — collect 階段會解析成真實網址
            "source": chosen["source"],
            "summary": chosen["summary"],
            "published": chosen["published"],
        },
    }
    save_json(result, cdir / "ranking.json")
    save_json(top1_full, cdir / "top1.json")
    logger.info(f"[PASS] TOP 1 選出: {chosen['title']}")
    logger.info(f"      來源: {chosen['source']}  {chosen['url']}")
    logger.info(f"      理由: {top1.get('why_top', '')}")


if __name__ == "__main__":
    main()
