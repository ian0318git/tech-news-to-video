"""run_daily 的 import 呼叫機制測試(候選 5)。

驗證: argv 注入、exit code 傳播、argv 還原 — 不 spawn 子程序。
"""

import importlib
import logging
import sys

import pytest
import run_daily


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
