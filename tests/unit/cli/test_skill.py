"""Tests for skill CLI commands."""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from notebooklm.notebooklm_cli import cli

from .conftest import get_cli_module

# Get the actual skill module (not the click group that shadows it)
skill_module = get_cli_module("skill")


@pytest.fixture
def runner():
    return CliRunner()


class TestSkillInstall:
    """Tests for skill install command."""

    def test_skill_install_creates_all_default_targets(self, runner, tmp_path):
        """Test that install writes both supported user targets by default."""
        home = tmp_path / "home"
        mock_source_content = "---\nname: notebooklm\n---\n# Test"

        with (
            patch.object(
                skill_module, "get_skill_source_content", return_value=mock_source_content
            ),
            patch.object(skill_module.Path, "home", return_value=home),
        ):
            result = runner.invoke(cli, ["skill", "install"])

        assert result.exit_code == 0
        assert "installed" in result.output.lower()
        assert (home / ".claude" / "skills" / "notebooklm" / "SKILL.md").exists()
        assert (home / ".agents" / "skills" / "notebooklm" / "SKILL.md").exists()

    def test_skill_install_project_agents_target_only(self, runner, tmp_path):
        """Test project-scope installs into the universal .agents path only."""
        home = tmp_path / "home"
        project = tmp_path / "project"
        mock_source_content = "---\nname: notebooklm\n---\n# Test"

        with (
            patch.object(
                skill_module, "get_skill_source_content", return_value=mock_source_content
            ),
            patch.object(skill_module.Path, "home", return_value=home),
            patch.object(skill_module.Path, "cwd", return_value=project),
        ):
            result = runner.invoke(
                cli, ["skill", "install", "--scope", "project", "--target", "agents"]
            )

        assert result.exit_code == 0
        assert (project / ".agents" / "skills" / "notebooklm" / "SKILL.md").exists()
        assert not (project / ".claude" / "skills" / "notebooklm" / "SKILL.md").exists()

    def test_skill_install_source_not_found(self, runner, tmp_path):
        """Test error when source file doesn't exist."""
        with patch.object(skill_module, "get_skill_source_content", return_value=None):
            result = runner.invoke(cli, ["skill", "install"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestSkillStatus:
    """Tests for skill status command."""

    def test_skill_status_not_installed(self, runner, tmp_path):
        """Test status when skill is not installed."""
        home = tmp_path / "home"

        with patch.object(skill_module.Path, "home", return_value=home):
            result = runner.invoke(cli, ["skill", "status"])

        assert result.exit_code == 0
        assert "not installed" in result.output.lower()
        assert "claude code" in result.output.lower()
        assert "agent skills" in result.output.lower()

    def test_skill_status_installed(self, runner, tmp_path):
        """Test status when skill is installed."""
        home = tmp_path / "home"
        skill_dest = home / ".agents" / "skills" / "notebooklm" / "SKILL.md"
        skill_dest.parent.mkdir(parents=True)
        skill_dest.write_text("<!-- notebooklm-py v0.1.0 -->\n# Test")

        with patch.object(skill_module.Path, "home", return_value=home):
            result = runner.invoke(cli, ["skill", "status"])

        assert result.exit_code == 0
        assert "installed" in result.output.lower()
        assert "version mismatch" in result.output.lower()


class TestSkillUninstall:
    """Tests for skill uninstall command."""

    def test_skill_uninstall_removes_selected_target_only(self, runner, tmp_path):
        """Test that uninstall removes only the requested target."""
        home = tmp_path / "home"
        skill_dest = home / ".agents" / "skills" / "notebooklm" / "SKILL.md"
        other_dest = home / ".claude" / "skills" / "notebooklm" / "SKILL.md"
        skill_dest.parent.mkdir(parents=True)
        skill_dest.write_text("# Test")
        other_dest.parent.mkdir(parents=True)
        other_dest.write_text("# Test")

        with patch.object(skill_module.Path, "home", return_value=home):
            result = runner.invoke(cli, ["skill", "uninstall", "--target", "agents"])

        assert result.exit_code == 0
        assert not skill_dest.exists()
        assert other_dest.exists()

    def test_skill_uninstall_not_installed(self, runner, tmp_path):
        """Test uninstall when skill doesn't exist."""
        home = tmp_path / "home"

        with patch.object(skill_module.Path, "home", return_value=home):
            result = runner.invoke(cli, ["skill", "uninstall"])

        assert result.exit_code == 0
        assert "not installed" in result.output.lower()


class TestSkillShow:
    """Tests for skill show command."""

    def test_skill_show_displays_source_content(self, runner):
        """Test that show defaults to the packaged skill source."""
        with patch.object(
            skill_module, "get_skill_source_content", return_value="# NotebookLM Skill\nTest content"
        ):
            result = runner.invoke(cli, ["skill", "show"])

        assert result.exit_code == 0
        assert "NotebookLM Skill" in result.output

    def test_skill_show_installed_target(self, runner, tmp_path):
        """Test that show can read an installed target."""
        home = tmp_path / "home"
        skill_dest = home / ".claude" / "skills" / "notebooklm" / "SKILL.md"
        skill_dest.parent.mkdir(parents=True)
        skill_dest.write_text("# NotebookLM Skill\nInstalled content")

        with patch.object(skill_module.Path, "home", return_value=home):
            result = runner.invoke(cli, ["skill", "show", "--target", "claude"])

        assert result.exit_code == 0
        assert "Installed content" in result.output

    def test_skill_show_target_not_installed(self, runner, tmp_path):
        """Test show when an installed target doesn't exist."""
        home = tmp_path / "home"

        with patch.object(skill_module.Path, "home", return_value=home):
            result = runner.invoke(cli, ["skill", "show", "--target", "claude"])

        assert result.exit_code == 0
        assert "not installed" in result.output.lower()


class TestSkillVersionExtraction:
    """Tests for version extraction logic."""

    def test_get_skill_version_extracts_version(self, tmp_path):
        """Test version extraction from skill file."""
        from notebooklm.cli.skill import get_skill_version

        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("---\nname: test\n---\n<!-- notebooklm-py v1.2.3 -->\n# Test")

        version = get_skill_version(skill_file)
        assert version == "1.2.3"

    def test_get_skill_version_no_version(self, tmp_path):
        """Test version extraction when no version present."""
        from notebooklm.cli.skill import get_skill_version

        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("# Test\nNo version here")

        version = get_skill_version(skill_file)
        assert version is None

    def test_get_skill_version_file_not_exists(self, tmp_path):
        """Test version extraction when file doesn't exist."""
        from notebooklm.cli.skill import get_skill_version

        skill_file = tmp_path / "nonexistent.md"
        version = get_skill_version(skill_file)
        assert version is None


class TestSkillSourceFallback:
    """Tests for resolving the canonical repository skill."""

    def test_get_skill_source_content_falls_back_to_repo_root(self, tmp_path):
        """Test reading the repo root SKILL.md when package data is unavailable."""
        repo_skill = tmp_path / "SKILL.md"
        repo_skill.write_text("# Canonical Skill", encoding="utf-8")

        with (
            patch.object(skill_module, "REPO_SKILL_SOURCE", repo_skill),
            patch.object(skill_module.resources, "files", side_effect=FileNotFoundError),
        ):
            assert skill_module.get_skill_source_content() == "# Canonical Skill"
