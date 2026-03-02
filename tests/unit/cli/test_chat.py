"""Tests for chat CLI commands (save-as-note, enhanced history)."""

from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from notebooklm.notebooklm_cli import cli
from notebooklm.types import AskResult, Note

from .conftest import create_mock_client, patch_client_for_module


def make_note(id="note_abc", title="Chat Note", content="The answer") -> Note:
    return Note(id=id, notebook_id="nb_123", title=title, content=content)


def make_ask_result(answer="The answer is 42.") -> AskResult:
    return AskResult(
        answer=answer,
        conversation_id="conv_abc",
        turn_number=1,
        is_follow_up=False,
        references=[],
        raw_response="",
    )


# Realistic history: history[0] = list of conversations
MOCK_HISTORY = [
    [
        ["conv_001", "What is ML?", "ML is a type of AI.", 1704067200],
        ["conv_002", "Explain AI", "AI stands for Artificial Intelligence.", 1704153600],
    ]
]


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


class TestAskSaveAsNote:
    def test_ask_save_as_note_creates_note(self, runner, mock_auth):
        with patch_client_for_module("chat") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.chat.ask = AsyncMock(return_value=make_ask_result())
            mock_client.chat.get_history = AsyncMock(return_value=None)
            mock_client.notes.create = AsyncMock(return_value=make_note())
            mock_client_cls.return_value = mock_client

            with patch("notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(
                    cli, ["ask", "What is 42?", "--save-as-note", "-n", "nb_123"]
                )

            assert result.exit_code == 0, result.output
            mock_client.notes.create.assert_awaited_once()
            call = mock_client.notes.create.call_args
            all_args = list(call.args) + list(call.kwargs.values())
            assert any("The answer is 42." in str(a) for a in all_args)

    def test_ask_save_as_note_uses_custom_title(self, runner, mock_auth):
        with patch_client_for_module("chat") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.chat.ask = AsyncMock(return_value=make_ask_result())
            mock_client.chat.get_history = AsyncMock(return_value=None)
            mock_client.notes.create = AsyncMock(return_value=make_note(title="My Title"))
            mock_client_cls.return_value = mock_client

            with patch("notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(
                    cli,
                    [
                        "ask",
                        "What is 42?",
                        "--save-as-note",
                        "--note-title",
                        "My Title",
                        "-n",
                        "nb_123",
                    ],
                )

            assert result.exit_code == 0, result.output
            call = mock_client.notes.create.call_args
            all_args = list(call.args) + list(call.kwargs.values())
            assert any("My Title" in str(a) for a in all_args)

    def test_ask_without_flag_does_not_create_note(self, runner, mock_auth):
        with patch_client_for_module("chat") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.chat.ask = AsyncMock(return_value=make_ask_result())
            mock_client.chat.get_history = AsyncMock(return_value=None)
            mock_client.notes.create = AsyncMock(return_value=make_note())
            mock_client_cls.return_value = mock_client

            with patch("notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["ask", "What is 42?", "-n", "nb_123"])

            assert result.exit_code == 0, result.output
            mock_client.notes.create.assert_not_awaited()


class TestHistoryCommand:
    def test_history_shows_conversations_with_previews(self, runner, mock_auth):
        with patch_client_for_module("chat") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.chat.get_history = AsyncMock(return_value=MOCK_HISTORY)
            mock_client_cls.return_value = mock_client

            with patch("notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["history", "-n", "nb_123"])

            assert result.exit_code == 0, result.output
            assert "conv_001" in result.output
            assert "conv_002" in result.output
            assert "What is ML?" in result.output

    def test_history_save_all_creates_note(self, runner, mock_auth):
        with patch_client_for_module("chat") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.chat.get_history = AsyncMock(return_value=MOCK_HISTORY)
            mock_client.notes.create = AsyncMock(return_value=make_note())
            mock_client_cls.return_value = mock_client

            with patch("notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["history", "--save", "-n", "nb_123"])

            assert result.exit_code == 0, result.output
            mock_client.notes.create.assert_awaited_once()

    def test_history_save_by_conversation_id(self, runner, mock_auth):
        with patch_client_for_module("chat") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.chat.get_history = AsyncMock(return_value=MOCK_HISTORY)
            mock_client.notes.create = AsyncMock(return_value=make_note())
            mock_client_cls.return_value = mock_client

            with patch("notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["history", "--save", "-c", "conv_001", "-n", "nb_123"])

            assert result.exit_code == 0, result.output
            mock_client.notes.create.assert_awaited_once()

    def test_history_save_unknown_conversation_id_fails(self, runner, mock_auth):
        with patch_client_for_module("chat") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.chat.get_history = AsyncMock(return_value=MOCK_HISTORY)
            mock_client_cls.return_value = mock_client

            with patch("notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(
                    cli, ["history", "--save", "-c", "conv_unknown", "-n", "nb_123"]
                )

            assert result.exit_code != 0
