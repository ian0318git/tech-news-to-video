"""fetch_news 的單元測試(mock httpx,不連網):parse_items 解析 + fetch_rss 護欄。"""

import time
import urllib.parse

import fetch_news
import httpx
import pytest

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


def test_fetch_rss_appends_freshness(monkeypatch):
    """freshness 要變成 Google News 的 `when:<期間>` 條件。

    2026-10-20 根因:沒有日期條件時,Google News 以相關性排序回傳,前 20 則
    可能是數月前同一批舊文 → 90 天去重全部封鎖 → embedded 頻道停產。
    """
    seen: dict = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return httpx.Response(200, text="<rss/>")

    monkeypatch.setattr(httpx, "get", fake_get)
    fetch_news.fetch_rss({**CH, "freshness": "14d"})
    assert "embedded linux when:14d" in urllib.parse.unquote(seen["url"])


def test_fetch_rss_keeps_existing_when_operator(monkeypatch):
    """關鍵字已自帶 when: 時不可重複附加(會變成 when:7d when:14d)。"""
    seen: dict = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return httpx.Response(200, text="<rss/>")

    monkeypatch.setattr(httpx, "get", fake_get)
    fetch_news.fetch_rss(
        {**CH, "keyword": "embedded linux when:7d", "freshness": "14d"}
    )
    decoded = urllib.parse.unquote(seen["url"])
    assert decoded.count("when:") == 1
    assert "when:7d" in decoded


def test_fetch_rss_rejects_invalid_freshness():
    """格式錯誤要立刻失敗,不可把垃圾送進 Google News 後才空手而回。"""
    with pytest.raises(SystemExit):
        fetch_news.search_keyword({**CH, "freshness": "two weeks"})


def test_fetch_rss_ok(monkeypatch):
    seen: dict = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["timeout"] = kwargs.get("timeout")
        return httpx.Response(200, text="<rss/>")

    monkeypatch.setattr(httpx, "get", fake_get)
    out = fetch_news.fetch_rss(CH)
    assert out == "<rss/>"
    assert "embedded%20linux" in seen["url"]
    assert seen["timeout"] == fetch_news.REQUEST_TIMEOUT


def test_fetch_rss_non_200_fails(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(503, text="nope"))
    with pytest.raises(SystemExit):
        fetch_news.fetch_rss(CH)


def test_fetch_rss_reraises_http_error(monkeypatch):
    """httpx 例外(含 DNS ConnectError)應原樣送回主執行緒,不吞錯。"""

    def boom(url, **kwargs):
        raise httpx.ConnectError("Temporary failure in name resolution")

    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(httpx.ConnectError):
        fetch_news.fetch_rss(CH)


def test_fetch_rss_deadline_fast_fails(monkeypatch):
    """DNS 卡死(worker 永久阻塞)時,護欄應在 FETCH_DEADLINE 內快速失敗,
    而不是等 worker 結束(2026-09-08 空轉整天教訓的防護)。"""

    def slow_get(url, **kwargs):
        time.sleep(2.0)  # 模擬 getaddrinfo 卡死
        raise AssertionError("護欄失效:worker 竟然跑完了")

    monkeypatch.setattr(httpx, "get", slow_get)
    monkeypatch.setattr(fetch_news, "FETCH_DEADLINE", 0.05)
    start = time.monotonic()
    with pytest.raises(SystemExit):
        fetch_news.fetch_rss(CH)
    assert time.monotonic() - start < 1.0  # 應快速失敗,而非等滿 2 秒
