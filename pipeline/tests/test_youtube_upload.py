"""youtube_upload 的 resumable 續傳決策邏輯測試(純函式,不連網)。

情境對應: 上傳完成但回應遺失 → 重跑查狀態補記;斷線 → 續傳;session 過期 → 重啟。
"""

import json
import logging
from pathlib import Path

import pytest
from _common import parse_program_marker
from youtube_upload import (
    DESCRIPTION_MAX,
    apply_custom_title,
    build_description,
    build_metadata,
    dates_agree,
    decide_resume,
    file_date,
    parse_range_end,
)

# --- build_metadata:真正決定 YouTube 標題的地方 ---------------------------
# 2026-10-08 reviewer 查出:爆款標題一度只被接到影片生成階段的 title,而那個
# title 只流向 NotebookLM 專案名稱(影片下載後該專案即被自動刪除),成品完全
# 沒套用。以下測試釘住「上傳標題的來源是 build_metadata」這件事。


def _write_top1(cdir, payload: dict) -> None:
    (cdir / "top1.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_build_metadata_uses_viral_title(tmp_path):
    """長片取 video_title、Shorts 取 shorts_title — 兩者欄位不同,且**都不加前綴**。

    channel 這裡刻意帶 title_prefix:它必須被忽略。前綴會占掉最前面的字元,
    而那些字元該放數字與結果(2026-10-08 使用者指示)。
    """
    _write_top1(
        tmp_path,
        {
            "video_title": "Fined $5.7B! EU Hits Chip Giant",
            "shorts_title": "Chip Giant Fined $5.7B!",
            "news": {"title": "EU fines chip giant", "url": "https://x", "source": "S"},
        },
    )
    channel = {"title_prefix": "Embedded Linux Daily"}

    long_title, _ = build_metadata(
        tmp_path, channel, tmp_path / "video_2026-10-08.branded.mp4"
    )
    assert long_title == "Fined $5.7B! EU Hits Chip Giant"
    assert "Embedded Linux Daily" not in long_title

    shorts_title, _ = build_metadata(
        tmp_path, channel, tmp_path / "shorts_2026-10-08.mp4"
    )
    assert shorts_title == "Chip Giant Fined $5.7B!"


def test_build_metadata_switches_to_short_backup_when_over_limit(tmp_path, caplog):
    """主標題超過 TITLE_MAX → 整條換成 video_title_short,不腰斬。

    爆款標題為了鉤人常把最有力的字放在字尾,硬切等於白寫;Gemini 為前三名
    各寫的第三條(video_title_short)就是為此存在。prompt 已把長片壓到 25
    字元,但那是指示不是保證 —— 模型寫爆的時候要有東西接住。
    """
    long_title = (
        "Broadcom Just Shipped A Brand New AI-Powered Linux Implant, "
        "And Absolutely Nobody Saw This Coming!"
    )
    short_title = "Broadcom's New AI Linux Implant!"
    _write_top1(
        tmp_path,
        {
            "date": "2026-10-08",
            "video_title": long_title,
            "video_title_short": short_title,
            "news": {"title": "Broadcom implant", "url": "https://x", "source": "S"},
        },
    )
    assert len(long_title) > 95, "測試前提:主標題必須真的超長"
    with caplog.at_level(logging.WARNING):
        title, _ = build_metadata(
            tmp_path,
            {"title_prefix": "Embedded Linux Daily"},
            tmp_path / "video_2026-10-08.mp4",
        )
    assert title == short_title
    assert "已改用較短的備援標題" in caplog.text


def test_build_metadata_never_uses_long_short_backup_for_shorts(tmp_path):
    """Shorts 不取 video_title_short — 兩個欄位屬於不同片子。

    Shorts 有專屬的 shorts_title,拿長片的備援去補是張冠李戴;真的還是太長
    就照舊截斷,不換欄位。
    """
    shorts_title = (
        "Toshiba Fights Hackers With A Brand New Cyber-Resilient Linux "
        "Distro, And It Ships Today For Free!"
    )
    _write_top1(
        tmp_path,
        {
            "date": "2026-10-08",
            "shorts_title": shorts_title,
            "video_title_short": "Long-Form Backup!",
            "news": {"title": "n", "url": "https://x", "source": "S"},
        },
    )
    assert len(shorts_title) > 95, "測試前提:Shorts 標題要真的超長,否則測不到截斷"
    title, _ = build_metadata(
        tmp_path,
        {"title_prefix": "Embedded Linux Daily"},
        tmp_path / "shorts_2026-10-08.mp4",
    )
    assert "Long-Form Backup!" not in title
    assert title == shorts_title[:95]


def test_build_metadata_falls_back_to_news_title(tmp_path):
    """沒有爆款標題時退回原始新聞標題(舊行為不變,不會變成空的)。

    前綴一併不加:fallback 是**原始新聞標題**,它本身就是完整的內容標題。
    """
    _write_top1(
        tmp_path,
        {"news": {"title": "Plain headline", "url": "https://x", "source": "S"}},
    )
    title, _ = build_metadata(
        tmp_path, {"title_prefix": "P"}, tmp_path / "video_2026-10-08.mp4"
    )
    assert title == "Plain headline"


def test_build_metadata_without_top1_uses_filename(tmp_path):
    """top1.json 不存在(例如手動補傳)時用檔名,不崩潰。

    這裡**保留**前綴,與內容標題相反 —— 這條路徑只有日期可放,沒有內容可以
    前置,前綴是唯一認得出頻道的資訊。這條斷言就是那個例外的守門人:哪天有人
    順手把前綴全拔了,這裡會紅。
    """
    title, description = build_metadata(
        tmp_path, {"title_prefix": "P"}, tmp_path / "video_2026-10-08.mp4"
    )
    assert title == "P - video 2026-10-08"
    assert description


def test_build_metadata_ignores_top1_from_another_day(tmp_path, caplog):
    """R3(2026-10-08 reviewer):top1.json 已被新的一天覆寫,而這支影片是舊的
    (當天生成成功、上傳失敗,隔天 --file 補傳)→ 不可把新主題的爆款標題掛上去。

    這是「A 的標題出現在 B 的影片上」唯一還存在的路徑:正常流程裡 top1.json
    與影片檔同一天產生;只有跨日補傳才會錯開。
    """
    _write_top1(
        tmp_path,
        {
            "date": "2026-10-08",
            "video_title": "Today's Viral Headline!",
            "news": {"title": "Today's article", "url": "https://x", "source": "S"},
        },
    )
    with caplog.at_level(logging.WARNING):
        title, description = build_metadata(
            tmp_path,
            {"title_prefix": "P"},
            tmp_path / "video_2026-10-07.branded.mp4",  # 昨天的影片
        )
    assert title == "P - video 2026-10-07.branded"
    assert "Today's Viral Headline!" not in title
    assert "Today's article" not in description
    assert "不同天" in caplog.text


def test_build_metadata_uses_top1_when_dates_match(tmp_path):
    """同一天(正常流程)→ 日期閘門不得誤擋爆款標題。"""
    _write_top1(
        tmp_path,
        {
            "date": "2026-10-08",
            "video_title": "Fined $5.7B!",
            "news": {"title": "n", "url": "https://x", "source": "S"},
        },
    )
    title, _ = build_metadata(
        tmp_path, {"title_prefix": "P"}, tmp_path / "video_2026-10-08.branded.mp4"
    )
    assert title == "Fined $5.7B!"


def test_build_metadata_proceeds_when_date_unknown(tmp_path):
    """任一邊取不到日期(舊 top1.json 沒 date / 手動命名的檔案)→ 不擋。

    誤擋的代價比漏擋高:手動補傳一支名叫 final.mp4 的影片是合法操作。
    """
    _write_top1(
        tmp_path,
        {"video_title": "Viral!", "news": {"title": "n", "url": "u", "source": "S"}},
    )
    title, _ = build_metadata(tmp_path, {"title_prefix": "P"}, tmp_path / "final.mp4")
    assert title == "Viral!"


@pytest.mark.parametrize("bad_title", [None, "", "   ", "<>", 123, {"x": 1}, ["t"]])
def test_build_metadata_never_leaves_hanging_title(tmp_path, bad_title):
    """F1(2026-10-08 reviewer):`news.get("title", file.stem)` 只擋得住「鍵不存在」。

    `"title": null` 會回 None、`"title": "<>"` 淨化後成空 → build_title 兩邊
    都拿不到值,產出空標題直接上傳(而 build_title 的 docstring 把檔名標題的
    責任推給呼叫端,呼叫端卻做不到)。fallback 現在保證非空。
    """
    _write_top1(
        tmp_path,
        {"date": "2026-10-08", "video_title": None, "news": {"title": bad_title}},
    )
    title, _ = build_metadata(
        tmp_path, {"title_prefix": "P"}, tmp_path / "video_2026-10-08.mp4"
    )
    # 走的是 filename_title,所以**帶前綴** —— 內容標題不加前綴,這條沒有內容
    # 可放,前綴是唯一認得出節目的資訊(與下面 without_top1 同一條規則)。
    assert title == "P - video 2026-10-08"
    assert title.strip(), "空標題會被 YouTube 拒絕(或更糟:送出無標題的影片)"


def test_build_metadata_tolerates_non_dict_news(tmp_path):
    """JSON 的 "news": null / 字串 → 不可 AttributeError 崩在最後一步上傳。"""
    _write_top1(tmp_path, {"date": "2026-10-08", "news": None, "video_title": "V!"})
    title, _ = build_metadata(
        tmp_path, {"title_prefix": "P"}, tmp_path / "video_2026-10-08.mp4"
    )
    assert title == "V!"


# --- build_description:節目標記(2026-10-08)--------------------------------
# 標題從 2026-10-08 起不再帶前綴,說明裡的 "program: <slug>" 就成為
# backfill_history 歸屬節目的唯一線索。被截掉不會有任何錯誤訊息,只會在幾個月後
# 「補 0 筆」安靜地漏掉影片 —— 所以截斷只能砍可變的前段。


def test_build_description_carries_program_marker():
    desc = build_description("Headline\n來源: S  https://x", "embedded")

    assert parse_program_marker(desc) == "embedded"


@pytest.mark.parametrize("slug", ["", "   ", None, 123])
def test_build_description_omits_marker_for_missing_slug(slug):
    """沒有 slug 就不要寫半截 "program: " —— 解析端會把它當成沒有標記。"""
    desc = build_description("Headline", slug)

    assert "program" not in desc
    assert parse_program_marker(desc) == ""


@pytest.mark.parametrize("head_len", [10, 4800, 40000])
def test_build_description_keeps_marker_when_head_is_huge(head_len):
    """摘要在長也砍不到標記(整串 [:4900] 的寫法會把尾端標記裁掉)。"""
    head = "x" * head_len
    desc = build_description(head, "tech")

    assert len(desc) <= DESCRIPTION_MAX, "超過 YouTube 說明硬限會被拒收"
    assert parse_program_marker(desc) == "tech"


def test_build_metadata_description_carries_program_marker(tmp_path):
    """上傳說明的標記由 build_metadata 蓋上去,不是靠呼叫端記得加。"""
    _write_top1(
        tmp_path,
        {
            "date": "2026-10-08",
            "video_title": "V!",
            "news": {"title": "n", "url": "https://x", "source": "S"},
        },
    )
    _, description = build_metadata(
        tmp_path,
        {"title_prefix": "P", "slug": "embedded"},
        tmp_path / "video_2026-10-08.mp4",
    )

    assert parse_program_marker(description) == "embedded"


def test_build_metadata_description_marker_on_the_no_content_path(tmp_path):
    """沒有 top1.json 的降級路徑也要帶標記 —— 手動補傳照樣會被 backfill 看見。"""
    _, description = build_metadata(
        tmp_path,
        {"title_prefix": "P", "slug": "tech"},
        tmp_path / "video_2026-10-08.mp4",
    )

    assert parse_program_marker(description) == "tech"


# --- apply_custom_title:--title 的淨化 ------------------------------------


def test_apply_custom_title_uses_cleaned_value():
    assert apply_custom_title("  My  Title  ", "Auto", logging.getLogger("t")) == "My Title"


@pytest.mark.parametrize("bad", ["   ", "  <  >  ", "<>", "\n<\n", "\t"])
def test_apply_custom_title_ignores_empty_after_cleaning(bad, caplog):
    """`--title "   "` 是 truthy → 舊版會直接截上傳,送出一條空白標題。
    判斷必須在淨化之後(與 build_title 的 R1 同一道原則),且要留下警告。"""
    with caplog.at_level(logging.WARNING):
        title = apply_custom_title(bad, "Auto Title", logging.getLogger("t"))
    assert title == "Auto Title"
    assert "淨化後為空" in caplog.text


def test_apply_custom_title_passes_through_when_absent():
    """沒下 --title(值為 None)→ 不動自動標題,也不發警告。"""
    assert apply_custom_title(None, "Auto Title", logging.getLogger("t")) == "Auto Title"


def test_apply_custom_title_truncates_at_platform_limit():
    assert len(apply_custom_title("x" * 300, "Auto", logging.getLogger("t"))) <= 100


def test_file_date_and_dates_agree():
    assert file_date(Path("video_2026-10-08.branded.mp4")) == "2026-10-08"
    assert file_date(Path("shorts_2026-10-08.mp4")) == "2026-10-08"
    assert file_date(Path("final.mp4")) is None
    # 無法證明不一致 → 放行
    assert dates_agree({}, Path("final.mp4"))
    assert dates_agree({"date": "2026-10-08"}, Path("final.mp4"))
    assert dates_agree({"date": "2026-10-08"}, Path("video_2026-10-08.mp4"))
    assert not dates_agree({"date": "2026-10-08"}, Path("video_2026-10-07.mp4"))


def test_parse_range_end():
    assert parse_range_end("bytes=0-1234") == 1235
    assert parse_range_end("bytes=0-0") == 1
    assert parse_range_end("bytes=0-999999") == 1000000


def test_parse_range_end_missing_or_malformed():
    assert parse_range_end(None) is None
    assert parse_range_end("bytes=0-abc") is None
    assert parse_range_end("bytes=100-200") is None  # 起點不是 0 → 當作無 Range


def test_decide_done_when_response_lost():
    """最後 PUT 的 200/201 回應遺失 → 狀態查詢回 200/201 → 判定已完成。"""
    assert decide_resume(200, None, 100) == ("done", None)
    assert decide_resume(201, "bytes=0-50", 100) == ("done", None)


def test_decide_resume_offset():
    assert decide_resume(308, "bytes=0-1234", 10000) == ("resume", 1235)
    assert decide_resume(308, None, 10000) == ("resume", 0)


def test_decide_resume_clamped_to_size():
    """Range 顯示全收到 → offset==size,交由收尾流程處理。"""
    assert decide_resume(308, "bytes=0-999999", 1000) == ("resume", 1000)
    assert decide_resume(308, "bytes=0-1000000", 1000) == ("resume", 1000)


def test_decide_restart_when_session_expired():
    assert decide_resume(404, None, 100) == ("restart", None)
    assert decide_resume(410, None, 100) == ("restart", None)


def test_decide_fail_on_unknown_status():
    """無法判定的狀態 → fail,不冒重複上傳的險。"""
    assert decide_resume(500, None, 100) == ("fail", None)
    assert decide_resume(403, "bytes=0-10", 100) == ("fail", None)
