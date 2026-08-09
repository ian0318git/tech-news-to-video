"""rank_news 的主題去重邏輯測試(7 天內不重複選題)。"""

from rank_news import pick_topic, title_key

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


def test_window_boundary_seven_days():
    # 剛好 7 天前(08-04)選過 → 仍在窗口內,應跳過
    history = [{"date": "2026-08-04", "title": ITEMS[0]["title"]}]
    item, _ = pick_topic(ITEMS, RANKING, history, TODAY)
    assert item["index"] == 1

    # 8 天前(08-02)選過 → 超出窗口,不影響
    history = [{"date": "2026-08-02", "title": ITEMS[0]["title"]}]
    item, _ = pick_topic(ITEMS, RANKING, history, TODAY)
    assert item["index"] == 0


def test_all_recent_falls_back_to_top1():
    history = [
        {"date": d, "title": ITEMS[i]["title"]}
        for i, d in enumerate(["2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"])
    ]
    item, _ = pick_topic(ITEMS, RANKING, history, TODAY)
    assert item["index"] == 0  # 極端情況退回第一名


def test_case_and_punctuation_mismatch_still_dedup():
    history = [
        {
            "date": "2026-08-09",
            "title": "Flattened Image Tree 1.0 Specification Released!!",
        }
    ]
    item, _ = pick_topic(ITEMS, RANKING, history, TODAY)
    assert item["index"] == 1  # 標點/大小寫不同仍視為同一主題
