"""Tests for research CLI commands."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from notebooklm.notebooklm_cli import cli

from .conftest import create_mock_client, patch_client_for_module


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_auth():
    with patch("notebooklm.cli.helpers.load_auth_from_storage") as mock:
        mock.return_value = {
            "SID": "test",
            "HSID": "test",
            "SSID": "test",
            "APISID": "test",
            "SAPISID": "test",
        }
        yield mock


# =============================================================================
# RESEARCH STATUS TESTS
# =============================================================================


class TestResearchStatus:
    def test_status_no_research(self, runner, mock_auth):
        with patch_client_for_module("research") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.research.poll = AsyncMock(return_value={"status": "no_research"})
            mock_client_cls.return_value = mock_client

            with patch(
                "notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["research", "status", "-n", "nb_123"])

            assert result.exit_code == 0
            assert "No research running" in result.output

    def test_status_in_progress(self, runner, mock_auth):
        with patch_client_for_module("research") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.research.poll = AsyncMock(
                return_value={"status": "in_progress", "query": "AI research"}
            )
            mock_client_cls.return_value = mock_client

            with patch(
                "notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["research", "status", "-n", "nb_123"])

            assert result.exit_code == 0
            assert "Research in progress" in result.output
            assert "AI research" in result.output

    def test_status_completed(self, runner, mock_auth):
        with patch_client_for_module("research") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.research.poll = AsyncMock(
                return_value={
                    "status": "completed",
                    "query": "AI research",
                    "sources": [
                        {"title": "Source 1", "url": "http://example.com/1"},
                        {"title": "Source 2", "url": "http://example.com/2"},
                    ],
                    "summary": "This is a summary of the research results.",
                }
            )
            mock_client_cls.return_value = mock_client

            with patch(
                "notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["research", "status", "-n", "nb_123"])

            assert result.exit_code == 0
            assert "Research completed" in result.output
            assert "Found 2 sources" in result.output
            assert "Source 1" in result.output

    def test_status_completed_with_many_sources(self, runner, mock_auth):
        """Test that more than 10 sources shows truncation message."""
        with patch_client_for_module("research") as mock_client_cls:
            mock_client = create_mock_client()
            sources = [{"title": f"Source {i}", "url": f"http://example.com/{i}"} for i in range(15)]
            mock_client.research.poll = AsyncMock(
                return_value={
                    "status": "completed",
                    "query": "AI research",
                    "sources": sources,
                    "summary": "",
                }
            )
            mock_client_cls.return_value = mock_client

            with patch(
                "notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["research", "status", "-n", "nb_123"])

            assert result.exit_code == 0
            assert "Found 15 sources" in result.output
            assert "and 5 more" in result.output

    def test_status_unknown(self, runner, mock_auth):
        with patch_client_for_module("research") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.research.poll = AsyncMock(
                return_value={"status": "unknown_status"}
            )
            mock_client_cls.return_value = mock_client

            with patch(
                "notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["research", "status", "-n", "nb_123"])

            assert result.exit_code == 0
            assert "Status: unknown_status" in result.output

    def test_status_json_output(self, runner, mock_auth):
        with patch_client_for_module("research") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.research.poll = AsyncMock(
                return_value={
                    "status": "completed",
                    "query": "AI research",
                    "sources": [{"title": "Source 1", "url": "http://example.com"}],
                    "summary": "Summary",
                }
            )
            mock_client_cls.return_value = mock_client

            with patch(
                "notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["research", "status", "-n", "nb_123", "--json"])

            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["status"] == "completed"
            assert len(data["sources"]) == 1


# =============================================================================
# RESEARCH WAIT TESTS
# =============================================================================


class TestResearchWait:
    def test_wait_completes(self, runner, mock_auth):
        with patch_client_for_module("research") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.research.poll = AsyncMock(
                return_value={
                    "status": "completed",
                    "task_id": "task_123",
                    "query": "AI research",
                    "sources": [{"title": "Source 1", "url": "http://example.com"}],
                }
            )
            mock_client_cls.return_value = mock_client

            with patch(
                "notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["research", "wait", "-n", "nb_123"])

            assert result.exit_code == 0
            assert "Research completed" in result.output
            assert "Found 1 sources" in result.output

    def test_wait_no_research(self, runner, mock_auth):
        with patch_client_for_module("research") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.research.poll = AsyncMock(return_value={"status": "no_research"})
            mock_client_cls.return_value = mock_client

            with patch(
                "notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["research", "wait", "-n", "nb_123"])

            assert result.exit_code == 1
            assert "No research running" in result.output

    def test_wait_timeout(self, runner, mock_auth):
        with patch_client_for_module("research") as mock_client_cls:
            mock_client = create_mock_client()
            # Always return in_progress to trigger timeout
            mock_client.research.poll = AsyncMock(
                return_value={"status": "in_progress", "query": "AI research"}
            )
            mock_client_cls.return_value = mock_client

            with patch(
                "notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                # Use very short timeout and interval
                result = runner.invoke(
                    cli, ["research", "wait", "-n", "nb_123", "--timeout", "1", "--interval", "1"]
                )

            assert result.exit_code == 1
            assert "Timed out" in result.output

    def test_wait_with_import_all(self, runner, mock_auth):
        with patch_client_for_module("research") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.research.poll = AsyncMock(
                return_value={
                    "status": "completed",
                    "task_id": "task_123",
                    "query": "AI research",
                    "sources": [{"title": "Source 1", "url": "http://example.com"}],
                }
            )
            mock_client.research.import_sources = AsyncMock(
                return_value=[{"id": "src_1", "title": "Source 1"}]
            )
            mock_client_cls.return_value = mock_client

            with patch(
                "notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(
                    cli, ["research", "wait", "-n", "nb_123", "--import-all"]
                )

            assert result.exit_code == 0
            assert "Imported 1 sources" in result.output
            mock_client.research.import_sources.assert_called_once()

    def test_wait_json_output_completed(self, runner, mock_auth):
        with patch_client_for_module("research") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.research.poll = AsyncMock(
                return_value={
                    "status": "completed",
                    "task_id": "task_123",
                    "query": "AI research",
                    "sources": [{"title": "Source 1", "url": "http://example.com"}],
                }
            )
            mock_client_cls.return_value = mock_client

            with patch(
                "notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(
                    cli, ["research", "wait", "-n", "nb_123", "--json"]
                )

            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["status"] == "completed"
            assert data["sources_found"] == 1

    def test_wait_json_output_with_import(self, runner, mock_auth):
        with patch_client_for_module("research") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.research.poll = AsyncMock(
                return_value={
                    "status": "completed",
                    "task_id": "task_123",
                    "query": "AI research",
                    "sources": [{"title": "Source 1", "url": "http://example.com"}],
                }
            )
            mock_client.research.import_sources = AsyncMock(
                return_value=[{"id": "src_1", "title": "Source 1"}]
            )
            mock_client_cls.return_value = mock_client

            with patch(
                "notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(
                    cli, ["research", "wait", "-n", "nb_123", "--json", "--import-all"]
                )

            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["status"] == "completed"
            assert data["imported"] == 1
            assert len(data["imported_sources"]) == 1

    def test_wait_json_no_research(self, runner, mock_auth):
        with patch_client_for_module("research") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.research.poll = AsyncMock(return_value={"status": "no_research"})
            mock_client_cls.return_value = mock_client

            with patch(
                "notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(
                    cli, ["research", "wait", "-n", "nb_123", "--json"]
                )

            assert result.exit_code == 1
            data = json.loads(result.output)
            assert data["status"] == "no_research"
            assert "error" in data

    def test_wait_json_timeout(self, runner, mock_auth):
        with patch_client_for_module("research") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.research.poll = AsyncMock(
                return_value={"status": "in_progress", "query": "AI research"}
            )
            mock_client_cls.return_value = mock_client

            with patch(
                "notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(
                    cli,
                    ["research", "wait", "-n", "nb_123", "--json", "--timeout", "1", "--interval", "1"],
                )

            assert result.exit_code == 1
            data = json.loads(result.output)
            assert data["status"] == "timeout"


# =============================================================================
# COMMAND EXISTENCE TESTS
# =============================================================================


class TestResearchCommandsExist:
    def test_research_group_exists(self, runner):
        result = runner.invoke(cli, ["research", "--help"])
        assert result.exit_code == 0
        assert "Research management commands" in result.output

    def test_research_status_command_exists(self, runner):
        result = runner.invoke(cli, ["research", "status", "--help"])
        assert result.exit_code == 0
        assert "Check research status" in result.output

    def test_research_wait_command_exists(self, runner):
        result = runner.invoke(cli, ["research", "wait", "--help"])
        assert result.exit_code == 0
        assert "Wait for research to complete" in result.output
