"""rank_news 的主題去重邏輯測試(HISTORY_DAYS 天內不重複選題)。"""

import logging
from datetime import date, timedelta

import pytest
from _common import SHORTS_TITLE_FIELD, VIDEO_TITLE_FIELD, VIDEO_TITLE_SHORT_FIELD
from rank_news import (
    HISTORY_DAYS,
    RANK_PROMPT_TEMPLATE,
    apply_dedup_choice,
    entry_index,
    pick_topic,
    title_key,
    topic_keys,
    url_key,
)

TODAY = "2026-08-10"

# apply_dedup_choice 需要 logger(只用在警告);測試用獨立的,不碰 rank_news 的檔案 handler
logger = logging.getLogger("test_rank_news")

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


def test_all_invalid_ranking_blames_index_not_dedup(caplog):
    """R2(2026-10-08 reviewer):ranking 條目的 index 全部壞掉時,真因是 Gemini
    回傳格式,不是去重窗口 — 舊版一律報「全部落在去重窗口內」,事故排查會走錯
    方向(去翻 topic_history.json,而那裡根本沒問題)。"""
    ranking = [{"index": 99}, {"index": "abc"}, {"index": -1}, {"index": None}]
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit):
        pick_topic(ITEMS, ranking, [], TODAY)
    assert "index 全部無效" in caplog.text
    assert "去重窗口" not in caplog.text


def test_empty_ranking_reports_empty_not_invalid(caplog):
    """F2(2026-10-08 reviewer):空 ranking 會讓 `invalid == len(ranking)` 成立
    (0 == 0),報成「0 則條目 index 全部無效」語意不通。"""
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit):
        pick_topic(ITEMS, [], [], TODAY)
    assert "空陣列" in caplog.text
    assert "index 全部無效" not in caplog.text


def test_all_blocked_message_includes_invalid_count(caplog):
    """壞條目與去重封鎖同時發生時,訊息要帶上壞條目數,否則看不出 pool 變小
    有兩個成因。"""
    history = [
        {"date": d, "title": ITEMS[i]["title"]}
        for i, d in enumerate(["2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"])
    ]
    ranking = [*RANKING, {"index": 99}]
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit):
        pick_topic(ITEMS, ranking, history, TODAY)
    assert f"全部落在 {HISTORY_DAYS} 天去重窗口內" in caplog.text
    assert "另有 1 則條目 index 無效" in caplog.text


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


# --- 選題 prompt 護欄 -------------------------------------------------------
# prompt 是整條 pipeline 唯一的選題輸入,但它只是個字串常數,改壞了不會有
# type error,只會在隔天早上安靜地選出爛題目。以下三條把意圖鎖住。


def test_rank_prompt_template_renders_with_real_arguments():
    """format 佔位符與 JSON 範例的大括號必須正確 — 渲染失敗 = 整天選題掛掉。"""
    out = RANK_PROMPT_TEMPLATE.format(topic="embedded linux", items_json="[]")
    assert "{topic}" not in out and "{items_json}" not in out  # 佔位符已替換
    assert "embedded linux" in out  # 參數確實注入
    assert "{{" not in out and "}}" not in out  # 跳脫字元已被 format 消化
    assert '"ranking": [' in out and '"top1": {' in out  # JSON 契約完整


def test_rank_prompt_keeps_editorial_priorities():
    """編輯立場護欄:優先科技巨頭/商業衝突/突破性產品,醫療與論文降權(2026-10-07)。"""
    out = RANK_PROMPT_TEMPLATE.format(topic="t", items_json="[]")
    assert "PRIORITISE" in out and "DE-PRIORITISE" in out
    for term in ("technology companies", "antitrust", "Medical", "academic"):
        assert term in out, f"編輯準則關鍵字消失: {term!r}"


def test_rank_prompt_requires_every_item_ranked():
    """降權 ≠ 排除。pick_topic 沿 ranking 依序取用,若 Gemini 省略被降權的項目,
    候選池會縮小 → 最壞情況整天 fail 選題。這道指令不能消失。"""
    out = RANK_PROMPT_TEMPLATE.format(topic="t", items_json="[]")
    assert "include every news item" in out


def test_rank_prompt_asks_for_viral_titles():
    """爆款標題的**風格規則**必須齊全。注意它與選題共用同一次 Gemini 呼叫 —
    免費層每日配額僅 20 次,不能為了標題另開一次呼叫(2026-10-07)。

    2026-10-08 起改用使用者給的規則集(原本是四個公式 A–D);其中「少用問號
    結尾」直接推翻了舊公式 (D) SUSPENSE(它的範例就是問句),所以那組公式
    是**被取代**而不是被補充。逐條檢查,少一條就可能整批標題走鐘。
    """
    out = RANK_PROMPT_TEMPLATE.format(topic="t", items_json="[]")
    for rule in (
        "RESULT, the CONFLICT, the number",  # 1 先講結果/衝突,不鋪陳
        "withhold WHY",  # 2 好奇心缺口
        "a question mark is\n   not",  # 3 直述句優先
        "Contrast is the strongest hook",  # 4 反差
        "series name",  # 5 禁止頻道/系列前綴
        "put it FIRST",  # 6 數字放最前面
        '"99% of people"',  # 7 禁止無法證實的誇大
        "Never first person",  # 8 不用第一人稱
    ):
        assert rule in out, f"標題規則消失: {rule!r}"
    # 舊公式確實退場(留著會與新規則打架:Gemini 會挑 SUSPENSE 寫問句)
    assert "SUSPENSE" not in out


def test_rank_prompt_bans_the_channel_prefix():
    """標題不得含頻道/系列/固定前綴 —— 前綴會吃掉最前面的字元。

    這條是 prompt 端的一半;另一半在 youtube_upload.build_metadata(傳空 head)。
    兩邊都要有:prompt 沒說,Gemini 會自己加上品牌;程式沒拔,前綴照樣進標題。
    使用者直接點名了這兩個字串,所以連範例都要在 prompt 裡。
    """
    out = RANK_PROMPT_TEMPLATE.format(topic="t", items_json="[]")
    for banned in ("TechSnack Daily", "Embedded Linux Daily"):
        assert banned in out, f"被點名的前綴沒寫進 prompt: {banned!r}"


def test_title_field_names_match_prompt_contract():
    """漂移護欄:rank_news 在 prompt 裡要 Gemini 填的欄位名,必須與 build_title
    讀取的常數一致。不一致不會報錯 — 只會靜默退回原始標題,很難察覺。

    必須出現在 **JSON 範例**裡:prompt 有兩處提到欄位名(規格行與 JSON 範例),
    Gemini 是照 JSON 範例的形狀回傳的 — 只檢查「全文出現過」會讓「範例欄位
    被刪掉、只剩規格行」照樣通過,而 Gemini 從此不再回這個欄位。

    2026-10-08 起欄位掛在 ranking 的前三條上(top1 那行**不該**再有標題欄位 —
    兩處都寫會讓 Gemini 兩邊都填,而程式只讀 ranking,多出來的那份只會誤導)。
    """
    out = RANK_PROMPT_TEMPLATE.format(topic="t", items_json="[]")
    lines = out.splitlines()
    example = [ln.strip() for ln in lines if ln.strip().startswith('{"index":')]
    assert len(example) >= 4, "JSON 範例的 ranking 條目不見了"
    titled = [ln for ln in example if f'"{VIDEO_TITLE_FIELD}"' in ln]
    assert len(titled) == 3, f"範例要有三條帶標題的 ranking 條目,實得 {len(titled)}"
    for ln in titled:
        for field in (VIDEO_TITLE_FIELD, VIDEO_TITLE_SHORT_FIELD, SHORTS_TITLE_FIELD):
            assert f'"{field}"' in ln, f"範例條目少了欄位: {field!r}"
    top1_line = next(ln for ln in lines if '"top1": {' in ln)
    for field in (VIDEO_TITLE_FIELD, VIDEO_TITLE_SHORT_FIELD, SHORTS_TITLE_FIELD):
        assert f'"{field}"' not in top1_line, f"top1 不該再帶 {field!r}"


def test_rank_prompt_asks_titles_for_top_three():
    """前三名各三條 — 這是 2026-10-08 改動的核心契約。

    為什麼是前三名:pick_topic 沿排名取第一則沒被去重窗口封鎖的,而實測 20 則
    候選有 18 則落在 90 天窗口內 → 改選幾乎是常態。只為 #1 寫標題,選中的
    往往不是 #1,標題等於白做。
    """
    out = RANK_PROMPT_TEMPLATE.format(topic="t", items_json="[]")
    assert "TOP 3" in out
    assert "THREE English headlines each" in out
    # 長度上限是契約的一部分。2026-10-08 使用者指示:長片約 25 字內(重點在前
    # 15 字)、Shorts 約 15 字內;備援再短一級。測試只認 prompt 的**指示**,
    # 不認模型是否照做 —— 遵從度靠實測(同一輪的 probe 顯示:Gemini 對長度
    # 幾乎不設防,25 字上限實測回 37–56 字 → 因此 prompt 才要明講「數」、
    # 給改寫範例、並在結尾再檢查一次)。
    for limit in (
        "MAXIMUM 25 characters",
        "FIRST 15 characters",
        "MAXIMUM 15 characters",
    ):
        assert limit in out, f"標題長度指示消失: {limit!r}"
    assert "count its characters" in out, "「自己數字數」的指令消失 → 長度會再度失控"
    assert "Final check before you return" in out
    # 只為前三名寫,其餘條目仍要留在 ranking 裡(省略會縮小候選池 → 可能整天 fail)
    assert "Every news item must still appear" in out


# --- apply_dedup_choice:去重改選後的 top1 重寫(2026-10-08 補) ---------------
# 這是整條選題鏈裡最容易「靜默出錯」的一段:錯了不會拋例外,只會讓 top1.json
# 變成「A 的標題 + B 的內容」的混合檔,一路傳到上傳。


def test_apply_dedup_choice_keeps_top1_when_index_matches():
    """Gemini 的 #1 就是實際選中的那篇 → 保留它自己的 title/url/why_top。

    標題欄位不沿用 top1 上的殘值,而是取自 chosen_entry(前三名各三條之後,
    ranking 條目才是唯一來源)。所以這裡比對的是「有沒有照抄 Gemini 的識別
    欄位」,不再是「回傳同一個物件」。
    """
    top1 = {"index": 1, "title": "T", "url": "u", "why_top": "gemini reason"}
    entry = {
        "reason": "gemini reason",
        VIDEO_TITLE_FIELD: "Viral!",
        VIDEO_TITLE_SHORT_FIELD: "Viral!",
        SHORTS_TITLE_FIELD: "Short viral!",
    }
    out = apply_dedup_choice(top1, {"title": "T"}, entry, 1, logger)
    assert out["index"] == 1
    assert out["title"] == "T"
    assert out["url"] == "u"
    assert out["why_top"] == "gemini reason"
    assert out[VIDEO_TITLE_FIELD] == "Viral!"
    assert out[VIDEO_TITLE_SHORT_FIELD] == "Viral!"
    assert out[SHORTS_TITLE_FIELD] == "Short viral!"


def test_apply_dedup_choice_carries_titles_from_the_entry_it_picked():
    """去重改選到前三名之內的別篇 → 沿用**那一篇**的爆款標題。

    這是 2026-10-08 改動的主要目的:先前只要去重改選就一律清空標題,而改選
    幾乎是常態(20 則候選有 18 則落在 90 天窗口內)→ 爆款標題等於白做。
    Gemini 現在為前三名各寫三條,改選到 #2/#3 時就有標題可用。
    """
    top1 = {
        "index": 0,
        "title": "Gemini #1",
        "url": "u0",
        VIDEO_TITLE_FIELD: "Describes The Rejected Article!",
    }
    chosen = {"title": "Chosen", "url": "u7", "summary": "sum"}
    entry = {
        "index": 2,
        "reason": "dedup reason",
        VIDEO_TITLE_FIELD: "Right long title!",
        VIDEO_TITLE_SHORT_FIELD: "Right short!",
        SHORTS_TITLE_FIELD: "Right shorts!",
    }
    out = apply_dedup_choice(top1, chosen, entry, 2, logger)
    assert out["title"] == "Chosen"
    assert out["url"] == "u7"
    assert out[VIDEO_TITLE_FIELD] == "Right long title!"
    assert out[VIDEO_TITLE_SHORT_FIELD] == "Right short!"
    assert out[SHORTS_TITLE_FIELD] == "Right shorts!"
    assert "Describes The Rejected Article!" not in out.values()


@pytest.mark.parametrize("bad", [None, 123, ["a"], {"x": 1}])
def test_apply_dedup_choice_coerces_non_string_titles(bad):
    """Gemini 可能把標題回成 null / 數字 / 陣列 → 一律收成空字串。

    與 build_title 的 clean_headline 同一個原則:型別判斷只有一份,不讓
    非字串繼續往下流(那會讓下游的 WARN 訊息指向錯誤的原因)。
    """
    entry = {VIDEO_TITLE_FIELD: bad, VIDEO_TITLE_SHORT_FIELD: bad}
    out = apply_dedup_choice({"index": 0}, {"title": "T"}, entry, 0, logger)
    assert out[VIDEO_TITLE_FIELD] == ""
    assert out[VIDEO_TITLE_SHORT_FIELD] == ""


def test_apply_dedup_choice_warns_when_top1_itself_lacks_titles(caplog):
    """選中的就是 Gemini 自己的 #1,卻一個標題欄位都沒有 → 契約違反,必須 WARN。

    #1 必然在前三名之內,所以「前三名各三條」的契約下這裡不該是空的。
    """
    with caplog.at_level(logging.WARNING):
        out = apply_dedup_choice({"index": 0}, {"title": "T"}, {}, 0, logger)
    assert out[VIDEO_TITLE_FIELD] == ""
    assert "不符 prompt 契約" in caplog.text


def test_apply_dedup_choice_stays_quiet_when_picked_outside_top_three(caplog):
    """去重改選到前三名之外 → 預期情形(那些條目本來就沒標題),記 INFO 即可。

    洗 WARN 會讓真正的契約違反被埋掉;下游 build_title 另有 WARN,不會靜默。
    """
    with caplog.at_level(logging.INFO):
        out = apply_dedup_choice({"index": 0}, {"title": "T"}, {}, 9, logger)
    assert out[VIDEO_TITLE_FIELD] == ""
    assert "前三名之外" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_apply_dedup_choice_rewrites_on_reject():
    """去重改選別篇 → 識別欄位換成真正選中的那篇,爆款標題清空(避免張冠李戴)。"""
    top1 = {
        "index": 3,
        "title": "Rejected",
        "url": "u3",
        "why_top": "gemini reason",
        VIDEO_TITLE_FIELD: "Describes The Rejected Article!",
        SHORTS_TITLE_FIELD: "Wrong short!",
    }
    chosen = {"title": "Chosen", "url": "u7", "summary": "sum"}
    out = apply_dedup_choice(top1, chosen, {"reason": "dedup reason"}, 7, logger)
    assert out["index"] == 7
    assert out["title"] == "Chosen"
    assert out["url"] == "u7"
    assert out["why_top"] == "dedup reason"
    assert out["headline"] == "sum"
    assert out[VIDEO_TITLE_FIELD] == ""
    assert out[SHORTS_TITLE_FIELD] == ""


@pytest.mark.parametrize("bad_index", [None, "abc", {"x": 1}, [0]])
def test_apply_dedup_choice_rewrites_when_index_unreadable(caplog, bad_index):
    """回歸護欄(2026-10-08 reviewer M1):index 缺失/畸形時,舊版因
    `top1_index is not None and ...` 而整段跳過 → 靜默留下 Gemini 那篇的
    title/url/爆款標題,與 top1["news"] 的真正選中文章不一致。現在一律覆寫。"""
    top1 = {
        "index": bad_index,
        "title": "Gemini Pick",
        "url": "u1",
        VIDEO_TITLE_FIELD: "Describes The Other Article!",
    }
    chosen = {"title": "Actually Chosen", "url": "u2", "summary": "s"}
    with caplog.at_level(logging.WARNING):
        out = apply_dedup_choice(top1, chosen, {"reason": "r"}, 2, logger)
    assert out["title"] == "Actually Chosen"
    assert out["url"] == "u2"
    assert out[VIDEO_TITLE_FIELD] == ""
    assert "缺少有效 index" in caplog.text


def test_apply_dedup_choice_missing_index_key(caplog):
    """完全沒有 index 欄位時同樣走覆寫路徑。"""
    chosen = {"title": "Chosen", "url": "u9", "summary": "s"}
    with caplog.at_level(logging.WARNING):
        out = apply_dedup_choice({"title": "No Index"}, chosen, {}, 3, logger)
    assert out["title"] == "Chosen"
    assert "缺少有效 index" in caplog.text


def test_apply_dedup_choice_tolerates_missing_chosen_keys(caplog):
    """chosen 缺 title/url 不該拋 KeyError(R4,2026-10-08 reviewer)。

    呼叫端目前保證 chosen 來自 items(fetch_news 會濾掉沒 title/link 的項目),
    所以現實中不可達 — 但同一輪已把 pick_topic 的取用改成容錯,這裡留著 []
    就只做了一半,而這正是本檔案存在的理由:畸形輸入不該變成 traceback,
    那會蓋掉真正的原因。
    """
    with caplog.at_level(logging.WARNING):
        out = apply_dedup_choice({"index": 5}, {}, {}, 0, logger)
    assert out["title"] == ""
    assert out["url"] == ""
    assert out[VIDEO_TITLE_FIELD] == ""
    assert out[SHORTS_TITLE_FIELD] == ""


# --- entry_index:畸形 ranking 條目的防呆 ------------------------------------


@pytest.mark.parametrize(
    "entry,expected",
    [
        ({"index": 0}, 0),
        ({"index": "2"}, 2),
        ({"index": 3}, None),  # 越界(3 個項目 → 0..2)
        ({"index": -1}, None),
        ({"title": "no index"}, None),
        ({"index": "abc"}, None),
        ({"index": None}, None),
        ("not a dict", None),
        (None, None),
    ],
)
def test_entry_index_handles_malformed(entry, expected):
    assert entry_index(entry, 3) == expected


def test_pick_topic_skips_malformed_ranking_entries(caplog):
    """畸形條目不可變成 traceback(IndexError/KeyError/ValueError)蓋掉真因,
    應跳過 + 警告,其餘候選照常運作。"""
    ranking = [
        {"index": 99},  # 越界
        {"title": "no index"},  # 缺欄位
        {"index": "abc"},  # 非數字
        {"index": 1, "title": ITEMS[1]["title"]},
    ]
    with caplog.at_level(logging.WARNING):
        item, entry = pick_topic(ITEMS, ranking, [], TODAY)
    assert item["index"] == 1
    assert entry["index"] == 1
    assert "index 無效" in caplog.text
