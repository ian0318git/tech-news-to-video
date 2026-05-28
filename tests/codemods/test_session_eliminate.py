from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.codemods.session_eliminate import iter_python_files, main, run, transform_source


def _apply(source: str) -> str:
    rewritten, unsupported = transform_source(source)
    assert unsupported == []
    return rewritten


def test_helper_imports_and_session_type_annotations() -> None:
    source = """\
from _helpers.session_factory import build_session_for_tests
from notebooklm._session import Session


def make() -> Session:
    core: Session = build_session_for_tests(auth)
    return core
"""
    assert _apply(source) == """\
from _helpers.client_factory import build_client_shell_for_tests
from notebooklm.client import NotebookLMClient


def make() -> NotebookLMClient:
    core: NotebookLMClient = build_client_shell_for_tests(auth)
    return core
"""


def test_direct_session_chains_are_receiver_parametric() -> None:
    source = """\
mock_client._session._rpc_executor.rpc_call = fake
client_rel._session._lifecycle._keepalive_storage_path
client_abs._session._kernel.get_http_client()
client._session.auth
client._session.cookie_persistence
"""
    assert _apply(source) == """\
mock_client._rpc_executor.rpc_call = fake
client_rel._collaborators.lifecycle._keepalive_storage_path
client_abs._collaborators.kernel.get_http_client()
client._auth
client._collaborators.cookie_persistence
"""


def test_session_alias_and_tracked_shell_reaches() -> None:
    source = """\
from _helpers.client_factory import build_client_for_tests

client = build_client_for_tests(auth)
core = client._session
core._kernel
core._transport
core.auth
core.is_open
await core.drain(timeout=1)
"""
    assert _apply(source) == """\
from _helpers.client_factory import build_client_shell_for_tests

client = build_client_shell_for_tests(auth)
core = client
core._collaborators.kernel
core._composed.transport
core._auth
core._collaborators.lifecycle.is_open()
await core._collaborators.drain_tracker.drain(timeout=1)
"""


def test_existing_client_shell_holders_remain_unchanged() -> None:
    source = """\
from _helpers.client_factory import build_client_for_tests

core = build_client_for_tests(auth)
core._collaborators.kernel
core._composed.transport
"""
    assert _apply(source) == """\
from _helpers.client_factory import build_client_shell_for_tests

core = build_client_shell_for_tests(auth)
core._collaborators.kernel
core._composed.transport
"""


def test_unrelated_core_names_are_not_rewritten() -> None:
    source = """\
class Fake:
    pass

core = Fake()
core._kernel
core.auth
"""
    assert _apply(source) == source


def test_string_targets_move_to_surviving_modules() -> None:
    source = """\
monkeypatch.setattr("notebooklm._session.asyncio.sleep", fake_sleep)
monkeypatch.setattr("notebooklm._session.random.uniform", fake_random)
patch("notebooklm._session.httpx.AsyncClient", factory)
"""
    assert _apply(source) == """\
monkeypatch.setattr("notebooklm._session_helpers.asyncio.sleep", fake_sleep)
monkeypatch.setattr("notebooklm._backoff._random.uniform", fake_random)
patch("notebooklm._session_init.httpx.AsyncClient", factory)
"""


def test_remaining_session_open_reports_unsupported() -> None:
    _, unsupported = transform_source('monkeypatch.setattr("notebooklm._session.Session.open", fn)\n')
    assert [(item.line, item.pattern_kind) for item in unsupported] == [
        (1, "session_attribute"),
        (1, "deleted_module_string"),
        (1, "session_type_name"),
    ]


def test_iter_python_files_and_run_apply(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    py_file = package / "test_target.py"
    py_file.write_text(
        "from _helpers.session_factory import build_session_for_tests\n"
        "core = build_session_for_tests(auth)\n"
        "core._kernel\n",
        encoding="utf-8",
    )
    ignored = package / "notes.txt"
    ignored.write_text("build_session_for_tests\n", encoding="utf-8")

    assert iter_python_files([package, ignored]) == [py_file]
    changed, unsupported = run([package], apply=True)

    assert changed is True
    assert unsupported == []
    assert py_file.read_text(encoding="utf-8") == (
        "from _helpers.client_factory import build_client_shell_for_tests\n"
        "core = build_client_shell_for_tests(auth)\n"
        "core._collaborators.kernel\n"
    )


def test_run_check_reports_changes_without_writing(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    source = "from _helpers.client_factory import build_client_for_tests\n"
    target.write_text(source, encoding="utf-8")

    changed, unsupported = run([target], apply=False)

    assert changed is True
    assert unsupported == []
    assert target.read_text(encoding="utf-8") == source


def test_main_check_apply_and_report(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    report = tmp_path / "report.json"
    target.write_text("from _helpers.client_factory import build_client_for_tests\n", encoding="utf-8")

    assert main([str(target), "--check", "--report", str(report)]) == 1
    assert report.read_text(encoding="utf-8") == "[]\n"

    assert main([str(target), "--apply", "--report", str(report)]) == 0
    assert "build_client_shell_for_tests" in target.read_text(encoding="utf-8")


def test_main_reports_unsupported_patterns(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    report = tmp_path / "report.json"
    target.write_text("client._session.open()\n", encoding="utf-8")

    assert main([str(target), "--check", "--report", str(report)]) == 2
    assert "session_attribute" in report.read_text(encoding="utf-8")


def test_main_requires_exactly_one_mode(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    target.write_text("", encoding="utf-8")

    with pytest.raises(SystemExit):
        main([str(target)])
