"""_common 的參數解析與頻道解析單元測試。"""

import json
import logging
import re
from pathlib import Path

import pytest
from _common import TITLE_MAX, build_title, flag_value, resolve_channel, today_str


@pytest.fixture
def test_logger():
    return logging.getLogger("test")


def test_flag_value_present():
    assert flag_value(["--channel", "tech"], "--channel") == "tech"


def test_flag_value_missing():
    assert flag_value([], "--channel") is None
    assert flag_value(["--skip-fetch"], "--channel") is None


def test_flag_value_default():
    assert flag_value([], "--privacy", "private") == "private"


def test_flag_value_flag_at_end_no_value():
    # 旗標是最後一個參數(沒有值)— 應回傳 default 而非報錯
    assert flag_value(["--channel"], "--channel") is None


def test_resolve_channel_default_first(test_logger):
    ch = resolve_channel(None, test_logger)
    assert ch["slug"] == "embedded"  # config/channels.json 的第一個頻道


def test_resolve_channel_by_slug(test_logger):
    # config/channels.json 是單一真相 — 斷言對應 slug 的 keyword 與 config 一致
    # (08-26 擴充查詢時曾漏改此處,導致測試卡在舊關鍵字)
    ch = resolve_channel("tech", test_logger)
    cfg_path = Path(__file__).parent.parent / "config" / "channels.json"
    config = json.loads(cfg_path.read_text(encoding="utf-8"))
    expected = next(
        c["keyword"] for c in config["channels"] if c["slug"] == "tech"
    )
    assert ch["keyword"] == expected


def test_resolve_channel_unknown_fails(test_logger):
    with pytest.raises(SystemExit):
        resolve_channel("nope", test_logger)


# ---- today_str: PIPELINE_DATE 覆寫(停電預製次日影片用)----


def test_today_str_default_format(monkeypatch):
    monkeypatch.delenv("PIPELINE_DATE", raising=False)
    s = today_str()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", s)


def test_today_str_override_valid(monkeypatch):
    monkeypatch.setenv("PIPELINE_DATE", "2026-08-19")
    assert today_str() == "2026-08-19"


def test_today_str_override_invalid_format(monkeypatch):
    monkeypatch.setenv("PIPELINE_DATE", "19-08-2026")
    with pytest.raises(ValueError):
        today_str()


# ---- build_title:爆款標題組合與退回(2026-10-07)----


def test_build_title_uses_viral_title(test_logger):
    title = build_title(
        "2026-10-08 TechSnack Daily", "Apple Takes On Microsoft!", "Apple sues rival", test_logger
    )
    assert title == "2026-10-08 TechSnack Daily - Apple Takes On Microsoft!"


def test_build_title_falls_back_when_missing(test_logger, caplog):
    """Gemini 沒回爆款標題時退回原始新聞標題,且必須留下警告(不靜默)。"""
    with caplog.at_level(logging.WARNING):
        title = build_title("2026-10-08 TechSnack Daily", None, "Original headline", test_logger)
    assert title == "2026-10-08 TechSnack Daily - Original headline"
    assert "退回原始新聞標題" in caplog.text


@pytest.mark.parametrize("bad", ["", "   ", 123, {"a": 1}, ["x"], True])
def test_build_title_falls_back_on_non_string(test_logger, caplog, bad):
    """Gemini 可能回非字串或空白 — 必須退回而非 AttributeError 崩潰。"""
    with caplog.at_level(logging.WARNING):
        title = build_title("H", bad, "Fallback", test_logger)
    assert title == "H - Fallback"
    assert "退回原始新聞標題" in caplog.text


def test_build_title_truncates_at_youtube_limit(test_logger, caplog):
    """超過 YouTube 上限要截斷,且必須發警告 — 不靜默吃掉字。

    斷言用**字面值 100**(YouTube 官方硬限),不是 TITLE_MAX — 否則有人把
    常數改成 5000 時測試照樣綠燈,但上傳會被 YouTube 拒收。
    """
    with caplog.at_level(logging.WARNING):
        title = build_title("H", "x" * 300, "f", test_logger)
    assert len(title) <= 100, "超過 YouTube 標題硬限"
    assert len(title) == TITLE_MAX, "應截到常數指定的長度"
    assert TITLE_MAX <= 100, "常數本身不得超過 YouTube 上限"
    assert "已截斷" in caplog.text


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("A Chip In Space?\nJensen Huang Speaks", "A Chip In Space? Jensen Huang Speaks"),
        ("Chips < 2nm > Now!", "Chips 2nm Now!"),
        ("  lots   of\tspace  ", "lots of space"),
    ],
)
def test_build_title_sanitizes_for_youtube(test_logger, caplog, raw, expected):
    """換行與 < > 必須清掉。舊標題來自 RSS 不會有這些字元,現在是 LLM 自由
    生成,而 YouTube Data API 對標題含 < > 會回 400 invalidTitle。"""
    with caplog.at_level(logging.WARNING):
        title = build_title("H", raw, "f", test_logger)
    assert title == f"H - {expected}"
    assert "\n" not in title and "<" not in title and ">" not in title


def test_build_title_handles_null_fallback(test_logger, caplog):
    """JSON 的 "title": null 會讓 .get 回 None — 不可產生 "H - None"。

    爆款標題與 fallback 都取不到時,尾端留空是刻意的:檔名標題由呼叫端
    (youtube_upload)負責,這裡只保證不崩、不產生 "None" 字樣。
    """
    with caplog.at_level(logging.WARNING):
        title = build_title("H", None, None, test_logger)
    assert title == "H - "
    assert "尾段為空" in caplog.text


@pytest.mark.parametrize("raw", ["<>", "< >", ">> <", "\n<\n", "  <  >  "])
def test_build_title_falls_back_when_sanitized_to_empty(test_logger, caplog, raw):
    """R1(2026-10-08 reviewer):整串只有 < > 或空白 → 淨化後成空字串。

    舊版把「有沒有值」判斷放在淨化**之前**,於是 viral="<>" 被判定有值、
    fallback 被跳過,最後產出尾端懸空的 "H - " 直接上傳 YouTube。
    先淨化再判斷,這個輸入就必須回頭用 fallback。
    """
    with caplog.at_level(logging.WARNING):
        title = build_title("H", raw, "Real Fallback News", test_logger)
    assert title == "H - Real Fallback News"
    assert "退回原始新聞標題" in caplog.text


# ---- build_title:備援短標題(2026-10-08)-----------------------------------
# Gemini 為前三名各寫三條:video_title(≤60)/ video_title_short(≤40,同一篇的
# 較短寫法)/ shorts_title(≤50)。爆款標題為了鉤人常寫得長,加上頻道前綴就超過
# TITLE_MAX,而硬切砍掉的往往正是最有力的字尾 — 有一條短的備援就能**整條換掉**
# 而不是腰斬。


def test_build_title_uses_short_backup_when_too_long(test_logger, caplog):
    """塞不下時換整條短的,而不是截斷。"""
    head = "2026-10-08 Embedded Linux Daily"
    viral = "y" * 80  # 31 + 3 + 80 = 114 > 95
    with caplog.at_level(logging.WARNING):
        title = build_title(head, viral, "fallback", test_logger, "Short Backup!")
    assert title == f"{head} - Short Backup!"
    assert "已改用較短的備援標題" in caplog.text
    assert "已截斷" not in caplog.text


def test_build_title_ignores_short_backup_that_still_overflows(test_logger, caplog):
    """備援也塞不進上限(Gemini 沒照 ≤40 寫)→ 維持原標題走截斷。

    不做「退而求其次換一條還是超長」的二次替換:那會產生一條**更長**的標題,
    而使用者從 log 看不出換過。
    """
    head = "H" * 40
    viral = "v" * 80  # 123 > 95
    short = "s" * 60  # candidate 103 > 95
    with caplog.at_level(logging.WARNING):
        title = build_title(head, viral, "f", test_logger, short)
    assert len(title) == TITLE_MAX
    assert "已改用較短的備援標題" not in caplog.text
    assert "已截斷" in caplog.text


def test_build_title_never_uses_short_backup_for_the_fallback_path(
    test_logger, caplog
):
    """退回原始新聞標題時,備援短標題也不得頂替。

    備援是**同一篇**的另一種寫法;fallback 是**別篇**(去重改選到前三名之外、
    或 Gemini 沒回)。混用等於把 A 的標題掛到 B 的影片上 — 正是 2026-08-14
    那次「FIT 標題 + ELBE news」的同型錯誤。
    """
    with caplog.at_level(logging.WARNING):
        title = build_title("H", None, "f" * 200, test_logger, "Some Short Backup!")
    assert len(title) == TITLE_MAX
    assert "Some Short Backup!" not in title
    assert "退回原始新聞標題" in caplog.text
    assert "已截斷" in caplog.text


@pytest.mark.parametrize("bad", [None, 123, ["x"], {"a": 1}, "<>", "   "])
def test_build_title_tolerates_bad_short_backup(test_logger, caplog, bad):
    """型別錯誤或淨化後為空 → 照舊截斷,不崩也不產生 "H - None"。

    與 viral 同一道防線:型別判斷只能有一份(clean_headline),呼叫端不需
    為了淨化先做一次型別檢查。
    """
    with caplog.at_level(logging.WARNING):
        title = build_title("H", "v" * 300, "f", test_logger, bad)
    assert len(title) == TITLE_MAX
    assert "None" not in title
