"""run_daily 的 import 呼叫機制 + 補跑冪等測試(候選 5)。

驗證: argv 注入、exit code 傳播、argv 還原 — 不 spawn 子程序;
以及 fetch/rank/collect 的逐步驟跳過判斷(修正 B+C)。
"""

import importlib
import json
import logging
import sys

import pytest
import run_daily

TODAY = "2026-10-08"


def _make_module(tmp_path, name, body):
    (tmp_path / f"{name}.py").write_text(body, encoding="utf-8")
    return tmp_path


def test_run_invokes_module_main_with_argv(monkeypatch, tmp_path):
    mod_dir = _make_module(
        tmp_path,
        "fake_step_argv",
        "import sys\ncalls = []\ndef main():\n    calls.append(list(sys.argv))\n",
    )
    monkeypatch.syspath_prepend(str(mod_dir))
    run_daily.run("fake_step_argv", ["--channel", "tech"], logging.getLogger("t"))
    assert importlib.import_module("fake_step_argv").calls == [
        ["fake_step_argv.py", "--channel", "tech"]
    ]


def test_run_propagates_nonzero_exit(monkeypatch, tmp_path):
    mod_dir = _make_module(
        tmp_path, "fake_step_fail", "import sys\ndef main():\n    sys.exit(3)\n"
    )
    monkeypatch.syspath_prepend(str(mod_dir))
    with pytest.raises(SystemExit) as exc:
        run_daily.run("fake_step_fail", [], logging.getLogger("t"))
    assert exc.value.code == 3


def test_run_ignores_zero_exit(monkeypatch, tmp_path):
    mod_dir = _make_module(tmp_path, "fake_step_ok", "def main():\n    pass\n")
    monkeypatch.syspath_prepend(str(mod_dir))
    run_daily.run("fake_step_ok", [], logging.getLogger("t"))  # 不拋出


def test_run_restores_argv(monkeypatch, tmp_path):
    mod_dir = _make_module(tmp_path, "fake_step_quiet", "def main():\n    pass\n")
    monkeypatch.syspath_prepend(str(mod_dir))
    monkeypatch.setattr(sys, "argv", ["run_daily.py", "--channel", "tech"])
    run_daily.run("fake_step_quiet", ["--channel", "tech"], logging.getLogger("t"))
    assert sys.argv == ["run_daily.py", "--channel", "tech"]  # 呼叫後還原


# ── 補跑冪等(B+C):cron 一天最多跑 28 輪,每輪都不該重複燒 Gemini 配額 ──


def write_top1(cdir, date=TODAY, title="Chips Drop", news=None):
    payload = {"date": date, "channel": "tech", "news": news or {"title": title}}
    (cdir / "top1.json").write_text(json.dumps(payload), encoding="utf-8")


def write_sources(cdir, topic="Chips Drop", sources=None):
    payload = {"topic": topic, "sources": sources or [{"url": "https://x.test"}]}
    (cdir / "sources.json").write_text(json.dumps(payload), encoding="utf-8")


def test_load_top1_missing_is_empty(tmp_path):
    assert run_daily.load_top1(tmp_path) == {}


@pytest.mark.parametrize("content", ["{not json", "[]", "null", '"a string"'])
def test_load_top1_treats_broken_file_as_absent(tmp_path, content):
    """壞檔當成沒跑過(重跑),不當成已完成(跳過)— 否則補跑會靜默不做任何事。"""
    (tmp_path / "top1.json").write_text(content, encoding="utf-8")
    assert run_daily.load_top1(tmp_path) == {}


def test_locked_topic_requires_todays_date(tmp_path):
    """昨天的 top1.json 不算鎖定 — 否則 rank 今天失敗過一次就再也選不出題。"""
    assert run_daily.locked_topic({"date": "2026-10-07", "news": {"title": "Old"}}, TODAY) == ""
    assert run_daily.locked_topic({"news": {"title": "No date"}}, TODAY) == ""
    assert run_daily.locked_topic({"date": TODAY, "news": {"title": "New"}}, TODAY) == "New"


@pytest.mark.parametrize("news", [None, "flat string", 123, {"title": None}, {"title": ""}])
def test_locked_topic_survives_odd_news_shapes(news):
    """畸形 news 一律視為「沒有鎖定」而不是拋例外(不靜默失敗原則)。"""
    assert run_daily.locked_topic({"date": TODAY, "news": news}, TODAY) == ""


def test_locked_topic_falls_back_to_flat_top1():
    """news 不是 dict 時退回 top1 本身 — 與 collect_sources/build_metadata 同寫法。"""
    assert run_daily.locked_topic({"date": TODAY, "title": "Flat"}, TODAY) == "Flat"


def test_skip_reason_locks_fetch_and_rank_on_todays_topic(tmp_path):
    top1 = {"date": TODAY, "news": {"title": "Chips Drop"}}
    for step in ("fetch", "rank"):
        reason = run_daily.skip_reason(step, tmp_path, TODAY, top1)
        assert reason, f"{step} 應在選題已鎖定時跳過"
        assert "Chips Drop" in reason


def test_skip_reason_runs_everything_without_todays_top1(tmp_path):
    """沒有今天的選題 → 三步都要跑(這是正常的每日首跑)。"""
    for step in ("fetch", "rank", "collect"):
        assert run_daily.skip_reason(step, tmp_path, TODAY, {}) == ""


def test_skip_reason_collect_needs_matching_topic(tmp_path):
    top1 = {"date": TODAY, "news": {"title": "Chips Drop"}}
    assert run_daily.skip_reason("collect", tmp_path, TODAY, top1) == ""  # 還沒有檔案

    write_sources(tmp_path, topic="Chips Drop")
    assert run_daily.skip_reason("collect", tmp_path, TODAY, top1), "同主題的來源應跳過"


def test_skip_reason_collect_reruns_when_topic_changed(tmp_path):
    """top1.json 被換題後,舊 sources.json 是別的題目的來源 → 必須重收。"""
    write_sources(tmp_path, topic="Yesterday's Story")
    top1 = {"date": TODAY, "news": {"title": "Chips Drop"}}
    assert run_daily.skip_reason("collect", tmp_path, TODAY, top1) == ""


@pytest.mark.parametrize("sources", [[], None, "not-a-list"])
def test_sources_ready_rejects_empty_or_broken_source_list(tmp_path, sources):
    """collect_sources 只在有可達來源時才寫檔;空的/壞的一律重跑。"""
    (tmp_path / "sources.json").write_text(
        json.dumps({"topic": "Chips Drop", "sources": sources}), encoding="utf-8"
    )
    assert run_daily.sources_ready(tmp_path, "Chips Drop") is False


def test_sources_ready_rejects_corrupt_file(tmp_path):
    (tmp_path / "sources.json").write_text("{broken", encoding="utf-8")
    assert run_daily.sources_ready(tmp_path, "Chips Drop") is False


def test_skip_reason_ignores_unknown_step(tmp_path):
    """未知步驟一律執行 — 不確定就不要跳過。"""
    assert run_daily.skip_reason("mystery", tmp_path, TODAY, {"date": TODAY}) == ""
