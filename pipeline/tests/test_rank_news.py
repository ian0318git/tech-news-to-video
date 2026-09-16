"""rank_news 的主題去重邏輯測試(HISTORY_DAYS 天內不重複選題)。"""

import logging
from datetime import date, timedelta

import pytest
from rank_news import HISTORY_DAYS, pick_topic, title_key, topic_keys, url_key

TODAY = "2026-08-10"

ITEMS = [
    {
        "index": 0,
        "title": "Flattened Image Tree 1.0 Specification Released",
        "url": "https://a",
    },
    {
        "index": 1,
        "title": "RISC-V SoC Vendor Opens Up Documentation",
        "url": "https://b",
    },
    {
        "index": 2,
        "title": "Kernel 6.15 Released With New Scheduler",
        "url": "https://c",
    },
    {"index": 3, "title": "Yocto 5.3 Adds Better SBOM Support", "url": "https://d"},
]

RANKING = [
    {"index": 0, "title": ITEMS[0]["title"], "score": 9.1},
    {"index": 1, "title": ITEMS[1]["title"], "score": 8.7},
    {"index": 2, "title": ITEMS[2]["title"], "score": 8.2},
    {"index": 3, "title": ITEMS[3]["title"], "score": 7.9},
]


def test_title_key_normalizes():
    assert title_key("Flattened Image Tree 1.0 — Spec!") == "flattenedimagetree10spec"
    assert title_key("  Hello, World!! ") == "helloworld"


def test_title_key_source_suffix_variant():
    """來源名寫法不同(Phoronix vs phoronix.com)仍視為同一主題。"""
    a = title_key("Flattened Image Tree 1.0 Specification - Phoronix")
    b = title_key("Flattened Image Tree 1.0 Specification - phoronix.com")
    assert a == b == "flattenedimagetree10specification"


def test_title_key_multi_word_source_name():
    """多字來源名也要剝掉 — 舊版只剝單一 token,這裡會分岔。

    2026-09-16 實證:同一篇文章來源名一次寫「Embedded Computing Design」、
    一次寫「embeddedcomputing.com」,兩者 key 不同 → 去重靜默失效,
    topic_history.json 裡相隔 6 天並存兩筆。
    """
    a = title_key(
        "How to Enable Secure Boot on Linux-Based Platforms"
        " - Embedded Computing Design"
    )
    b = title_key(
        "How to Enable Secure Boot on Linux-Based Platforms - embeddedcomputing.com"
    )
    assert a == b == "howtoenablesecurebootonlinuxbasedplatforms"


def test_title_key_keeps_hyphenated_words():
    """連字號(無空白)不是來源名分隔符,不可剝離。"""
    assert title_key("Linux-Based") == "linuxbased"
    assert title_key("OpenAI Tests GPT-6 Astra") == "openaitestsgpt6astra"


def test_title_key_short_title_not_overstripped():
    """剝完過短 → 保留原標題,避免短主題互相誤撞。"""
    assert title_key("Foo - Bar") == "foobar"
    assert title_key("AI - Reuters") == "aireuters"


def test_title_key_keeps_part_number():
    """系列文章(Part 1 / Part 2)剝掉來源名後仍須可區分。"""
    a = title_key("Intro to Embedded Linux Part 1: Defining Android - Linux Foundation")
    b = title_key("Intro to Embedded Linux Part 2: Building a Rootfs - Linux Foundation")
    assert a != b
    assert "part1" in a
    assert "part2" in b


# --- URL 去重鍵 ------------------------------------------------------------

# 真實的 Google News 轉址 URL(2026-09-16 由 YouTube 影片描述取回,
# 同一篇文章相隔 26 天的兩次抓取逐字元相同)
FIT_URL = (
    "https://news.google.com/rss/articles/"
    "CBMiXkFVX3lxTE9tY3JNcTVScXlJX21yNnlZM0hVcEk2d3JYZTl2MzFpNEtMajhINWFlZ2ZfS2hT"
    "X2RwY0xfVUh0Y19MSTRLaUx0QUdlNEhDMWVkNW1kT3FaVTZ5VkE3UFE?oc=5"
)


def test_url_key_stable_across_tracking_and_scheme():
    """追蹤參數 / scheme / www / fragment 不影響同一篇文章的鍵。"""
    base = url_key(FIT_URL)
    assert base == url_key(FIT_URL.replace("?oc=5", ""))
    assert base == url_key(FIT_URL.replace("?oc=5", "?oc=5&hl=en-AU"))
    assert url_key("https://www.example.com/a?utm_source=x") == url_key(
        "http://example.com/a"
    )
    assert url_key("https://example.com/a#section") == url_key("https://example.com/a")


def test_url_key_keeps_meaningful_query():
    """query 可能承載文章身分 — 不可整段丟棄。"""
    assert url_key("https://example.com/post?id=1") != url_key(
        "https://example.com/post?id=2"
    )


def test_url_key_empty():
    assert url_key("") == ""
    assert url_key("   ") == ""


def test_url_key_different_articles_differ():
    assert url_key("https://example.com/news/foo") != url_key(
        "https://example.com/news/bar"
    )


def test_topic_keys_unions_url_and_title():
    """URL 與標題兩種鍵都算,任一相符即可封鎖。"""
    keys = topic_keys("Some Headline - Source", FIT_URL)
    assert title_key("Some Headline - Source") in keys
    assert url_key(FIT_URL) in keys


def test_topic_keys_empty_inputs_add_nothing():
    """空輸入不可產生空字串鍵(否則會變成萬用鍵,封鎖一切)。"""
    assert topic_keys() == set()
    assert topic_keys("", "") == set()
    assert topic_keys("!!!") == set()  # 全標點正規化後為空


# --- pick_topic 的 URL 比對 ------------------------------------------------


def test_pick_blocks_same_url_with_changed_title():
    """同一篇文章換了標題/來源名寫法,URL 仍相同 → 必須封鎖。

    這是真實情境:FIT spec 於 08-06~08-12 被選中,09-02 又出現,
    URL 逐字元相同但來源名從「Phoronix」變成「Phoronix」/「phoronix.com」。
    """
    history = [
        {
            "date": "2026-08-08",
            "title": "Flattened Image Tree 1.0 Specification - Phoronix",
            "url": FIT_URL,
        }
    ]
    items = [
        {
            "index": 0,
            "title": "Flattened Image Tree 1.0 Specification For Embedded Linux - Phoronix",
            "url": FIT_URL,
        },
        *ITEMS[1:],
    ]
    ranking = [{"index": 0, "title": items[0]["title"], "score": 9.5}, *RANKING[1:]]
    item, _ = pick_topic(items, ranking, history, TODAY)
    assert item["index"] == 1


def test_history_entry_without_url_still_blocks_by_title():
    """舊版 history 沒有 url 欄位 → 退回標題比對,不可因此漏封鎖。"""
    history = [{"date": "2026-08-08", "title": ITEMS[0]["title"]}]
    item, _ = pick_topic(ITEMS, RANKING, history, TODAY)
    assert item["index"] == 1


def test_pick_skips_multi_word_source_variant():
    """端到端:history 記多字來源名,候選換成網域寫法仍要被封鎖。"""
    history = [
        {
            "date": "2026-08-08",
            "title": "How to Enable Secure Boot on Linux-Based Platforms"
            " - Embedded Computing Design",
        }
    ]
    items = [
        {
            "index": 0,
            "title": (
                "How to Enable Secure Boot on Linux-Based Platforms"
                " - embeddedcomputing.com"
            ),
            "url": "https://x",
        },
        *ITEMS[1:],
    ]
    ranking = [{"index": 0, "title": items[0]["title"], "score": 9.5}, *RANKING[1:]]
    item, _ = pick_topic(items, ranking, history, TODAY)
    assert item["index"] == 1  # 選到第二順位,不是同一篇文章的變體


def test_pick_skips_source_suffix_variant():
    """history 用「- Phoronix」封鎖,候選「- phoronix.com」變體也要被封鎖。"""
    history = [
        {"date": "2026-08-08", "title": "Flattened Image Tree 1.0 Specification - Phoronix"}
    ]
    items = [
        {
            "index": 0,
            "title": "Flattened Image Tree 1.0 Specification - phoronix.com",
            "url": "https://x",
        },
        *ITEMS[1:],
    ]
    ranking = [
        {"index": 0, "title": items[0]["title"], "score": 9.5},
        *RANKING[1:],
    ]
    item, _ = pick_topic(items, ranking, history, TODAY)
    assert item["index"] == 1  # 選到第二順位,不是變體 FIT


def test_warns_when_pool_nearly_exhausted(caplog):
    """可用候選過少 → 先發警告(不失敗),讓使用者在硬失敗前看到徵兆。"""
    history = [
        {"date": "2026-08-09", "title": ITEMS[i]["title"]} for i in (0, 1, 2)
    ]
    with caplog.at_level(logging.WARNING):
        item, _ = pick_topic(ITEMS, RANKING, history, TODAY)
    assert item["index"] == 3  # 只剩一個可用,仍要正常選出
    assert any("可用主題僅剩 1 則" in r.getMessage() for r in caplog.records)


def test_pick_top1_when_no_recent_match():
    item, _entry = pick_topic(ITEMS, RANKING, [], TODAY)
    assert item["index"] == 0


def test_skips_recently_selected_topic():
    history = [{"date": "2026-08-09", "title": ITEMS[0]["title"]}]  # 昨天選過 #1
    item, _entry = pick_topic(ITEMS, RANKING, history, TODAY)
    assert item["index"] == 1  # 跳到第二名


def test_skips_multiple_recent():
    history = [
        {"date": "2026-08-08", "title": ITEMS[0]["title"]},
        {"date": "2026-08-09", "title": ITEMS[1]["title"]},
        {"date": "2026-08-07", "title": ITEMS[2]["title"]},
    ]
    item, _ = pick_topic(ITEMS, RANKING, history, TODAY)
    assert item["index"] == 3  # 前三名都近期選過 → 第四名


def test_window_boundary():
    """窗口邊界由 HISTORY_DAYS 推算,視窗調整時測試不會失效。

    舊版把 14 天寫死;09-16 把視窗改成 90 天時該測試即失效 ——
    這正是它該抓到的訊號,所以改成跟著常數走。
    """
    today_d = date.fromisoformat(TODAY)
    inside = (today_d - timedelta(days=HISTORY_DAYS - 1)).isoformat()
    outside = (today_d - timedelta(days=HISTORY_DAYS)).isoformat()

    # 窗口最後一天選過 → 仍要跳過
    history = [{"date": inside, "title": ITEMS[0]["title"]}]
    item, _ = pick_topic(ITEMS, RANKING, history, TODAY)
    assert item["index"] == 1

    # 剛好滑出窗口 → 不影響
    history = [{"date": outside, "title": ITEMS[0]["title"]}]
    item, _ = pick_topic(ITEMS, RANKING, history, TODAY)
    assert item["index"] == 0


def test_all_recent_fails_instead_of_repeating():
    """全部候選都在窗口內 → 明確失敗,不可靜默重播舊主題。

    舊版退回第一名,等於重製一支既有影片(浪費 NotebookLM 配額 +
    頻道多一支重複),且使用者無從得知 — 2026-09 重複上傳成因之一。
    """
    history = [
        {"date": d, "title": ITEMS[i]["title"]}
        for i, d in enumerate(["2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"])
    ]
    with pytest.raises(SystemExit):
        pick_topic(ITEMS, RANKING, history, TODAY)


def test_case_and_punctuation_mismatch_still_dedup():
    history = [
        {
            "date": "2026-08-09",
            "title": "Flattened Image Tree 1.0 Specification Released!!",
        }
    ]
    item, _ = pick_topic(ITEMS, RANKING, history, TODAY)
    assert item["index"] == 1  # 標點/大小寫不同仍視為同一主題


def test_same_day_entry_does_not_block_rerun():
    """同日(catch-up 重跑)已記錄的主題不封鎖 → 當天選題維持穩定。"""
    history = [{"date": TODAY, "title": ITEMS[0]["title"]}]
    item, _ = pick_topic(ITEMS, RANKING, history, TODAY)
    assert item["index"] == 0


def test_malformed_history_entry_ignored():
    """壞日期/缺欄位的歷史條目被忽略,不讓選題崩潰或誤封鎖。"""
    history = [
        {"date": "not-a-date", "title": ITEMS[0]["title"]},
        {"date": "2026-08-09", "title": ITEMS[1]["title"]},
    ]
    item, _ = pick_topic(ITEMS, RANKING, history, TODAY)
    assert item["index"] == 0  # 壞資料被忽略,#0 未被近期選過
