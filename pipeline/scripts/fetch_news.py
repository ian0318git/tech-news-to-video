#!/usr/bin/env python3
"""Step 1 — 新聞抓取(per-channel)。

用法: python scripts/fetch_news.py [--channel <slug>]
頻道與來源定義在 config/channels.json;輸出 output/<slug>/news_raw.json。
不需 API key — 全部是公開 RSS/Atom feed。

2026-10-21 起支援多來源:
- channel 的 `feeds` 陣列列出 publisher RSS(時間排序、無配額、單站故障不致命)
- Google News RSS 仍保留為其中一個來源(廣度補充;channel 可設 `freshness`)
各來源輪流取樣,確保每個來源都有代表,再依 URL/標題去重後輸出。
"""

import html
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import _http
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
FETCH_DEADLINE = 45.0  # 執行緒護欄總期限(_http.bounded_get);必須 > REQUEST_TIMEOUT

# Google News 搜尋的日期運算子;channel 可設 "freshness": "14d" 追加。
WHEN_OPERATOR = "when:"
WHEN_FORMAT = re.compile(r"\d+[dhm]\Z")

ATOM = "{http://www.w3.org/2005/Atom}"

# 合併時的 URL 去重:這些追蹤參數不影響文章身分。
TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src"}

# 尾綴剝離後至少要有這麼多個正規化字元,否則視為過度剝離 → 保留原標題。
# 與 rank_news.MIN_BASE_LEN 同值:避免「Tech Life - BBC」與「Tech Now - BBC」
# 這類短標題被剝成同名,在合併時誤殺不同文章(2026-10-21 reviewer 實測)。
MIN_TITLE_BASE_LEN = 20


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


def google_url(channel: dict) -> str:
    """Google News RSS 搜尋 URL(保留為多來源的其中一個)。"""
    kw = search_keyword(channel)
    query = urllib.parse.quote(kw)
    return (
        f"https://news.google.com/rss/search?q={query}"
        f"&hl={channel.get('hl', 'en')}&gl={channel.get('gl', 'US')}"
        f"&ceid={channel.get('ceid', 'US:en')}"
    )


def feed_defs(channel: dict) -> list[dict]:
    """解析並驗證 channel 的 `feeds` 設定;格式錯誤立刻 fail(不靜默)。"""
    feeds = channel.get("feeds", [])
    if feeds is None:
        return []
    if not isinstance(feeds, list):
        fail(
            logger,
            f"頻道 {channel.get('slug')} 的 feeds 必須是陣列",
            '格式: [{"name": "Phoronix", "url": "https://www.phoronix.com/rss.php"}]',
        )
    out = []
    for i, feed in enumerate(feeds):
        if not isinstance(feed, dict):
            fail(logger, f"頻道 {channel.get('slug')} 的 feeds[{i}] 不是物件")
        name = str(feed.get("name") or "").strip()
        url = str(feed.get("url") or "").strip()
        if not name or not url:
            fail(
                logger,
                f"頻道 {channel.get('slug')} 的 feeds[{i}] 缺少 name 或 url",
                f"實際值: {feed!r}",
            )
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError as exc:
            fail(logger, f"頻道 {channel.get('slug')} 的 feeds[{i}] URL 無效: {url!r}", str(exc))
        if parts.scheme not in ("http", "https") or not parts.netloc:
            fail(
                logger,
                f"頻道 {channel.get('slug')} 的 feeds[{i}] 必須是 http(s) URL: {url!r}",
            )
        out.append({"name": name, "url": url})
    return out


def fetch_source(url: str, label: str, *, strict: bool) -> str | None:
    """抓一個 feed。strict=True 失敗即退出(Google News 既有行為);
    strict=False 只警告並回 None — 單一 publisher 掛掉不該拖垮整個頻道。"""
    logger.info(f"[INFO] 抓取 {label}: {url}")
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
        msg = (
            f"{label} 抓取逾時(DNS/連線卡住 > {FETCH_DEADLINE:.0f}s)"
            "— 快速失敗,待下一輪重試"
        )
        if strict:
            fail(logger, msg)
        logger.warning(f"[WARN] {msg};跳過此來源")
        return None
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        # InvalidURL 不是 HTTPError(例如 config 寫壞的 port/NUL 字元) —
        # 不一起接住的話,strict=False 仍會讓整個頻道 crash。
        if strict:
            raise
        logger.warning(
            f"[WARN] {label} 連線失敗({type(exc).__name__}: {exc});跳過此來源"
        )
        return None
    if resp.status_code != 200:
        msg = f"{label} 回傳 HTTP {resp.status_code}"
        if strict:
            fail(logger, msg, resp.text[:1000])
        logger.warning(f"[WARN] {msg};跳過此來源")
        return None
    return resp.text


def fetch_rss(channel: dict) -> str:
    """抓 Google News RSS(單一來源模式,既有測試與呼叫端依賴此行為)。"""
    return fetch_source(
        google_url(channel), f"Google News RSS ({channel['slug']})", strict=True
    )


def _node_text(node) -> str:
    """取節點全文(含巢狀 XHTML 的文字),正規化空白。"""
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def _child_text(node, *tags: str) -> str:
    """取第一個有文字的指定子節點;RSS 用裸標籤、Atom 自動加 namespace。"""
    for tag in tags:
        child = node.find(tag)
        if child is None and not tag.startswith("{"):
            child = node.find(f"{ATOM}{tag}")
        if child is not None:
            text = _node_text(child)
            if text:
                return text
    return ""


def _atom_link(node) -> str:
    """Atom 的 <link> 是屬性而非文字;優先取 rel="alternate"。"""
    fallback = ""
    for link in node.findall(f"{ATOM}link"):
        href = (link.get("href") or "").strip()
        if not href:
            continue
        if link.get("rel", "alternate") == "alternate":
            return href
        fallback = fallback or href
    return fallback


def _clean_summary(raw: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


def parse_feed(
    xml_text: str, source_name: str = "", *, limit: int | None = None, strict: bool = False
) -> list[dict]:
    """解析 RSS 2.0 或 Atom,輸出與 Google News 相同的 item schema。

    strict=False(直接來源):單一 feed 的 XML 壞掉只警告並回空 list。
    strict=True(既有 parse_items 契約):解析失敗呼叫 fail。
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        msg = f"RSS/Atom XML 解析失敗({source_name or 'unknown'}): {exc}"
        if strict:
            fail(logger, msg)
        logger.warning(f"[WARN] {msg};跳過此來源")
        return []

    nodes = list(root.iter("item")) or list(root.iter(f"{ATOM}entry"))
    items = []
    for node in nodes:
        title = _child_text(node, "title")
        link = _child_text(node, "link") or _atom_link(node)
        if not title or not link:
            continue
        # Google News 的 RSS 有 <source> 子節點;直接來源用設定檔的 name。
        source = _child_text(node, "source") or source_name
        summary = _clean_summary(
            _child_text(node, "description", "summary", "content")
        )
        items.append(
            {
                "title": title,
                "url": link,  # Google News 是轉址 — collect 階段會跟隨解析
                "source": source,
                "summary": summary[:500],
                "published": _child_text(
                    node, "pubDate", "published", "updated", f"{ATOM}published",
                    f"{ATOM}updated",
                ),
            }
        )
    return items[:limit] if limit is not None else items


def parse_items(xml_text: str, limit: int | None = None) -> list[dict]:
    """既有 Google News 解析入口(RSS 2.0,預設套用 LIMIT)。"""
    return parse_feed(xml_text, limit=LIMIT if limit is None else limit, strict=True)


def item_url_key(url: str) -> str:
    """同日合併用的 URL 身分鍵:忽略大小寫、www、尾斜線與追蹤參數。"""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        # 畸形 URL(如 "http://[")不可讓整個頻道 crash;退回字串正規化。
        return re.sub(r"[^a-z0-9]+", "", url.lower())
    host = parts.netloc.lower().removeprefix("www.")
    query = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/")
    return f"{host}{path}?{urllib.parse.urlencode(query)}" if query else f"{host}{path}"


def item_title_key(title: str) -> str:
    """同日合併用的標題鍵:剝掉「 - 來源」尾綴再正規化。

    與 rank_news.title_key 相同防護:剝完若太短就保留原標題,否則
    「Tech Life - BBC」與「Tech Now - BBC」會撞成同一個 key,誤殺候選。
    """
    base = re.sub(r"\s+-\s+.*$", "", title.strip())
    candidate = (
        base
        if len(re.sub(r"[^a-z0-9]+", "", base.lower())) >= MIN_TITLE_BASE_LEN
        else title
    )
    return re.sub(r"[^a-z0-9]+", "", candidate.lower())[:120]


def merge_items(batches: list[list[dict]], limit: int) -> list[dict]:
    """各來源輪流取樣(第一輪各取第 1 則、第二輪各取第 2 則…)並去重。

    輪流而不是串接,是為了讓每個來源都有代表:若直接串接,排前面的
    Phoronix/Wired 會吃滿 20 個名額,publisher RSS 的多樣性就白費了。
    """
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    out: list[dict] = []
    depth = 0
    while len(out) < limit:
        progressed = False
        for batch in batches:
            if depth >= len(batch):
                continue
            progressed = True
            item = batch[depth]
            url_key = item_url_key(item["url"])
            title_key = item_title_key(item["title"])
            if url_key in seen_urls or title_key in seen_titles:
                continue
            seen_urls.add(url_key)
            seen_titles.add(title_key)
            out.append(item)
            if len(out) >= limit:
                break
        if not progressed:
            break
        depth += 1
    return out


def gather_items(channel: dict) -> list[dict]:
    """抓所有直接來源 + Google News,合併去重後回傳 LIMIT 則。"""
    batches: list[list[dict]] = []
    feeds = feed_defs(channel)
    failed = 0  # 連線/HTTP 失敗
    empty = 0  # 抓到了但解析不出任何項目

    for feed in feeds:
        xml = fetch_source(feed["url"], feed["name"], strict=False)
        if xml is None:
            failed += 1
            continue
        items = parse_feed(xml, feed["name"])
        if not items:
            empty += 1
            logger.warning(f"[WARN] {feed['name']} 沒有可用項目;跳過")
            continue
        batches.append(items)
        logger.info(f"[INFO] {feed['name']}: {len(items)} 則")

    xml = fetch_source(
        google_url(channel), f"Google News RSS ({channel['slug']})", strict=False
    )
    if xml is None:
        failed += 1
    else:
        items = parse_feed(xml)
        if items:
            batches.append(items)
            logger.info(f"[INFO] Google News: {len(items)} 則")
        else:
            empty += 1

    if not batches:
        total = len(feeds) + 1
        if failed == total:
            fail(
                logger,
                f"頻道 {channel['slug']} 的所有新聞來源都抓取失敗",
                "檢查網路/DNS,或 config/channels.json 的 feeds 設定;下一輪 catch-up 會重試",
            )
        fail(
            logger,
            f"頻道 {channel['slug']} 沒有可用新聞(抓取失敗 {failed}、解析為空 {empty})",
            "來源有回應但沒有項目 — 檢查 RSS/Atom 格式或 feed 是否為空;下一輪 catch-up 會重試",
        )
    merged = merge_items(batches, LIMIT)
    if not merged:
        fail(
            logger,
            f"頻道 {channel['slug']} 抓到來源但合併去重後沒有任何項目",
            "可能各來源內容完全重複,或 RSS 格式不如預期;檢查 logs/fetch_news.log",
        )
    return merged


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

    items = gather_items(channel)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "channel": channel["slug"],
        "keyword": channel["keyword"],
        "feeds": [f["name"] for f in feed_defs(channel)],
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
