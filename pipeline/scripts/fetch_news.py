#!/usr/bin/env python3
"""Step 1 — Google News RSS 抓取(per-channel)。

用法: python scripts/fetch_news.py [--channel <slug>]
頻道與關鍵字定義在 config/channels.json;輸出 output/<slug>/news_raw.json。
不需 API key — Google News RSS 是公開 feed。
"""

import html
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import _http
from _common import (
    channel_dir,
    fail,
    flag_value,
    load_env,
    resolve_channel,
    save_json,
    setup_logging,
)

logger = setup_logging("fetch_news")

LIMIT = 20  # 預設;實際值在 main() 讀 .env 後覆寫
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30.0  # httpx 層:涵蓋 TCP 連線/讀寫
FETCH_DEADLINE = 45.0  # 執行緒護欄總期限(_http.bounded_get);必須 > REQUEST_TIMEOUT

# Google News 搜尋的日期運算子;channel 可設 "freshness": "14d" 追加。
WHEN_OPERATOR = "when:"
WHEN_FORMAT = re.compile(r"\d+[dhm]\Z")


def search_keyword(channel: dict) -> str:
    """回傳送進 Google News 的查詢字串,必要時附加 `when:<freshness>`。

    2026-10-20 根因:「embedded linux」在沒有日期條件時,Google News RSS
    以**相關性**排序回傳 100 則,其中前 20 則常年是同一批舊文(實測最舊
    可到 1 月);fetch_news 只取前 20,於是每天都是同一批候選,90 天去重
    把它們全部封鎖 → embedded 頻道自 10-12 起完全停產。

    加上 `when:14d` 後改由 Google 先做日期過濾(實測 28 則、全部落在
    14 天內),新聞池才會滾動。channel 沒設 freshness 時行為完全不變。
    關鍵字本身已含 `when:` 就不再附加,避免同一個條件出現兩次。
    """
    kw = str(channel["keyword"]).strip()
    freshness = str(channel.get("freshness") or "").strip()
    if not freshness:
        return kw
    if not WHEN_FORMAT.match(freshness):
        fail(
            logger,
            f"頻道 {channel.get('slug')} 的 freshness 格式無效: {freshness!r}",
            "範例: 14d / 7d / 36h(when: 運算子的日期格式)",
        )
    if WHEN_OPERATOR in kw:
        return kw
    return f"{kw} {WHEN_OPERATOR}{freshness}"


def fetch_rss(channel: dict) -> str:
    kw = search_keyword(channel)
    query = urllib.parse.quote(kw)
    url = (
        f"https://news.google.com/rss/search?q={query}"
        f"&hl={channel.get('hl', 'en')}&gl={channel.get('gl', 'US')}&ceid={channel.get('ceid', 'US:en')}"
    )
    logger.info(f"[INFO] 抓取 Google News RSS ({channel['slug']}): {url}")
    try:
        # 執行緒護欄:httpx timeout 管不到 DNS 解析卡死(2026-09-08 教訓)—
        # 逾時快速失敗,交下一輪 catch-up 重試
        resp = _http.bounded_get(
            url,
            headers={"User-Agent": UA},
            timeout=REQUEST_TIMEOUT,
            deadline=FETCH_DEADLINE,
        )
    except _http.GuardTimeoutError:
        fail(
            logger,
            f"Google News RSS 抓取逾時(DNS/連線卡住 > {FETCH_DEADLINE:.0f}s)"
            "— 快速失敗,待下一輪重試",
        )
    if resp.status_code != 200:
        fail(logger, f"Google News RSS 回傳 HTTP {resp.status_code}", resp.text[:1000])
    return resp.text


def parse_items(xml_text: str, limit: int | None = None) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        fail(logger, "RSS XML 解析失敗", str(exc))

    def text(item, tag: str) -> str:
        node = item.find(tag)
        return (node.text or "").strip() if node is not None and node.text else ""

    items = []
    for item in root.iter("item"):
        title = text(item, "title")
        link = text(item, "link")
        if not title or not link:
            continue
        source = item.find("source")
        source_name = source.text.strip() if source is not None and source.text else ""
        desc = text(item, "description")
        summary = re.sub(r"<[^>]+>", "", desc)
        summary = html.unescape(re.sub(r"\s+", " ", summary)).strip()
        items.append(
            {
                "title": title,
                "url": link,  # Google News 轉址 — 真實網址在 collect 階段跟隨轉址解析
                "source": source_name,
                "summary": summary[:500],
                "published": text(item, "pubDate"),
            }
        )
    return items[: limit if limit else LIMIT]


def main() -> None:
    load_env()
    args = sys.argv[1:]
    slug = flag_value(args, "--channel")
    channel = resolve_channel(slug, logger)

    global LIMIT  # .env 的 NEWS_LIMIT 在 load_env 之後才讀
    try:
        LIMIT = int(os.environ.get("NEWS_LIMIT", "20"))
    except ValueError:
        fail(logger, f"NEWS_LIMIT 不是數字: {os.environ.get('NEWS_LIMIT')!r}")

    rss = fetch_rss(channel)
    items = parse_items(rss, limit=LIMIT)
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
