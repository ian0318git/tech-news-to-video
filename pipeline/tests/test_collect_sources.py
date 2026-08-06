"""collect_sources.filter_suggested 的來源過濾單元測試。"""

import logging

from collect_sources import filter_suggested


pytest_logger = logging.getLogger("test")  # 簡潔起見用模組層 logger


def test_news_url_included():
    news = {"url": "https://example.com/article", "title": "Article"}
    out = filter_suggested([], news, pytest_logger)
    assert len(out) == 1
    assert out[0]["category"] == "news"
    assert out[0]["url"] == "https://example.com/article"


def test_google_redirect_news_skipped():
    news = {"url": "https://news.google.com/rss/articles/CBMiXXX", "title": "Article"}
    out = filter_suggested([], news, pytest_logger)
    assert out == []  # 轉址不當來源


def test_duplicates_and_non_http_removed():
    news = {"url": "https://a.example/x", "title": "A"}
    suggested = [
        {"url": "https://a.example/x", "title": "dup"},  # 與原文重複
        {"url": "not-a-url", "title": "bad"},  # 非 http
        {"url": "https://b.example/y", "title": "B"},  # 合法
        {"url": "https://b.example/y", "title": "dup2"},  # 互相重複
    ]
    out = filter_suggested(suggested, news, pytest_logger)
    urls = [s["url"] for s in out]
    assert urls == ["https://a.example/x", "https://b.example/y"]


def test_google_redirect_suggestions_removed():
    news = {"url": "https://a.example/x", "title": "A"}
    suggested = [
        {"url": "https://news.google.com/rss/articles/CBMiZZZ", "title": "redirect"},
        {"url": "https://good.example/doc", "title": "good"},
    ]
    out = filter_suggested(suggested, news, pytest_logger)
    assert len(out) == 2
    assert all("news.google.com" not in s["url"] for s in out)
