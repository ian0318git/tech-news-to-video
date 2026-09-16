"""channel_stats 頻道解析邏輯測試(不連網)。

關鍵行為: 頻道掛在品牌帳號下時 `channels.list(mine=true)` 會回 items=0
(2026-09-16 實測),必須自動改用 forHandle 重查,不可直接判定失敗。
"""

import pytest
from channel_stats import (
    CHANNEL_HANDLE_ENV,
    DEFAULT_CHANNEL_HANDLE,
    channel_from_item,
    fetch_channel,
    resolve_handle,
)


def _logger():
    import logging

    return logging.getLogger("test_channel_stats")


def _item(channel_id="UC_x", title="TechSnack Daily", **stats):
    base = {"subscriberCount": "15", "viewCount": "1044", "videoCount": "135"}
    base.update({k: str(v) for k, v in stats.items()})
    return {
        "id": channel_id,
        "snippet": {"title": title, "publishedAt": "2026-08-06T00:00:00Z"},
        "statistics": base,
        "contentDetails": {"relatedPlaylists": {"uploads": "UU_x"}},
    }


class _Recorder:
    """假的 api_get: 依 params 回傳預設結果,並記錄呼叫順序。"""

    def __init__(self, by_mine=None, by_handle=None):
        self.by_mine = by_mine if by_mine is not None else []
        self.by_handle = by_handle if by_handle is not None else []
        self.calls = []

    def __call__(self, logger, path, token, **params):
        self.calls.append(params)
        if params.get("mine") == "true":
            return {"items": self.by_mine}
        return {"items": self.by_handle}


# --- channel_from_item -----------------------------------------------------


def test_channel_from_item_maps_fields():
    ch = channel_from_item(_item())
    assert ch == {
        "id": "UC_x",
        "title": "TechSnack Daily",
        "published_at": "2026-08-06T00:00:00Z",
        "subscribers": 15,
        "total_views": 1044,
        "video_count": 135,
        "uploads_playlist": "UU_x",
    }


def test_channel_from_item_missing_stats_defaults_to_zero():
    """statistics 缺欄位(例如隱藏訂閱數)→ 0,不可 KeyError。"""
    item = _item()
    item["statistics"] = {}
    assert channel_from_item(item)["subscribers"] == 0
    assert channel_from_item(item)["total_views"] == 0


# --- fetch_channel: mine=true 優先 -----------------------------------------


def test_fetch_channel_uses_mine_when_available(monkeypatch):
    """一般帳號: mine=true 有結果就用它,不應多打一次 forHandle。"""
    rec = _Recorder(by_mine=[_item()])
    monkeypatch.setattr("channel_stats.api_get", rec)

    ch = fetch_channel(_logger(), "TOKEN")

    assert ch["id"] == "UC_x"
    assert len(rec.calls) == 1
    assert rec.calls[0]["mine"] == "true"


# --- fetch_channel: 品牌帳號 fallback --------------------------------------


def test_fetch_channel_falls_back_to_handle_for_brand_account(monkeypatch):
    """mine=true 回空(品牌帳號)→ 自動改用 forHandle,而不是直接失敗。"""
    rec = _Recorder(by_mine=[], by_handle=[_item(channel_id="UCwg4TBdH_jRxx0bU88t9ZNA")])
    monkeypatch.setattr("channel_stats.api_get", rec)

    ch = fetch_channel(_logger(), "TOKEN")

    assert ch["id"] == "UCwg4TBdH_jRxx0bU88t9ZNA"
    assert len(rec.calls) == 2, "應先試 mine=true 再試 forHandle"
    assert rec.calls[0]["mine"] == "true"
    assert rec.calls[1]["forHandle"] == DEFAULT_CHANNEL_HANDLE
    assert "mine" not in rec.calls[1]


def test_fetch_channel_accepts_explicit_handle(monkeypatch):
    rec = _Recorder(by_mine=[], by_handle=[_item()])
    monkeypatch.setattr("channel_stats.api_get", rec)

    fetch_channel(_logger(), "TOKEN", handle="@other-channel")

    assert rec.calls[1]["forHandle"] == "@other-channel"


def test_fetch_channel_fails_when_both_paths_empty(monkeypatch):
    """兩條路都查不到 → 快速失敗(不靜默回空頻道)。"""
    monkeypatch.setattr("channel_stats.api_get", _Recorder(by_mine=[], by_handle=[]))

    with pytest.raises(SystemExit):
        fetch_channel(_logger(), "TOKEN")


# --- resolve_handle --------------------------------------------------------


def test_resolve_handle_default(monkeypatch):
    monkeypatch.delenv(CHANNEL_HANDLE_ENV, raising=False)
    assert resolve_handle() == DEFAULT_CHANNEL_HANDLE


def test_resolve_handle_env_override(monkeypatch):
    monkeypatch.setenv(CHANNEL_HANDLE_ENV, "@override-me")
    assert resolve_handle() == "@override-me"


def test_resolve_handle_empty_env_falls_back_to_default(monkeypatch):
    """空字串等同未設定 — 否則 forHandle= 會送出無效請求。"""
    monkeypatch.setenv(CHANNEL_HANDLE_ENV, "")
    assert resolve_handle() == DEFAULT_CHANNEL_HANDLE
