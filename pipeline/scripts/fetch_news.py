#!/usr/bin/env python3
"""Step 1 — Google News RSS 抓取(per-channel)。

用法: python scripts/fetch_news.py [--channel <slug>]
頻道與關鍵字定義在 config/channels.json;輸出 output/<slug>/news_raw.json。
不需 API key — Google News RSS 是公開 feed。
"""

import html
import os
import queue
import re
import sys
import threading
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx
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
FETCH_DEADLINE = 45.0  # 執行緒護欄總期限;必須 > REQUEST_TIMEOUT


def _bounded_get(url: str) -> httpx.Response:
    """daemon thread 執行 httpx.get,DNS/連線卡死時快速失敗。

    httpx/socket 的 timeout 管不到 libc 的 DNS 解析(getaddrinfo 阻塞不遵守
    socket timeout)— 2026-09-08 整天卡在 "Temporary failure in name
    resolution"、fetch 空轉 8 小時的實測教訓。護欄保證主流程在
    FETCH_DEADLINE 內一定返回:逾時則 fail(卡住的 daemon thread 隨
    process 結束回收,不阻塞 exit);httpx 例外原樣送回主執行緒重拋,
    維持呼叫方的錯誤處理不變。
    """
    box: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": UA},
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
            )
            box.put(("ok", resp))
        except Exception as exc:  # httpx.HTTPError 等
            box.put(("err", exc))

    t = threading.Thread(target=worker, daemon=True, name="rss-fetch")
    t.start()
    try:
        status, payload = box.get(timeout=FETCH_DEADLINE)
    except queue.Empty:
        fail(
            logger,
            f"Google News RSS 抓取逾時(DNS/連線卡住 > {FETCH_DEADLINE:.0f}s)"
            "— 快速失敗,待下一輪重試",
        )
    if status == "err":
        assert isinstance(payload, BaseException)
        raise payload
    resp = payload
    assert isinstance(resp, httpx.Response)
    return resp


def fetch_rss(channel: dict) -> str:
    kw = channel["keyword"]
    query = urllib.parse.quote(kw)
    url = (
        f"https://news.google.com/rss/search?q={query}"
        f"&hl={channel.get('hl', 'en')}&gl={channel.get('gl', 'US')}&ceid={channel.get('ceid', 'US:en')}"
    )
    logger.info(f"[INFO] 抓取 Google News RSS ({channel['slug']}): {url}")
    resp = _bounded_get(url)
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
