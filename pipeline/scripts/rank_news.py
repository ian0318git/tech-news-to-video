#!/usr/bin/env python3
"""Step 2 — Gemini 排名選題(per-channel)。

用法: python scripts/rank_news.py [--channel <slug>]
讀取 output/<slug>/news_raw.json,選出 TOP 1,
寫出 output/<slug>/ranking.json 與 output/<slug>/top1.json。
需要 .env 的 GEMINI_API_KEY。
"""

import json
import os
import sys

from _common import channel_dir, fail, flag_value, gemini_json, load_env, resolve_channel, save_json, setup_logging

logger = setup_logging("rank_news")

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

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


def main() -> None:
    load_env()
    args = sys.argv[1:]
    slug = flag_value(args, "--channel")
    channel = resolve_channel(slug, logger)
    cdir = channel_dir(channel)

    raw_path = cdir / "news_raw.json"
    if not raw_path.exists():
        fail(logger, f"找不到 {raw_path} — 請先執行 scripts/fetch_news.py --channel {channel['slug']}")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    items = raw["items"]
    if not items:
        fail(logger, "news_raw.json 沒有任何新聞項目")

    items_for_prompt = [
        {"index": i, "title": it["title"], "url": it["url"], "source": it["source"], "summary": it["summary"]}
        for i, it in enumerate(items)
    ]
    prompt = RANK_PROMPT_TEMPLATE.format(
        topic=channel["keyword"], items_json=json.dumps(items_for_prompt, ensure_ascii=False, indent=2)
    )
    result = gemini_json(prompt, logger, model=MODEL)

    ranking = result.get("ranking")
    top1 = result.get("top1")
    if not isinstance(ranking, list) or not isinstance(top1, dict):
        fail(logger, "Gemini 回傳缺少 ranking/top1 欄位", json.dumps(result, ensure_ascii=False)[:2000])

    idx = top1.get("index")
    if not isinstance(idx, int) or not (0 <= idx < len(items)):
        fail(logger, f"top1.index 無效: {idx!r}(有效範圍 0-{len(items) - 1})", json.dumps(top1, ensure_ascii=False))

    chosen = items[idx]
    top1_full = {
        **top1,
        "channel": channel["slug"],
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
