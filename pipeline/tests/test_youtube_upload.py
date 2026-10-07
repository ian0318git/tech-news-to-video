"""youtube_upload 的 resumable 續傳決策邏輯測試(純函式,不連網)。

情境對應: 上傳完成但回應遺失 → 重跑查狀態補記;斷線 → 續傳;session 過期 → 重啟。
"""

import json
import logging
from pathlib import Path

import pytest
from youtube_upload import (
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
    """長片取 video_title、Shorts 取 shorts_title — 兩者欄位不同。"""
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
    assert long_title == "Embedded Linux Daily - Fined $5.7B! EU Hits Chip Giant"

    shorts_title, _ = build_metadata(
        tmp_path, channel, tmp_path / "shorts_2026-10-08.mp4"
    )
    assert shorts_title == "Embedded Linux Daily - Chip Giant Fined $5.7B!"


def test_build_metadata_switches_to_short_backup_when_over_limit(tmp_path, caplog):
    """主標題加上前綴後超過 TITLE_MAX → 整條換成 video_title_short,不腰斬。

    爆款標題為了鉤人常把最有力的字放在字尾,硬切等於白寫;Gemini 為前三名
    各寫的第三條(video_title_short)就是為此存在。
    """
    prefix = "Embedded Linux Daily"
    long_title = (
        "Broadcom Just Shipped A Brand New AI-Powered Linux Implant, "
        "And Nobody Saw This Coming!"
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
    assert len(f"{prefix} - {long_title}") > 95, "測試前提:主標題必須真的超長"
    with caplog.at_level(logging.WARNING):
        title, _ = build_metadata(
            tmp_path, {"title_prefix": prefix}, tmp_path / "video_2026-10-08.mp4"
        )
    assert title == f"{prefix} - {short_title}"
    assert "已改用較短的備援標題" in caplog.text


def test_build_metadata_never_uses_long_short_backup_for_shorts(tmp_path):
    """Shorts 不取 video_title_short — 兩個欄位屬於不同片子。

    Shorts 有專屬的 shorts_title(≤50),拿長片的備援去補是張冠李戴;真的
    還是太長就照舊截斷,不換欄位。
    """
    prefix = "Embedded Linux Daily"
    shorts_title = (
        "Toshiba Fights Hackers With A Brand New Cyber-Resilient Linux Distro Today!"
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
    expected = f"{prefix} - {shorts_title}"
    assert len(expected) > 95, "測試前提:Shorts 標題要真的超長,否則測不到截斷"
    title, _ = build_metadata(
        tmp_path, {"title_prefix": prefix}, tmp_path / "shorts_2026-10-08.mp4"
    )
    assert "Long-Form Backup!" not in title
    assert title == expected[:95]


def test_build_metadata_falls_back_to_news_title(tmp_path):
    """沒有爆款標題時退回原始新聞標題(舊行為不變,不會變成空的)。"""
    _write_top1(
        tmp_path,
        {"news": {"title": "Plain headline", "url": "https://x", "source": "S"}},
    )
    title, _ = build_metadata(
        tmp_path, {"title_prefix": "P"}, tmp_path / "video_2026-10-08.mp4"
    )
    assert title == "P - Plain headline"


def test_build_metadata_without_top1_uses_filename(tmp_path):
    """top1.json 不存在(例如手動補傳)時用檔名,不崩潰。"""
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
    assert title == "P - Fined $5.7B!"


def test_build_metadata_proceeds_when_date_unknown(tmp_path):
    """任一邊取不到日期(舊 top1.json 沒 date / 手動命名的檔案)→ 不擋。

    誤擋的代價比漏擋高:手動補傳一支名叫 final.mp4 的影片是合法操作。
    """
    _write_top1(
        tmp_path,
        {"video_title": "Viral!", "news": {"title": "n", "url": "u", "source": "S"}},
    )
    title, _ = build_metadata(tmp_path, {"title_prefix": "P"}, tmp_path / "final.mp4")
    assert title == "P - Viral!"


@pytest.mark.parametrize("bad_title", [None, "", "   ", "<>", 123, {"x": 1}, ["t"]])
def test_build_metadata_never_leaves_hanging_title(tmp_path, bad_title):
    """F1(2026-10-08 reviewer):`news.get("title", file.stem)` 只擋得住「鍵不存在」。

    `"title": null` 會回 None、`"title": "<>"` 淨化後成空 → build_title 兩邊
    都拿不到值,產出尾端懸空的 `"P - "` 直接上傳(而 build_title 的 docstring
    把檔名標題的責任推給呼叫端,呼叫端卻做不到)。fallback 現在保證非空。
    """
    _write_top1(
        tmp_path,
        {"date": "2026-10-08", "video_title": None, "news": {"title": bad_title}},
    )
    title, _ = build_metadata(
        tmp_path, {"title_prefix": "P"}, tmp_path / "video_2026-10-08.mp4"
    )
    assert title == "P - video 2026-10-08"
    assert not title.endswith("- "), "尾端懸空標題會直接上傳到 YouTube"


def test_build_metadata_tolerates_non_dict_news(tmp_path):
    """JSON 的 "news": null / 字串 → 不可 AttributeError 崩在最後一步上傳。"""
    _write_top1(tmp_path, {"date": "2026-10-08", "news": None, "video_title": "V!"})
    title, _ = build_metadata(
        tmp_path, {"title_prefix": "P"}, tmp_path / "video_2026-10-08.mp4"
    )
    assert title == "P - V!"


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
