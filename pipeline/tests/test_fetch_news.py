"""fetch_news 的單元測試(mock httpx,不連網):parse_items 解析 + fetch_rss 護欄。"""

import time

import httpx
import pytest

import fetch_news

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Test embedded linux news</title>
    <link>https://news.google.com/rss/articles/CBMiREDIRECT</link>
    <pubDate>Wed, 06 Aug 2026 07:00:00 GMT</pubDate>
    <source url="https://example.com">Example News</source>
    <description>&lt;a href="https://news.google.com/rss/articles/CBMiREDIRECT" target="_blank"&gt;Test embedded linux news&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;Example News&lt;/font&gt;&lt;br&gt;A summary &lt;b&gt;with tags&lt;/b&gt; inside.</description>
  </item>
  <item>
    <title>Second item without source</title>
    <link>https://news.google.com/rss/articles/CBMiSECOND</link>
    <pubDate>Wed, 06 Aug 2026 08:00:00 GMT</pubDate>
    <description>Plain description.</description>
  </item>
</channel></rss>"""


def test_parse_items_basic():
    items = fetch_news.parse_items(SAMPLE_RSS)
    assert len(items) == 2
    first = items[0]
    assert first["title"] == "Test embedded linux news"
    assert first["url"].startswith("https://news.google.com/rss/articles/")
    assert first["source"] == "Example News"
    assert first["published"] == "Wed, 06 Aug 2026 07:00:00 GMT"
    # summary 應剝掉 HTML 標籤
    assert "with tags" in first["summary"]
    assert "<b>" not in first["summary"]


def test_parse_items_missing_source():
    second = fetch_news.parse_items(SAMPLE_RSS)[1]
    assert second["source"] == ""
    assert second["summary"] == "Plain description."


def test_parse_items_empty():
    assert fetch_news.parse_items("<rss><channel></channel></rss>") == []


def test_parse_items_bad_xml_raises():
    with pytest.raises(SystemExit):
        fetch_news.parse_items("<rss><channel><item></rss>")  # 未關閉標籤


def test_parse_items_respects_limit(monkeypatch):
    monkeypatch.setattr(fetch_news, "LIMIT", 1)
    items = fetch_news.parse_items(SAMPLE_RSS)
    assert len(items) == 1


CH = {"slug": "embedded", "keyword": "embedded linux"}


def test_fetch_rss_ok(monkeypatch):
    seen: dict = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["timeout"] = kwargs.get("timeout")
        return httpx.Response(200, text="<rss/>")

    monkeypatch.setattr(fetch_news.httpx, "get", fake_get)
    out = fetch_news.fetch_rss(CH)
    assert out == "<rss/>"
    assert "embedded%20linux" in seen["url"]
    assert seen["timeout"] == fetch_news.REQUEST_TIMEOUT


def test_fetch_rss_non_200_fails(monkeypatch):
    monkeypatch.setattr(
        fetch_news.httpx, "get", lambda url, **kw: httpx.Response(503, text="nope")
    )
    with pytest.raises(SystemExit):
        fetch_news.fetch_rss(CH)


def test_fetch_rss_reraises_http_error(monkeypatch):
    """httpx 例外(含 DNS ConnectError)應原樣送回主執行緒,不吞錯。"""

    def boom(url, **kwargs):
        raise httpx.ConnectError("Temporary failure in name resolution")

    monkeypatch.setattr(fetch_news.httpx, "get", boom)
    with pytest.raises(httpx.ConnectError):
        fetch_news.fetch_rss(CH)


def test_fetch_rss_deadline_fast_fails(monkeypatch):
    """DNS 卡死(worker 永久阻塞)時,護欄應在 FETCH_DEADLINE 內快速失敗,
    而不是等 worker 結束(2026-09-08 空轉整天教訓的防護)。"""

    def slow_get(url, **kwargs):
        time.sleep(2.0)  # 模擬 getaddrinfo 卡死
        raise AssertionError("護欄失效:worker 竟然跑完了")

    monkeypatch.setattr(fetch_news.httpx, "get", slow_get)
    monkeypatch.setattr(fetch_news, "FETCH_DEADLINE", 0.05)
    start = time.monotonic()
    with pytest.raises(SystemExit):
        fetch_news.fetch_rss(CH)
    assert time.monotonic() - start < 1.0  # 應快速失敗,而非等滿 2 秒
