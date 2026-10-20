"""fetch_news 的單元測試(mock httpx,不連網):解析、多來源合併與護欄。"""

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
    # 沒設 freshness 的頻道(如 tech)不可被硬加日期條件
    assert "when:" not in urllib.parse.unquote(seen["url"])
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


# --- 直接 publisher RSS / Atom(2026-10-21)-----------------------------------

ATOM_FEED = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Atom story</title>
    <link rel="alternate" href="https://example.com/atom-story"/>
    <published>2026-10-20T08:00:00Z</published>
    <summary type="html">&lt;p&gt;Atom &lt;b&gt;summary&lt;/b&gt;&lt;/p&gt;</summary>
  </entry>
</feed>"""


def test_parse_feed_atom():
    """Atom 的 link 是屬性、summary 可能是 HTML — 都要正規化成既有 schema。"""
    item = fetch_news.parse_feed(ATOM_FEED, "Example")[0]
    assert item["title"] == "Atom story"
    assert item["url"] == "https://example.com/atom-story"
    assert item["source"] == "Example"
    assert item["summary"] == "Atom summary"
    assert item["published"] == "2026-10-20T08:00:00Z"


def test_parse_feed_uses_config_source_name():
    """直接來源沒有 <source> 時,來源名取自 config 的 name。"""
    items = fetch_news.parse_feed(SAMPLE_RSS, "Example Feed")
    assert items[0]["source"] == "Example News"  # RSS 自帶的 <source> 優先
    assert items[1]["source"] == "Example Feed"  # 沒有 <source> 則用設定值


def test_parse_feed_bad_xml_is_tolerated():
    """單一 publisher 的 XML 壞掉只跳過該來源,不讓整個頻道失敗。"""
    assert fetch_news.parse_feed("<rss><item>", "Broken") == []


def test_item_url_key_ignores_www_path_slash_and_tracking():
    a = fetch_news.item_url_key("https://www.example.com/story/?utm_source=x&fbclid=y")
    b = fetch_news.item_url_key("http://example.com/story")
    assert a == b


def test_item_title_key_strips_google_news_source_suffix():
    """長標題才剝尾綴;短標題由下一支測試釘住「不得剝」。"""
    with_suffix = "A Long Enough Headline For Stripping - Phoronix"
    without = "A Long Enough Headline For Stripping"
    assert fetch_news.item_title_key(with_suffix) == fetch_news.item_title_key(without)


def _item(title, url):
    return {"title": title, "url": url, "source": "s", "summary": "", "published": ""}


def test_merge_items_round_robin_and_dedup():
    """輪流取樣讓每個來源都有代表;同 URL 的另一來源版本不重複輸出。"""
    first = [_item("A1", "https://a.example/1"), _item("A2", "https://a.example/2")]
    second = [_item("B1", "https://b.example/1"), _item("A1", "https://a.example/1/")]
    merged = fetch_news.merge_items([first, second], 10)
    assert [m["title"] for m in merged] == ["A1", "B1", "A2"]


def test_merge_items_respects_limit_and_skips_duplicate_titles():
    first = [_item("Same", "https://a.example/1"), _item("A2", "https://a.example/2")]
    second = [_item("Same", "https://b.example/1")]
    merged = fetch_news.merge_items([first, second], 2)
    # 釘住「重複標題只留一則」,而不是只看長度(長度在去重失效時也會是 2)
    assert [m["title"] for m in merged] == ["Same", "A2"]


def test_title_key_keeps_short_titles_with_suffix_distinct():
    """短標題不可被尾綴剝離誤殺(Tech Life / Tech Now 是不同節目集數)。"""
    assert fetch_news.item_title_key("Tech Life - BBC") != fetch_news.item_title_key(
        "Tech Now - BBC"
    )


def test_title_key_still_merges_long_titles_with_source_suffix():
    a = fetch_news.item_title_key(
        "A Very Long Headline About Something Big - Phoronix"
    )
    b = fetch_news.item_title_key("A Very Long Headline About Something Big")
    assert a == b


def test_item_url_key_tolerates_malformed_url():
    """畸形 URL 不可讓整個頻道 crash(reviewer 實測 http://[ 會拋 ValueError)。"""
    assert fetch_news.item_url_key("http://[")  # 只要求不拋例外


def test_merge_items_tolerates_malformed_url():
    merged = fetch_news.merge_items(
        [[_item("X", "http://[")], [_item("Y", "https://ok.example/1")]], 2
    )
    assert [m["title"] for m in merged] == ["X", "Y"]


def test_fetch_source_tolerates_invalid_url(monkeypatch):
    def boom(*args, **kwargs):
        raise httpx.InvalidURL("bad port")

    monkeypatch.setattr(fetch_news._http, "bounded_get", boom)
    assert fetch_news.fetch_source("https://x.example:bad/", "X", strict=False) is None
    with pytest.raises(httpx.InvalidURL):
        fetch_news.fetch_source("https://x.example:bad/", "X", strict=True)


def test_feed_defs_rejects_malformed_config():
    for feeds in ("not-a-list", [{"name": "X"}], [{"url": "https://x"}], ["x"]):
        with pytest.raises(SystemExit):
            fetch_news.feed_defs({"slug": "s", "feeds": feeds})


def test_feed_defs_rejects_non_http_url():
    for url in ("ftp://x.example/rss", "not a url", "http://["):
        with pytest.raises(SystemExit):
            fetch_news.feed_defs(
                {"slug": "s", "feeds": [{"name": "X", "url": url}]}
            )


def test_feed_defs_allows_missing_feeds():
    assert fetch_news.feed_defs({"slug": "s"}) == []


def test_shipped_config_feeds_are_valid():
    """實際 config 的 feeds 必須通過驗證、走 https、且沒有重複 URL。"""
    import json
    from pathlib import Path

    config = Path(__file__).resolve().parents[1] / "config" / "channels.json"
    channels = json.loads(config.read_text(encoding="utf-8"))["channels"]
    for channel in channels:
        feeds = fetch_news.feed_defs(channel)
        assert feeds, f"{channel['slug']} 沒有任何直接來源"
        urls = [f["url"] for f in feeds]
        assert len(urls) == len(set(urls)), f"{channel['slug']} 有重複來源 URL"
        assert all(u.startswith("https://") for u in urls)


def _rss(*entries):
    """產生只含最小欄位的 RSS 2.0,供多來源測試使用。"""
    body = "".join(
        f"<item><title>{title}</title><link>{url}</link>"
        f"<description>{title} summary</description></item>"
        for title, url in entries
    )
    return f"<?xml version='1.0'?><rss version='2.0'><channel>{body}</channel></rss>"


def _fake_get(responses, default_text):
    """依 URL 子字串回傳指定回應或拋錯;未命中用 default_text。"""

    def fake(url, **kwargs):
        for marker, result in responses.items():
            if marker in url:
                if isinstance(result, Exception):
                    raise result
                return result
        return httpx.Response(200, text=default_text)

    return fake


def test_gather_items_merges_feeds_and_google(monkeypatch):
    channel = {
        "slug": "embedded",
        "keyword": "embedded linux",
        "feeds": [
            {"name": "Phoronix", "url": "https://phoronix.example/rss"},
            {"name": "LWN", "url": "https://lwn.example/rss"},
        ],
    }
    responses = {
        "phoronix.example": httpx.Response(
            200, text=_rss(("P1", "https://p.example/1"), ("P2", "https://p.example/2"))
        ),
        "lwn.example": httpx.Response(
            200, text=_rss(("L1", "https://l.example/1"), ("L2", "https://l.example/2"))
        ),
        "news.google.com": httpx.Response(
            200, text=_rss(("G1", "https://g.example/1"), ("G2", "https://g.example/2"))
        ),
    }
    monkeypatch.setattr(
        fetch_news._http, "bounded_get", _fake_get(responses, _rss())
    )
    items = fetch_news.gather_items(channel)
    # 三個來源各兩則 → 輪流輸出,共 6 則,每個來源都有代表
    assert [i["title"] for i in items] == ["P1", "L1", "G1", "P2", "L2", "G2"]
    assert [i["source"] for i in items[:3]] == ["Phoronix", "LWN", ""]


def test_gather_items_tolerates_dead_and_bad_sources(monkeypatch):
    """單一來源連線失敗 / HTTP 500 / XML 壞掉,其餘來源照常合併。"""
    channel = {
        "slug": "embedded",
        "keyword": "embedded linux",
        "feeds": [
            {"name": "Dead", "url": "https://dead.example/rss"},
            {"name": "Bad", "url": "https://bad.example/rss"},
            {"name": "Broken", "url": "https://broken.example/rss"},
            {"name": "Good", "url": "https://good.example/rss"},
        ],
    }
    responses = {
        "dead.example": httpx.ConnectError("boom"),
        "bad.example": httpx.Response(500, text="nope"),
        "broken.example": httpx.Response(200, text="<rss><item>"),
        "good.example": httpx.Response(200, text=_rss(("OK1", "https://ok.example/1"))),
        "news.google.com": httpx.Response(
            200, text=_rss(("G1", "https://g.example/1"))
        ),
    }
    monkeypatch.setattr(
        fetch_news._http, "bounded_get", _fake_get(responses, _rss())
    )
    items = fetch_news.gather_items(channel)
    assert [i["title"] for i in items] == ["OK1", "G1"]
    assert all(i["source"] != "Dead" for i in items)


def test_gather_items_fails_when_all_sources_fail(monkeypatch):
    channel = {"slug": "embedded", "keyword": "embedded linux"}
    monkeypatch.setattr(
        fetch_news._http, "bounded_get", lambda *a, **k: httpx.Response(503, text="x")
    )
    with pytest.raises(SystemExit):
        fetch_news.gather_items(channel)


def test_gather_items_respects_news_limit(monkeypatch):
    channel = {"slug": "embedded", "keyword": "embedded linux"}
    monkeypatch.setattr(fetch_news, "LIMIT", 1)
    monkeypatch.setattr(
        fetch_news._http, "bounded_get", _fake_get({}, SAMPLE_RSS)
    )
    assert len(fetch_news.gather_items(channel)) == 1
