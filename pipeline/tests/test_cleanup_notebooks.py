"""cleanup_notebooks 掃描測試: state 檔 + done marker 閘門 + dry-run。

直接測 sweep_state_file 的判定邏輯;main() 用 monkeypatch 假頻道/目錄,
不需要真實 NotebookLM(config/channels.json 與 .env 存在即可跑)。
"""

import json
import logging

import _cli
import pytest
from cleanup_notebooks import main, sweep_listed_notebooks, sweep_state_file

TODAY = "2026-08-25"
OLD = "2026-08-01"


@pytest.fixture(autouse=True)
def _auto_delete_on(monkeypatch):
    monkeypatch.setenv("AUTO_DELETE_NOTEBOOKS", "true")


@pytest.fixture
def logger():
    return logging.getLogger("test")


@pytest.fixture
def logs_dir(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    return d


@pytest.fixture
def state_file(tmp_path):
    f = tmp_path / "pipeline_state.json"
    f.write_text(
        json.dumps({"date": OLD, "notebook_id": "nb-old", "title": "Old"}),
        encoding="utf-8",
    )
    return f


@pytest.fixture
def fake_run_cli(monkeypatch):
    calls = []

    def fake(args, logger, timeout=None):
        calls.append(args)
        return "{}"

    monkeypatch.setattr(_cli, "run_cli", fake)
    return calls


def test_old_date_with_marker_deletes(state_file, logs_dir, logger, fake_run_cli):
    (logs_dir / f"done_{OLD}.marker").touch()
    result = sweep_state_file(state_file, TODAY, logs_dir, dry_run=False, logger=logger)
    assert result == "nb-old"
    assert fake_run_cli == [["delete", "-n", "nb-old", "-y", "--json"]]


def test_old_date_no_marker_keeps(state_file, logs_dir, logger, fake_run_cli):
    result = sweep_state_file(state_file, TODAY, logs_dir, dry_run=False, logger=logger)
    assert result is None
    assert fake_run_cli == []


def test_today_keeps(tmp_path, logs_dir, logger, fake_run_cli):
    f = tmp_path / "pipeline_state.json"
    f.write_text(
        json.dumps({"date": TODAY, "notebook_id": "nb-today", "title": "T"}),
        encoding="utf-8",
    )
    (logs_dir / f"done_{TODAY}.marker").touch()
    result = sweep_state_file(f, TODAY, logs_dir, dry_run=False, logger=logger)
    assert result is None
    assert fake_run_cli == []


def test_dry_run_no_delete(state_file, logs_dir, logger, fake_run_cli):
    (logs_dir / f"done_{OLD}.marker").touch()
    result = sweep_state_file(state_file, TODAY, logs_dir, dry_run=True, logger=logger)
    assert result == "nb-old"  # 仍回報「會刪的 id」
    assert fake_run_cli == []


def test_corrupt_state_skips(tmp_path, logs_dir, logger, fake_run_cli):
    f = tmp_path / "pipeline_state.json"
    f.write_text("not json{{{", encoding="utf-8")
    result = sweep_state_file(f, TODAY, logs_dir, dry_run=False, logger=logger)
    assert result is None
    assert fake_run_cli == []


def test_missing_state_skips(tmp_path, logs_dir, logger, fake_run_cli):
    result = sweep_state_file(
        tmp_path / "nope.json", TODAY, logs_dir, dry_run=False, logger=logger
    )
    assert result is None
    assert fake_run_cli == []


def test_delete_failure_continues(state_file, logs_dir, logger, monkeypatch):
    """刪除失敗只警告 — 不回報例外,照常回傳 id。"""
    (logs_dir / f"done_{OLD}.marker").touch()

    def boom(args, logger, timeout=None):
        raise SystemExit(1)

    monkeypatch.setattr(_cli, "run_cli", boom)
    result = sweep_state_file(state_file, TODAY, logs_dir, dry_run=False, logger=logger)
    assert result == "nb-old"


# ---------- 清單掃描(sweep_listed_notebooks) ----------


def _item(nb_id, title):
    return {"id": nb_id, "title": title}


def test_listed_old_with_marker_deletes(logs_dir, logger, fake_run_cli):
    items = [_item("nb-1", "2026-08-01 Embedded Linux Daily - Test")]
    (logs_dir / "done_2026-08-01.marker").touch()
    n = sweep_listed_notebooks(items, TODAY, logs_dir, dry_run=False, logger=logger)
    assert n == 1
    assert fake_run_cli == [["delete", "-n", "nb-1", "-y", "--json"]]


def test_listed_no_marker_keeps(logs_dir, logger, fake_run_cli):
    items = [_item("nb-1", "2026-08-01 Embedded Linux Daily - Test")]
    n = sweep_listed_notebooks(items, TODAY, logs_dir, dry_run=False, logger=logger)
    assert n == 1  # 有比對到,但閘門不放行
    assert fake_run_cli == []


def test_listed_today_keeps(logs_dir, logger, fake_run_cli):
    items = [_item("nb-1", f"{TODAY} Embedded Linux Daily - Test")]
    (logs_dir / f"done_{TODAY}.marker").touch()
    sweep_listed_notebooks(items, TODAY, logs_dir, dry_run=False, logger=logger)
    assert fake_run_cli == []


def test_listed_non_pipeline_title_skipped(logs_dir, logger, fake_run_cli):
    """個人專案(非日期前綴標題)一律不碰。"""
    items = [_item("nb-personal", "NPE-010"), _item("nb-2", "LX2160 project notes")]
    n = sweep_listed_notebooks(items, TODAY, logs_dir, dry_run=False, logger=logger)
    assert n == 0
    assert fake_run_cli == []


def test_listed_dry_run_no_delete(logs_dir, logger, fake_run_cli):
    items = [_item("nb-1", "2026-08-01 TechSnack Daily - X")]
    (logs_dir / "done_2026-08-01.marker").touch()
    n = sweep_listed_notebooks(items, TODAY, logs_dir, dry_run=True, logger=logger)
    assert n == 1
    assert fake_run_cli == []


def test_listed_dedupe_with_seen(logs_dir, logger, fake_run_cli):
    """state 掃描已處理過的 id 不再重複刪。"""
    items = [_item("nb-state", "2026-08-01 Embedded - X")]
    (logs_dir / "done_2026-08-01.marker").touch()
    sweep_listed_notebooks(items, TODAY, logs_dir, dry_run=False, logger=logger, seen_ids={"nb-state"})
    assert fake_run_cli == []


def test_listed_shorts_format_matches(logs_dir, logger, fake_run_cli):
    """Shorts 標題格式(日期在第二位)同樣比對。"""
    items = [_item("nb-s", "Shorts 2026-08-01 - Debates over AI")]
    (logs_dir / "done_2026-08-01.marker").touch()
    n = sweep_listed_notebooks(items, TODAY, logs_dir, dry_run=False, logger=logger)
    assert n == 1
    assert fake_run_cli == [["delete", "-n", "nb-s", "-y", "--json"]]


def test_toggle_off_main_no_op(tmp_path, monkeypatch, logger):
    monkeypatch.setenv("AUTO_DELETE_NOTEBOOKS", "false")
    import cleanup_notebooks as cn

    monkeypatch.setattr(cn, "load_env", lambda: None)
    monkeypatch.setattr(cn, "setup_logging", lambda name: logger)
    monkeypatch.setattr(cn, "load_channels", lambda logger: [{"slug": "embedded"}])
    monkeypatch.setattr(cn, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(cn, "LOGS_DIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir()
    (tmp_path / "pipeline_state.json").write_text(
        json.dumps({"date": OLD, "notebook_id": "nb-x", "title": "x"}),
        encoding="utf-8",
    )
    (tmp_path / "logs" / f"done_{OLD}.marker").touch()

    calls = []
    monkeypatch.setattr(_cli, "run_cli", lambda args, lg, timeout=None: calls.append(args) or "{}")
    monkeypatch.setattr(cn.sys, "argv", ["cleanup_notebooks.py"])
    main()
    assert calls == []  # 開關關閉 → 完全不刪


def test_main_scans_all_channels_and_states(tmp_path, monkeypatch, logger, fake_run_cli):
    """embedded + tech 的 pipeline_state + tech 的 shorts_state → 3 個全刪。"""
    import cleanup_notebooks as cn

    monkeypatch.setattr(cn, "load_env", lambda: None)
    monkeypatch.setattr(cn, "setup_logging", lambda name: logger)
    monkeypatch.setattr(
        cn,
        "load_channels",
        lambda logger: [{"slug": "embedded"}, {"slug": "tech"}],
    )
    monkeypatch.setattr(cn, "OUTPUT_DIR", tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(cn, "LOGS_DIR", logs)
    (logs / f"done_{OLD}.marker").touch()
    for slug in ("embedded", "tech"):
        d = tmp_path / slug
        d.mkdir()
        (d / "pipeline_state.json").write_text(
            json.dumps({"date": OLD, "notebook_id": f"nb-{slug}", "title": slug}),
            encoding="utf-8",
        )
    (tmp_path / "tech" / "shorts_state.json").write_text(
        json.dumps({"date": OLD, "notebook_id": "nb-shorts", "title": "shorts"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(cn, "notebook_list", lambda logger: [])  # 清單掃描不影響本測試
    monkeypatch.setattr(cn.sys, "argv", ["cleanup_notebooks.py"])
    main()
    deleted = [args[2] for args in fake_run_cli]
    assert sorted(deleted) == ["nb-embedded", "nb-shorts", "nb-tech"]
