"""Tests for migration from legacy flat layout to profile-based structure."""

import json
import os
from unittest.mock import patch

import pytest

from notebooklm.migration import ensure_profiles_dir, migrate_to_profiles
from notebooklm.paths import _reset_config_cache, set_active_profile


@pytest.fixture(autouse=True)
def _reset_profile_state():
    """Reset module-level profile state between tests."""
    set_active_profile(None)
    _reset_config_cache()
    yield
    set_active_profile(None)
    _reset_config_cache()


def _clean_env():
    env = os.environ.copy()
    env.pop("NOTEBOOKLM_HOME", None)
    env.pop("NOTEBOOKLM_PROFILE", None)
    return env


class TestMigrateToProfiles:
    def test_already_migrated(self, tmp_path):
        """Returns False if profiles/ already exists."""
        (tmp_path / "profiles" / "default").mkdir(parents=True)
        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": str(tmp_path)}, clear=True):
            assert migrate_to_profiles() is False

    def test_fresh_install(self, tmp_path):
        """Creates profiles/ dir on fresh install (no legacy files)."""
        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": str(tmp_path)}, clear=True):
            assert migrate_to_profiles() is True
            assert (tmp_path / "profiles").exists()

    def test_migrates_legacy_files(self, tmp_path):
        """Moves legacy files into profiles/default/."""
        # Create legacy layout
        (tmp_path / "storage_state.json").write_text('{"cookies":[]}')
        (tmp_path / "context.json").write_text('{"notebook_id":"nb1"}')
        (tmp_path / "browser_profile").mkdir()
        (tmp_path / "browser_profile" / "data").write_text("chrome data")

        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": str(tmp_path)}, clear=True):
            assert migrate_to_profiles() is True

        default_dir = tmp_path / "profiles" / "default"
        # Files moved to profile dir
        assert (default_dir / "storage_state.json").exists()
        assert json.loads((default_dir / "storage_state.json").read_text()) == {"cookies": []}
        assert (default_dir / "context.json").exists()
        assert (default_dir / "browser_profile" / "data").exists()
        # Migration marker present
        assert (default_dir / ".migration_complete").exists()
        # Originals removed
        assert not (tmp_path / "storage_state.json").exists()
        assert not (tmp_path / "context.json").exists()
        assert not (tmp_path / "browser_profile").exists()

    def test_updates_config(self, tmp_path):
        """Sets default_profile in config.json."""
        (tmp_path / "storage_state.json").write_text('{"cookies":[]}')

        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": str(tmp_path)}, clear=True):
            migrate_to_profiles()

        config = json.loads((tmp_path / "config.json").read_text())
        assert config["default_profile"] == "default"

    def test_preserves_existing_config(self, tmp_path):
        """Preserves existing config.json values during migration."""
        (tmp_path / "storage_state.json").write_text('{"cookies":[]}')
        (tmp_path / "config.json").write_text(json.dumps({"language": "ja"}))

        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": str(tmp_path)}, clear=True):
            migrate_to_profiles()

        config = json.loads((tmp_path / "config.json").read_text())
        assert config["language"] == "ja"
        assert config["default_profile"] == "default"

    def test_idempotent(self, tmp_path):
        """Running twice is safe — second call is no-op."""
        (tmp_path / "storage_state.json").write_text('{"cookies":[]}')

        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": str(tmp_path)}, clear=True):
            assert migrate_to_profiles() is True
            assert migrate_to_profiles() is False  # Already migrated

    def test_partial_legacy(self, tmp_path):
        """Works with only some legacy files present."""
        (tmp_path / "storage_state.json").write_text('{"cookies":[]}')
        # No context.json or browser_profile

        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": str(tmp_path)}, clear=True):
            assert migrate_to_profiles() is True

        default_dir = tmp_path / "profiles" / "default"
        assert (default_dir / "storage_state.json").exists()
        assert not (default_dir / "context.json").exists()


class TestEnsureProfilesDir:
    def test_creates_on_first_run(self, tmp_path):
        """Creates profiles directory on first invocation."""
        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": str(tmp_path)}, clear=True):
            ensure_profiles_dir()
            assert (tmp_path / "profiles").exists()

    def test_noop_when_exists(self, tmp_path):
        """Does nothing if profiles/ already exists."""
        (tmp_path / "profiles").mkdir()
        with patch.dict(os.environ, {"NOTEBOOKLM_HOME": str(tmp_path)}, clear=True):
            ensure_profiles_dir()  # Should not raise
            assert (tmp_path / "profiles").exists()
