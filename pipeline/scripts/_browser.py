"""瀏覽器 adapter: Google News 轉址的 JS 重導解析。

依賴規則: 只匯入兄弟模組;不得匯入 _common facade。
重依賴(playwright / chromium)集中在此 — collect_sources 不需要知道瀏覽器存在。
任何失敗都優雅降級(回傳 None,呼叫端略過原文來源)。
"""


def resolve_article_url(url: str, logger=None) -> str | None:
    """跟隨 Google News 轉址,回傳最終的真實文章網址;失敗回傳 None。

    Google News 轉址頁用 JS 重導到原文,HTTP 層解析不到,需要瀏覽器渲染。
    """
    if "news.google.com" not in url:
        return url  # 非轉址直接放行,不需瀏覽器
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
            logger.warning(
                f"[WARN] 轉址解析例外: {type(exc).__name__}: {str(exc)[:200]}"
            )
    return None
