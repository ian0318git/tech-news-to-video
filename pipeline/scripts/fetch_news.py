#!/usr/bin/env python3
"""Step 1 — Google News RSS 抓取(per-channel)。

用法: python scripts/fetch_news.py [--channel <slug>]
頻道與關鍵字定義在 config/channels.json;輸出 output/<slug>/news_raw.json。
不需 API key — Google News RSS 是公開 feed。
"""

import html
import json
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from _common import channel_dir, fail, flag_value, load_env, resolve_channel, save_json, setup_logging

logger = setup_logging("fetch_news")

LIMIT = int(__import__("os").environ.get("NEWS_LIMIT", "20"))
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def fetch_rss(channel: dict) -> str:
    kw = channel["keyword"]
    query = urllib.parse.quote(kw)
    url = (
        f"https://news.google.com/rss/search?q={query}"
        f"&hl={channel.get('hl', 'en')}&gl={channel.get('gl', 'US')}&ceid={channel.get('ceid', 'US:en')}"
    )
    logger.info(f"[INFO] 抓取 Google News RSS ({channel['slug']}): {url}")
    resp = httpx.get(url, headers={"User-Agent": UA}, timeout=30.0, follow_redirects=True)
    if resp.status_code != 200:
        fail(logger, f"Google News RSS 回傳 HTTP {resp.status_code}", resp.text[:1000])
    return resp.text


def parse_items(xml_text: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        fail(logger, "RSS XML 解析失敗", str(exc))
    items = []
    for item in root.iter("item"):
        def text(tag: str) -> str:
            node = item.find(tag)
            return (node.text or "").strip() if node is not None and node.text else ""

        title = text("title")
        link = text("link")
        if not title or not link:
            continue
        source = item.find("source")
        source_name = source.text.strip() if source is not None and source.text else ""
        desc = text("description")
        summary = re.sub(r"<[^>]+>", "", desc)
        summary = html.unescape(re.sub(r"\s+", " ", summary)).strip()
        items.append(
            {
                "title": title,
                "url": link,  # Google News 轉址 — 真實網址在 collect 階段跟隨轉址解析
                "source": source_name,
                "summary": summary[:500],
                "published": text("pubDate"),
            }
        )
    return items[:LIMIT]


def main() -> None:
    load_env()
    args = sys.argv[1:]
    slug = flag_value(args, "--channel")
    channel = resolve_channel(slug, logger)

    rss = fetch_rss(channel)
    items = parse_items(rss)
    if not items:
        fail(logger, f"關鍵字 {channel['keyword']!r} 沒有抓到任何新聞")
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "channel": channel["slug"],
        "keyword": channel["keyword"],
        "count": len(items),
        "items": items,
    }
    out_path = channel_dir(channel) / "news_raw.json"
    save_json(payload, out_path)
    for it in items[:5]:
        logger.info(f"  - [{it['source']}] {it['title']}")
    if len(items) > 5:
        logger.info(f"  ...(共 {len(items)} 則)")
    logger.info(f"[PASS] 新聞已寫入 {out_path}")


if __name__ == "__main__":
    main()
