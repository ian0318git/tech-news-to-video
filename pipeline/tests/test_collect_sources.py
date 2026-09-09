"""collect_sources 單元測試(filter_suggested 純函式 + check_url 護欄,mock httpx 不連網)。"""

import logging
import time

import httpx
import pytest

from collect_sources import check_url, filter_suggested

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


def test_check_url_reachable(monkeypatch):
    monkeypatch.setattr(
        httpx, "get", lambda url, **kw: httpx.Response(200, text="ok")
    )
    assert check_url("https://good.example/doc") == (True, 200)


def test_check_url_http_status_is_failure(monkeypatch):
    monkeypatch.setattr(
        httpx, "get", lambda url, **kw: httpx.Response(503, text="down")
    )
    assert check_url("https://down.example/") == (False, 503)


def test_check_url_connect_error_degrades(monkeypatch):
    """DNS/連線失敗 → 該 URL 視同不可達,不中斷其他來源檢查。"""

    def boom(url, **kwargs):
        raise httpx.ConnectError("Temporary failure in name resolution")

    monkeypatch.setattr(httpx, "get", boom)
    assert check_url("https://stall.example/") == (False, 0)


def test_check_url_deadline_degrades_fast(monkeypatch):
    """DNS 卡死(worker 永久阻塞)時,護欄應在 deadline 內降級 (False, 0),
    而不是等 worker 結束 — 否則 N 個來源會無限拖住 collect_sources。"""

    def slow_get(url, **kwargs):
        time.sleep(2.0)  # 模擬 getaddrinfo 卡死
        raise AssertionError("護欄失效:worker 竟然跑完了")

    monkeypatch.setattr(httpx, "get", slow_get)
    start = time.monotonic()
    assert check_url("https://stall.example/", deadline=0.05) == (False, 0)
    assert time.monotonic() - start < 1.0  # 應快速降級,而非等滿 2 秒
