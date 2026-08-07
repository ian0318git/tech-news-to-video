"""瀏覽器 adapter 測試: 非轉址 URL 不需瀏覽器直接放行。"""

from _browser import resolve_article_url


def test_non_google_url_passthrough():
    assert (
        resolve_article_url("https://example.com/article")
        == "https://example.com/article"
    )


def test_google_redirect_without_browser_returns_none_or_url():
    # 不啟動瀏覽器的情況下,轉址 URL 不應回傳非 Google 網址(無瀏覽器 → None 或原樣失敗)
    result = resolve_article_url("https://news.google.com/rss/articles/CBMiXXX")
    assert result is None or "news.google.com" in result
