"""backfill_history 影片描述解析測試(不連網)。

關鍵行為: 描述第 1 行是 `<文章標題><來源>`,分隔用的是**不斷行空格** `\\xa0\\xa0`
而非普通空格。用 `"  "` 去切會切不開、把來源名留在標題裡,`title_key` 就永遠
對不上 feed 的標題 —— 2026-09-16 實際發生過,補進去的歷史條目全帶著來源名,
去重因此靜默失效。這支測試就是那個 bug 的回歸鎖。
"""

import pytest
from backfill_history import parse_video
from rank_news import title_key

NBSP = "\xa0"
URL = (
    "https://news.google.com/rss/articles/CBMiWkFVX3lxTE5Yb0"
    "?oc=5&hl=en-US&gl=US&ceid=US:en"
)


def _video(
    vid: str = "vid0000001",
    title: str = "Embedded Linux Daily - 2026-08-06 Flattened Image Tree 1.0",
    published: str = "2026-08-06T22:30:00Z",
    desc: str = "",
) -> dict:
    return {"vid": vid, "title": title, "published": published, "desc": desc}


def _desc(article_title: str, source: str, url: str = URL, sep: str = NBSP * 2) -> str:
    """組出與正式 pipeline 相同格式的影片描述。"""
    return (
        f"{article_title}{sep}{source}\n"
        f"來源: {source}  {url}\n"
        f"\n由 tech-news-to-video pipeline 自動生成"
    )


# ---------- 標題 / 來源分離 ----------


def test_nbsp_separator_strips_source_from_title():
    """回歸鎖:分隔是不斷行空格,標題裡不可以殘留來源名。"""
    rec = parse_video(_video(desc=_desc("Flattened Image Tree 1.0 Released", "Phoronix")))

    assert rec["title"] == "Flattened Image Tree 1.0 Released"
    assert "Phoronix" not in rec["title"]


def test_ascii_double_space_separator_also_works():
    """普通雙空格的描述(來源名可能是多字)一樣要切得開。"""
    rec = parse_video(
        _video(desc=_desc("Secure Boot on Linux Platforms", "Embedded Computing Design", sep="  "))
    )

    assert rec["title"] == "Secure Boot on Linux Platforms"


def test_title_keeps_internal_single_spaces():
    """標題內的單一空白不是分隔 — 不能被切掉。"""
    rec = parse_video(_video(desc=_desc("Fjall 3.0: Rust Storage Engine", "LWN.net")))

    assert rec["title"] == "Fjall 3.0: Rust Storage Engine"


def test_parsed_title_matches_feed_title_key():
    """補進 history 的標題,必須和新闻池的標題產生同一個 title_key。

    這是整個 backfill 的目的:key 對不上就等於沒補。
    """
    feed_title = "Flattened Image Tree 1.0 Released"
    rec = parse_video(_video(desc=_desc(feed_title, "Phoronix")))

    assert title_key(rec["title"]) == title_key(feed_title)


# ---------- 節目歸屬 ----------


@pytest.mark.parametrize(
    ("prefix", "slug"),
    [
        ("Embedded Linux Daily", "embedded"),
        ("TechSnack Daily", "tech"),
        ("Tech & AI Daily", "tech"),  # tech 的舊前綴
    ],
)
def test_slug_from_title_prefix(prefix, slug):
    rec = parse_video(_video(title=f"{prefix} - 2026-08-06 Something", desc=_desc("A", "B")))

    assert rec["slug"] == slug


@pytest.mark.parametrize(
    ("vid", "slug"),
    [("kFttyvxMZFw", "embedded"), ("2oN6Z-4Oi2U", "tech")],
)
def test_slug_fallback_by_video_id(vid, slug):
    """2026-08-06 的兩支首次上傳實測沒有正式前綴,靠影片 ID 歸屬。"""
    rec = parse_video(_video(vid=vid, title="Some Article - 2026-08-06", desc=_desc("A", "B")))

    assert rec["slug"] == slug


def test_unknown_origin_returns_none():
    """既無已知前綴也非指定影片 ID → 不歸屬,寧可略過也不塞錯節目。"""
    rec = parse_video(_video(vid="unknown", title="Random Video - Whatever", desc=_desc("A", "B")))

    assert rec is None


def test_missing_source_url_returns_none():
    """沒有來源 URL 就沒有可靠的去重鍵 → 略過。"""
    rec = parse_video(_video(desc="Embedded Linux Daily\n沒有來源網址的描述"))

    assert rec is None


# ---------- 日期時區 ----------


def test_published_utc_converted_to_sydney_date():
    """08:00 AEST 上傳的影片,UTC 日期會落在前一天 — 必須換算。"""
    rec = parse_video(_video(published="2026-08-06T22:30:00Z", desc=_desc("A", "B")))

    assert rec["date"] == "2026-08-07"


def test_sydney_date_unchanged_when_utc_already_same_day():
    rec = parse_video(_video(published="2026-08-06T00:30:00Z", desc=_desc("A", "B")))

    assert rec["date"] == "2026-08-06"


# ---------- URL ----------


def test_source_url_captured_verbatim():
    rec = parse_video(_video(desc=_desc("A", "B")))

    assert rec["url"] == URL
