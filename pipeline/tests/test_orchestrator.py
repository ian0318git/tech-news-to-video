"""編排器測試: 用 FakeCli 注入,驗證流程順序、跳過邏輯與新鮮度閘門。

不需要真實 NotebookLM — 這就是候選 4 的 seam 價值。
"""

import logging

import pytest
from _orchestrator import run_video_flow

TODAY = "2026-08-07"


class FakeCli:
    """記錄呼叫順序的假 CLI;download_video 會建立檔案讓流程通過。"""

    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.calls = []

    def ensure_notebook(self, cdir, today, title, state_name, logger):
        self.calls.append(("ensure_notebook", title, state_name))
        return "nb-1"

    def sync_sources(self, urls, logger):
        self.calls.append(("sync_sources", urls))

    def generate_video(self, desc, fmt, logger, timeout=1800):
        self.calls.append(("generate_video", fmt, timeout))
        return {"status": "completed"}

    def download_video(self, path, logger):
        self.calls.append(("download_video", str(path)))
        path.write_bytes(b"fake-mp4")  # 讓存在性檢查通過


@pytest.fixture
def logger():
    return logging.getLogger("test")


def _flow(tmp_path, cli, **overrides):
    params = {
        "cdir": tmp_path,
        "today": TODAY,
        "title": "2026-08-07 Test - News",
        "desc": "summarize",
        "fmt": "explainer",
        "filename_pattern": "video_{date}.mp4",
        "state_name": "pipeline_state.json",
        "timeout": 1800,
        "top1_date": TODAY,
        "urls": ["https://example.com/a"],
        "logger": logging.getLogger("test"),
        "cli": cli,
    }
    params.update(overrides)
    return run_video_flow(**params)


def test_video_exists_skips_everything(tmp_path, logger):
    video = tmp_path / f"video_{TODAY}.mp4"
    video.write_bytes(b"x" * 100)
    cli = FakeCli(tmp_path)
    result = _flow(tmp_path, cli)
    assert result == video
    assert cli.calls == []  # 完全沒有 CLI 呼叫


def test_stale_top1_fails(tmp_path, logger):
    cli = FakeCli(tmp_path)
    with pytest.raises(SystemExit):
        _flow(tmp_path, cli, top1_date="2026-08-06")  # 昨天的新聞
    assert cli.calls == []


def test_happy_path_order(tmp_path, logger):
    cli = FakeCli(tmp_path)
    result = _flow(tmp_path, cli)
    assert result.exists()
    assert [c[0] for c in cli.calls] == [
        "ensure_notebook",
        "sync_sources",
        "generate_video",
        "download_video",
    ]
    # 參數檢查
    assert cli.calls[0][2] == "pipeline_state.json"
    assert cli.calls[2][1] == "explainer"
    assert cli.calls[2][2] == 1800


def test_short_variant(tmp_path, logger):
    cli = FakeCli(tmp_path)
    result = _flow(
        tmp_path,
        cli,
        fmt="short",
        filename_pattern="shorts_{date}.mp4",
        state_name="shorts_state.json",
        timeout=3600,
    )
    assert result.name == f"shorts_{TODAY}.mp4"
    assert cli.calls[0][2] == "shorts_state.json"
    assert cli.calls[2][1] == "short"
    assert cli.calls[2][2] == 3600
