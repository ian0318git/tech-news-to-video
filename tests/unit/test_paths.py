"""Tests for path resolution module."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from notebooklm.paths import (
    get_browser_profile_dir,
    get_context_path,
    get_home_dir,
    get_path_info,
    get_storage_path,
)


# Windows clears HOME/USERPROFILE differently, so we need to preserve them
# when testing default path behavior
def _get_env_without_notebooklm_home():
    """Get current env without NOTEBOOKLM_HOME, preserving HOME/USERPROFILE."""
    env = os.environ.copy()
    env.pop("NOTEBOOKLM_HOME", None)
    return env


class TestGetHomeDir:
    def test_default_path(self):
        """Without NOTEBOOKLM_HOME, returns ~/.notebooklm."""
        with patch.dict(os.environ, _get_env_without_notebooklm_home(), clear=True):
            result = get_home_dir()
            assert result == Path.home() / ".notebooklm"

    def test_respects_env_var(self, tmp_path):
        """NOTEBOOKLM_HOME env var overrides default."""
        custom_path = tmp_path / "custom_home"
        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": str(custom_path)}):
            result = get_home_dir()
            assert result == custom_path.resolve()

    def test_expands_tilde(self):
        """Tilde in NOTEBOOKLM_HOME is expanded."""
        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": "~/custom_notebooklm"}):
            result = get_home_dir()
            assert result == (Path.home() / "custom_notebooklm").resolve()

    def test_create_flag_creates_directory(self, tmp_path):
        """create=True creates the directory if it doesn't exist."""
        custom_path = tmp_path / "new_home"
        assert not custom_path.exists()

        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": str(custom_path)}):
            result = get_home_dir(create=True)
            assert result.exists()
            assert result.is_dir()

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Unix permissions not applicable on Windows"
    )
    def test_create_flag_sets_permissions(self, tmp_path):
        """create=True sets directory permissions to 0o700."""
        custom_path = tmp_path / "secure_home"

        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": str(custom_path)}):
            get_home_dir(create=True)
            # Check permissions (on Unix systems)
            mode = custom_path.stat().st_mode & 0o777
            assert mode == 0o700


    def test_windows_create_skips_mode_and_chmod(self, tmp_path, monkeypatch):
        """On Windows, create=True calls mkdir without mode= and skips chmod."""
        import notebooklm.paths as paths_mod

        custom_path = tmp_path / "win_home"
        monkeypatch.setenv("NOTEBOOKLM_HOME", str(custom_path))
        monkeypatch.setattr(paths_mod.sys, "platform", "win32")

        mkdir_calls = []
        chmod_calls = []
        _orig_mkdir = Path.mkdir

        def _track_mkdir(self, *args, **kwargs):
            mkdir_calls.append({"args": args, "kwargs": kwargs})
            return _orig_mkdir(self, *args, **kwargs)

        def _track_chmod(self, *args, **kwargs):
            chmod_calls.append({"args": args, "kwargs": kwargs})

        monkeypatch.setattr(Path, "mkdir", _track_mkdir)
        monkeypatch.setattr(Path, "chmod", _track_chmod)

        get_home_dir(create=True)

        assert custom_path.exists()
        # mkdir should NOT receive mode= kwarg on Windows
        assert len(mkdir_calls) == 1
        assert "mode" not in mkdir_calls[0]["kwargs"]
        # chmod should NOT be called on Windows
        assert len(chmod_calls) == 0

    def test_unix_create_sets_mode_and_chmod(self, tmp_path, monkeypatch):
        """On Unix, create=True passes mode=0o700 to mkdir and calls chmod(0o700)."""
        import notebooklm.paths as paths_mod

        custom_path = tmp_path / "unix_home"
        monkeypatch.setenv("NOTEBOOKLM_HOME", str(custom_path))
        monkeypatch.setattr(paths_mod.sys, "platform", "linux")

        mkdir_calls = []
        chmod_calls = []
        _orig_mkdir = Path.mkdir

        def _track_mkdir(self, *args, **kwargs):
            mkdir_calls.append({"args": args, "kwargs": kwargs})
            return _orig_mkdir(self, *args, **kwargs)

        def _track_chmod(self, *args, **kwargs):
            chmod_calls.append({"args": args, "kwargs": kwargs})

        monkeypatch.setattr(Path, "mkdir", _track_mkdir)
        monkeypatch.setattr(Path, "chmod", _track_chmod)

        get_home_dir(create=True)

        assert custom_path.exists()
        # mkdir should receive mode=0o700 on Unix
        assert len(mkdir_calls) == 1
        assert mkdir_calls[0]["kwargs"].get("mode") == 0o700
        # chmod should be called with 0o700 on Unix
        assert len(chmod_calls) == 1
        assert chmod_calls[0]["args"] == (0o700,)


class TestGetStoragePath:
    def test_default_path(self):
        """Returns storage_state.json in home dir."""
        with patch.dict(os.environ, _get_env_without_notebooklm_home(), clear=True):
            result = get_storage_path()
            assert result == Path.home() / ".notebooklm" / "storage_state.json"

    def test_respects_home_env_var(self, tmp_path):
        """Storage path follows NOTEBOOKLM_HOME."""
        custom_path = tmp_path / "custom_home"
        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": str(custom_path)}):
            result = get_storage_path()
            assert result == custom_path.resolve() / "storage_state.json"


class TestGetContextPath:
    def test_default_path(self):
        """Returns context.json in home dir."""
        with patch.dict(os.environ, _get_env_without_notebooklm_home(), clear=True):
            result = get_context_path()
            assert result == Path.home() / ".notebooklm" / "context.json"

    def test_respects_home_env_var(self, tmp_path):
        """Context path follows NOTEBOOKLM_HOME."""
        custom_path = tmp_path / "custom_home"
        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": str(custom_path)}):
            result = get_context_path()
            assert result == custom_path.resolve() / "context.json"


class TestGetBrowserProfileDir:
    def test_default_path(self):
        """Returns browser_profile in home dir."""
        with patch.dict(os.environ, _get_env_without_notebooklm_home(), clear=True):
            result = get_browser_profile_dir()
            assert result == Path.home() / ".notebooklm" / "browser_profile"

    def test_respects_home_env_var(self, tmp_path):
        """Browser profile follows NOTEBOOKLM_HOME."""
        custom_path = tmp_path / "custom_home"
        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": str(custom_path)}):
            result = get_browser_profile_dir()
            assert result == custom_path.resolve() / "browser_profile"


class TestGetPathInfo:
    def test_default_paths(self):
        """Returns correct info with default paths."""
        with patch.dict(os.environ, _get_env_without_notebooklm_home(), clear=True):
            info = get_path_info()

            assert info["home_source"] == "default (~/.notebooklm)"
            assert ".notebooklm" in info["home_dir"]
            assert "storage_state.json" in info["storage_path"]
            assert "context.json" in info["context_path"]
            assert "browser_profile" in info["browser_profile_dir"]

    def test_custom_home(self, tmp_path):
        """Returns correct info with NOTEBOOKLM_HOME set."""
        custom_path = tmp_path / "custom_home"
        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": str(custom_path)}):
            info = get_path_info()

            assert info["home_source"] == "NOTEBOOKLM_HOME"
            assert str(custom_path.resolve()) in info["home_dir"]
