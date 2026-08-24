"""編排器測試: 用 FakeCli 注入,驗證流程順序、跳過邏輯與新鮮度閘門。

不需要真實 NotebookLM — 這就是候選 4 的 seam 價值。
"""

import json
import logging

import pytest
from _orchestrator import run_video_flow

TODAY = "2026-08-07"


@pytest.fixture(autouse=True)
def _auto_delete_on(monkeypatch):
    """固定開關為啟用,避免宿主環境變數影響測試結果。"""
    monkeypatch.setenv("AUTO_DELETE_NOTEBOOKS", "true")


def _write_state(tmp_path, date=TODAY, nb_id="nb-1", title="Test"):
    """在 cdir 寫 pipeline_state.json(ensure_notebook 會寫的形狀)。"""
    (tmp_path / "pipeline_state.json").write_text(
        json.dumps({"date": date, "notebook_id": nb_id, "title": title}),
        encoding="utf-8",
    )


class FakeCli:
    """記錄呼叫順序的假 CLI;download_video 會建立檔案讓流程通過。"""

    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.calls = []
        self.fail_on_delete = False

    def notebook_delete(self, nb_id, logger):
        self.calls.append(("notebook_delete", nb_id))
        if self.fail_on_delete:
            raise SystemExit(1)

    def ensure_notebook(self, cdir, today, title, state_name, logger):
        self.calls.append(("ensure_notebook", title, state_name))
        return "nb-1"

    def sync_sources(self, urls, logger):
        self.calls.append(("sync_sources", urls))

    def generate_video(
        self, desc, fmt, logger, timeout=1800, style=None, style_prompt=None
    ):
        self.calls.append(("generate_video", fmt, timeout, style))
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


def test_zero_byte_video_regenerates(tmp_path, logger):
    """零位元殘骸(下載中斷)→ 刪除重跑,不被當成完成品。"""
    video = tmp_path / f"video_{TODAY}.mp4"
    video.write_bytes(b"")
    cli = FakeCli(tmp_path)
    result = _flow(tmp_path, cli)
    assert next(c[0] for c in cli.calls) == "ensure_notebook"  # 有重跑
    assert result.exists() and result.stat().st_size > 0


def test_stale_top1_with_existing_video_skips(tmp_path, logger):
    """影片已存在 → 直接跳過,不因 top1 過期而硬失敗(冪等優先)。"""
    video = tmp_path / f"video_{TODAY}.mp4"
    video.write_bytes(b"x" * 100)
    cli = FakeCli(tmp_path)
    result = _flow(tmp_path, cli, top1_date="2026-08-06")
    assert result == video
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
    first = cli.calls[0]
    gen = cli.calls[2]
    assert first[2] == "pipeline_state.json"
    assert gen[1] == "explainer"
    assert gen[2] == 1800


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


# ---------- 自動刪除 notebook hook ----------


def test_happy_path_deletes_notebook(tmp_path, logger):
    """影片下載成功且 state 記錄今天 → 刪除該 notebook,且在 download 之後。"""
    _write_state(tmp_path)
    cli = FakeCli(tmp_path)
    _flow(tmp_path, cli)
    names = [c[0] for c in cli.calls]
    assert names == [
        "ensure_notebook",
        "sync_sources",
        "generate_video",
        "download_video",
        "notebook_delete",
    ]
    assert cli.calls[-1] == ("notebook_delete", "nb-1")


def test_no_state_file_no_delete(tmp_path, logger):
    cli = FakeCli(tmp_path)
    _flow(tmp_path, cli)
    assert all(c[0] != "notebook_delete" for c in cli.calls)


def test_state_missing_notebook_id_no_delete(tmp_path, logger):
    (tmp_path / "pipeline_state.json").write_text(
        json.dumps({"date": TODAY, "title": "t"}), encoding="utf-8"
    )
    cli = FakeCli(tmp_path)
    _flow(tmp_path, cli)
    assert all(c[0] != "notebook_delete" for c in cli.calls)


def test_state_wrong_date_no_delete(tmp_path, logger):
    """state 日期不是今天(舊的殘留)→ 不刪,避免誤刪其他天的專案。"""
    _write_state(tmp_path, date="2026-08-06")
    cli = FakeCli(tmp_path)
    _flow(tmp_path, cli)
    assert all(c[0] != "notebook_delete" for c in cli.calls)


def test_toggle_off_no_delete(tmp_path, logger, monkeypatch):
    monkeypatch.setenv("AUTO_DELETE_NOTEBOOKS", "false")
    _write_state(tmp_path)
    cli = FakeCli(tmp_path)
    _flow(tmp_path, cli)
    assert all(c[0] != "notebook_delete" for c in cli.calls)


def test_delete_failure_does_not_break_flow(tmp_path, logger):
    """刪除失敗(SystemExit)只警告 — 主流程仍成功回傳影片路徑。"""
    _write_state(tmp_path)
    cli = FakeCli(tmp_path)
    cli.fail_on_delete = True
    result = _flow(tmp_path, cli)
    assert result.exists() and result.stat().st_size > 0
    assert cli.calls[-1] == ("notebook_delete", "nb-1")


def test_video_exists_skip_no_delete(tmp_path, logger):
    """影片已存在 → 整個流程(含刪除)都跳過。"""
    video = tmp_path / f"video_{TODAY}.mp4"
    video.write_bytes(b"x" * 100)
    _write_state(tmp_path)
    cli = FakeCli(tmp_path)
    result = _flow(tmp_path, cli)
    assert result == video
    assert cli.calls == []


def test_zero_byte_regenerates_then_deletes(tmp_path, logger):
    """零位元殘骸 → 重跑成功後才刪(刪除絕不在下載成功前發生)。"""
    video = tmp_path / f"video_{TODAY}.mp4"
    video.write_bytes(b"")
    _write_state(tmp_path)
    cli = FakeCli(tmp_path)
    _flow(tmp_path, cli)
    names = [c[0] for c in cli.calls]
    assert names[-1] == "notebook_delete"
    assert names.index("notebook_delete") > names.index("download_video")
