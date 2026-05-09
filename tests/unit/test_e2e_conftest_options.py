"""Unit tests for E2E conftest CLI options.

Covers the --profile flag added in issue #339 without spinning up the full
E2E suite (which requires real auth).

The E2E conftest is loaded by file path because `tests/` is not a Python
package (no `__init__.py`), so a normal `from tests.e2e import conftest`
import would fail under pytest.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace

CONFTEST_PATH = Path(__file__).resolve().parents[1] / "e2e" / "conftest.py"


def _load_e2e_conftest() -> ModuleType:
    spec = importlib.util.spec_from_file_location("e2e_conftest", CONFTEST_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_config(profile: str | None) -> SimpleNamespace:
    return SimpleNamespace(getoption=lambda name: profile if name == "--profile" else None)


class TestProfileOptionLifecycle:
    """pytest_configure + pytest_unconfigure round-trip."""

    def test_round_trip_no_prior_env(self, monkeypatch):
        monkeypatch.delenv("NOTEBOOKLM_PROFILE", raising=False)
        conftest = _load_e2e_conftest()
        config = _make_config("work")

        conftest.pytest_configure(config)
        assert os.environ.get("NOTEBOOKLM_PROFILE") == "work"

        conftest.pytest_unconfigure(config)
        assert "NOTEBOOKLM_PROFILE" not in os.environ

    def test_round_trip_restores_prior_env(self, monkeypatch):
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "preset")
        conftest = _load_e2e_conftest()
        config = _make_config("work")

        conftest.pytest_configure(config)
        assert os.environ.get("NOTEBOOKLM_PROFILE") == "work"

        conftest.pytest_unconfigure(config)
        assert os.environ.get("NOTEBOOKLM_PROFILE") == "preset"

    def test_no_flag_with_prior_env_is_noop(self, monkeypatch):
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "preset")
        conftest = _load_e2e_conftest()
        config = _make_config(None)

        conftest.pytest_configure(config)
        conftest.pytest_unconfigure(config)
        assert os.environ.get("NOTEBOOKLM_PROFILE") == "preset"

    def test_no_flag_without_prior_env_is_noop(self, monkeypatch):
        monkeypatch.delenv("NOTEBOOKLM_PROFILE", raising=False)
        conftest = _load_e2e_conftest()
        config = _make_config(None)

        conftest.pytest_configure(config)
        conftest.pytest_unconfigure(config)
        assert "NOTEBOOKLM_PROFILE" not in os.environ


class TestArgvProfile:
    """Parsing of --profile out of argv (used at import time)."""

    def test_long_form(self):
        argv = ["pytest", "--profile", "work", "tests/e2e"]
        assert _load_e2e_conftest()._argv_profile(argv) == "work"

    def test_equals_form(self):
        argv = ["pytest", "--profile=work", "tests/e2e"]
        assert _load_e2e_conftest()._argv_profile(argv) == "work"

    def test_absent(self):
        argv = ["pytest", "tests/e2e", "-m", "e2e"]
        assert _load_e2e_conftest()._argv_profile(argv) is None

    def test_long_form_missing_value_returns_none(self):
        argv = ["pytest", "--profile"]
        assert _load_e2e_conftest()._argv_profile(argv) is None

    def test_last_occurrence_wins(self):
        argv = ["pytest", "--profile", "foo", "--profile", "bar"]
        assert _load_e2e_conftest()._argv_profile(argv) == "bar"

    def test_long_form_rejects_dash_prefixed_value(self):
        argv = ["pytest", "--profile", "--verbose"]
        assert _load_e2e_conftest()._argv_profile(argv) is None
