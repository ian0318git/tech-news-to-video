"""YouTube 權杖管理與頻道統計純函式測試(不連網)。

重點行為:
- 非互動環境缺權杖必須**快速失敗**,不可啟動 device flow 空等
  (2026-08-14 實測:cron 下空等 2 小時直到 expired_token,整日癱瘓)。
- 唯讀權杖與上傳權杖分離,且唯讀 scope 不得帶任何寫入權限。
"""

import io
import json
import logging
import time

import httpx
import pytest
from _youtube import TOKEN_PATH, ensure_token
from _youtube_read import READ_SCOPE, READ_TOKEN_PATH
from channel_stats import compute_summary

SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
AUTH_SCRIPT = "youtube_read_auth.py"


def _logger():
    return logging.getLogger("test_youtube_token")


def _expired_token_file(path, **overrides):
    payload = {
        "access_token": "OLD",
        "expires_at": time.time() - 10,
        "refresh_token": "RT",
        "client_id": "cid",
        "client_secret": "cs",
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- 權杖讀取 / 快速失敗 ---------------------------------------------------


def test_missing_token_in_noninteractive_fails_without_device_flow(tmp_path, monkeypatch):
    """cron 環境缺權杖 → 立即失敗,絕不啟動 device flow。"""
    monkeypatch.setattr("sys.stdin", io.StringIO())  # isatty() -> False
    started = []
    monkeypatch.setattr("_youtube.device_auth", lambda *a, **k: started.append(1))

    with pytest.raises(SystemExit):
        ensure_token(_logger(), tmp_path / "absent.json", SCOPE, AUTH_SCRIPT)

    assert not started, "非互動環境不應啟動 device flow"


def test_valid_token_returns_cached_value(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(
        json.dumps({"access_token": "AT", "expires_at": time.time() + 3600}),
        encoding="utf-8",
    )
    assert ensure_token(_logger(), path, SCOPE, AUTH_SCRIPT) == "AT"


def test_corrupt_token_fails(tmp_path):
    path = tmp_path / "t.json"
    path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(SystemExit):
        ensure_token(_logger(), path, SCOPE, AUTH_SCRIPT)


def test_missing_refresh_token_fails(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(
        json.dumps({"access_token": "AT", "expires_at": time.time() - 10}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        ensure_token(_logger(), path, SCOPE, AUTH_SCRIPT)


# --- refresh 路徑 ----------------------------------------------------------


def test_refresh_success_persists_new_token(tmp_path, monkeypatch):
    path = _expired_token_file(tmp_path / "t.json")

    class _Resp:
        status_code = 200

        def json(self):
            return {"access_token": "NEW", "expires_in": 3600}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())

    assert ensure_token(_logger(), path, SCOPE, AUTH_SCRIPT) == "NEW"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["access_token"] == "NEW"
    assert saved["expires_at"] > time.time()
    assert saved["refresh_token"] == "RT", "refresh_token 必須保留"


def test_refresh_auth_error_clears_dead_token(tmp_path, monkeypatch):
    """400 級錯誤代表 refresh token 已死 → 清除該檔,不留死權杖。"""
    path = _expired_token_file(tmp_path / "t.json")

    class _Resp:
        status_code = 400
        text = "invalid_grant"

        def json(self):
            return {}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())

    with pytest.raises(SystemExit):
        ensure_token(_logger(), path, SCOPE, AUTH_SCRIPT)
    assert not path.exists(), "400 級錯誤應清除死權杖"


def test_refresh_server_error_keeps_token(tmp_path, monkeypatch):
    """5xx 是暫時性錯誤 → 保留權杖,不清除。"""
    path = _expired_token_file(tmp_path / "t.json")

    class _Resp:
        status_code = 503
        text = "backend error"

        def json(self):
            return {}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    monkeypatch.setattr("time.sleep", lambda _s: None)

    with pytest.raises(SystemExit):
        ensure_token(_logger(), path, SCOPE, AUTH_SCRIPT)
    assert path.exists(), "暫時性錯誤不應清除權杖"


# --- 唯讀權杖的隔離與最小權限 ----------------------------------------------


def test_read_token_path_is_separate_from_upload_token():
    """唯讀權杖獨立成檔 — 壞了不影響生產中的上傳權杖。"""
    assert READ_TOKEN_PATH != TOKEN_PATH
    assert READ_TOKEN_PATH.name == "youtube_read_token.json"


def test_read_scope_grants_no_write_access():
    """唯讀 scope 不得帶任何寫入能力。"""
    assert READ_SCOPE.endswith("youtube.readonly")
    for write_scope in ("upload", "force-ssl", "youtubepartner"):
        assert write_scope not in READ_SCOPE


# --- compute_summary -------------------------------------------------------


def test_compute_summary_empty():
    assert compute_summary([]) == {
        "count": 0,
        "total": 0,
        "median": 0,
        "mean": 0,
        "max": 0,
        "min": 0,
    }


def test_compute_summary_even_count_median():
    videos = [{"views": v} for v in (10, 20, 30, 100)]
    summary = compute_summary(videos)
    assert summary["count"] == 4
    assert summary["total"] == 160
    assert summary["median"] == 25  # (20 + 30) / 2
    assert summary["mean"] == 40
    assert summary["max"] == 100
    assert summary["min"] == 10


def test_compute_summary_odd_count_median():
    videos = [{"views": v} for v in (5, 1, 100)]
    assert compute_summary(videos)["median"] == 5
