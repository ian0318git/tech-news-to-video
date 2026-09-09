"""_common 的參數解析與頻道解析單元測試。"""

import json
import logging
import re
from pathlib import Path

import pytest
from _common import flag_value, resolve_channel, today_str


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
