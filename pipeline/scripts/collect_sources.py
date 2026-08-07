#!/usr/bin/env python3
"""Step 3 — Source Collector(per-channel)。

用法: python scripts/collect_sources.py [--channel <slug>]
讀取 output/<slug>/top1.json,收集官方/主要來源並驗證可達性,
寫出 output/<slug>/sources.json。需要 .env 的 GEMINI_API_KEY。
"""

import json
import os
import sys

import httpx
from _common import (
    channel_dir,
    fail,
    flag_value,
    gemini_json,
    load_env,
    resolve_channel,
    save_json,
    setup_logging,
)

logger = setup_logging("collect_sources")

DEFAULT_MODEL = "gemini-2.5-flash"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

COLLECT_PROMPT_TEMPLATE = """Suggest 3-5 authoritative primary sources for researching this news topic:
official documentation, GitHub repositories, vendor pages, or standards bodies.
Prefer URLs likely to still be valid.
Return JSON only, with this exact shape:
{{
  "sources": [
    {{"url": "https://...", "title": "...", "category": "official-docs|github|vendor|news|other", "why": "short note"}}
  ]
}}

News topic:
{news_json}
"""


def check_url(url: str) -> tuple[bool, int]:
    try:
        resp = httpx.get(url, headers={"User-Agent": UA}, timeout=15.0, follow_redirects=True)
        return resp.status_code < 400, resp.status_code
    except httpx.HTTPError:
        return False, 0


def resolve_article_url(url: str, logger=None) -> str | None:
    """跟隨 Google News 轉址,回傳最終的真實文章網址;失敗回傳 None。

    Google News 轉址頁用 JS 重導到原文,HTTP 層解析不到,需要瀏覽器渲染。
    用 chromium(playwright)處理;任何失敗都優雅降級(略過原文來源)。
    """
    if "news.google.com" not in url:
        return url
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)  # 等 JS 重導完成
                final = page.url
            finally:
                browser.close()
        if final and "news.google.com" not in final:
            return final
    except Exception as exc:  # noqa: BLE001 — 解析失敗不阻斷 pipeline
        if logger:
            logger.warning(f"[WARN] 轉址解析例外: {type(exc).__name__}: {str(exc)[:200]}")
    return None


def filter_suggested(suggested: list, news: dict, logger) -> list[dict]:
    """過濾來源清單(純函式,可測試): 排除非 http、重複、Google News 轉址。

    回傳順序: 原文新聞(若可用)在前,Gemini 建議隨後。
    """
    sources: list[dict] = []
    seen: set[str] = set()

    news_url = str(news.get("url", "")).strip()
    if news_url.startswith("http") and "news.google.com" not in news_url:
        sources.append(
            {
                "url": news_url,
                "title": news.get("title", "news article"),
                "category": "news",
                "why": "original article",
            }
        )
        seen.add(news_url)
    else:
        logger.warning("[WARN] 原文 URL 是 Google News 轉址或無效,略過原文來源")

    for s in suggested:
        url = str(s.get("url", "")).strip()
        if not url.startswith("http") or url in seen:
            continue
        if "news.google.com" in url:
            logger.warning(f"[WARN] 排除 Google News 轉址: {url[:80]}...")
            continue
        seen.add(url)
        sources.append(
            {
                "url": url,
                "title": str(s.get("title", "")).strip() or url,
                "category": str(s.get("category", "other")).strip(),
                "why": str(s.get("why", "")).strip(),
            }
        )
    return sources


def main() -> None:
    load_env()
    args = sys.argv[1:]
    slug = flag_value(args, "--channel")
    channel = resolve_channel(slug, logger)
    cdir = channel_dir(channel)

    top1_path = cdir / "top1.json"
    if not top1_path.exists():
        fail(
            logger,
            f"找不到 {top1_path} — 請先執行 scripts/rank_news.py --channel {channel['slug']}",
        )
    top1 = json.loads(top1_path.read_text(encoding="utf-8"))
    news = top1.get("news", top1)

    # 原文 URL 若為 Google News 轉址,先解析成真實文章網址(轉址會擋 NotebookLM 抓取)
    if "news.google.com" in str(news.get("url", "")):
        logger.info("[INFO] 解析原文真實網址(瀏覽器跟隨轉址)...")
        real = resolve_article_url(str(news["url"]), logger)
        if real:
            logger.info(f"[OK] 原文真實網址: {real}")
            news = {**news, "url": real}
        else:
            logger.warning("[WARN] 轉址解析失敗,原文來源將被略過")

    prompt = COLLECT_PROMPT_TEMPLATE.format(
        news_json=json.dumps(
            {"title": news.get("title"), "url": news.get("url"), "summary": news.get("summary")},
            ensure_ascii=False,
        )
    )
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)  # load_env 之後才讀
    result = gemini_json(prompt, logger, model=model)

    suggested = result.get("sources")
    if not isinstance(suggested, list):
        fail(logger, "Gemini 回傳缺少 sources 欄位", json.dumps(result, ensure_ascii=False)[:2000])

    sources = filter_suggested(suggested, news, logger)

    ok_sources = []
    for s in sources:
        reachable, status = check_url(s["url"])
        s["reachable"] = reachable
        s["http_status"] = status
        if reachable:
            ok_sources.append(s)
        else:
            logger.warning(f"[WARN] 無法存取 {s['url']}(HTTP {status or '連線失敗'}),已排除")

    if not ok_sources:
        fail(logger, "所有建議來源都無法存取")

    save_json(
        {"topic": news.get("title"), "sources": ok_sources, "total_suggested": len(sources)},
        cdir / "sources.json",
    )
    for s in ok_sources:
        logger.info(f"  [{s['category']}] {s['title']}  {s['url']}  (HTTP {s['http_status']})")
    logger.info(f"[PASS] 來源已寫入 {cdir / 'sources.json'}({len(ok_sources)} 個可達來源)")


if __name__ == "__main__":
    main()
