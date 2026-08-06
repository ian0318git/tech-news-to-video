"""fetch_news.parse_items 的 RSS 解析單元測試(mock XML,不連網)。"""

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
    import pytest

    with pytest.raises(SystemExit):
        fetch_news.parse_items("<rss><channel><item></rss>")  # 未關閉標籤


def test_parse_items_respects_limit(monkeypatch):
    monkeypatch.setattr(fetch_news, "LIMIT", 1)
    items = fetch_news.parse_items(SAMPLE_RSS)
    assert len(items) == 1
