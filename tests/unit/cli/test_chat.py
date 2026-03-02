"""Tests for chat CLI commands (save-as-note, enhanced history)."""

import json
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
        conversation_id="a1b2c3d4-0000-0000-0000-000000000001",
        turn_number=1,
        is_follow_up=False,
        references=[],
        raw_response="",
    )


# get_history returns list of (conversation_id, [(question, answer), ...])
MOCK_CONV_ID = "conv-abc123"
MOCK_QA_PAIRS = [
    ("What is ML?", "ML is a type of AI."),
    ("Explain AI", "AI stands for Artificial Intelligence."),
]
MOCK_HISTORY = [(MOCK_CONV_ID, MOCK_QA_PAIRS)]


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
            mock_client.chat.get_last_conversation_id = AsyncMock(return_value=None)
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
            mock_client.chat.get_last_conversation_id = AsyncMock(return_value=None)
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
            mock_client.chat.get_last_conversation_id = AsyncMock(return_value=None)
            mock_client.notes.create = AsyncMock(return_value=make_note())
            mock_client_cls.return_value = mock_client

            with patch("notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["ask", "What is 42?", "-n", "nb_123"])

            assert result.exit_code == 0, result.output
            mock_client.notes.create.assert_not_awaited()


class TestHistoryCommand:
    def test_history_shows_qa_pairs(self, runner, mock_auth):
        with patch_client_for_module("chat") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.chat.get_history = AsyncMock(return_value=MOCK_HISTORY)
            mock_client_cls.return_value = mock_client

            with patch("notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["history", "-n", "nb_123"])

            assert result.exit_code == 0, result.output
            assert "What is ML?" in result.output
            assert "Explain AI" in result.output

    def test_history_save_creates_note(self, runner, mock_auth):
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

    def test_history_empty_shows_message(self, runner, mock_auth):
        with patch_client_for_module("chat") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.chat.get_history = AsyncMock(return_value=[])
            mock_client_cls.return_value = mock_client

            with patch("notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["history", "-n", "nb_123"])

            assert result.exit_code == 0, result.output
            assert "No conversation history" in result.output

    def test_history_json_outputs_valid_json(self, runner, mock_auth):
        with patch_client_for_module("chat") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.chat.get_history = AsyncMock(return_value=MOCK_HISTORY)
            mock_client_cls.return_value = mock_client

            with patch("notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["history", "--json", "-n", "nb_123"])

            assert result.exit_code == 0, result.output
            import json

            data = json.loads(result.output)
            assert data["notebook_id"] == "nb_123"
            assert len(data["conversations"]) == 1
            conv = data["conversations"][0]
            assert conv["conversation_id"] == MOCK_CONV_ID
            assert conv["count"] == 2
            assert conv["qa_pairs"][0]["turn"] == 1
            assert conv["qa_pairs"][0]["question"] == "What is ML?"
            assert conv["qa_pairs"][0]["answer"] == "ML is a type of AI."
            assert conv["qa_pairs"][1]["turn"] == 2

    def test_history_json_empty(self, runner, mock_auth):
        with patch_client_for_module("chat") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.chat.get_history = AsyncMock(return_value=[])
            mock_client_cls.return_value = mock_client

            with patch("notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["history", "--json", "-n", "nb_123"])

            assert result.exit_code == 0, result.output
            import json

            data = json.loads(result.output)
            assert data["conversations"] == []

    def test_history_show_all_outputs_full_text(self, runner, mock_auth):
        long_q = "Q" * 100
        long_a = "A" * 100
        pairs = [(long_q, long_a)]

        with patch_client_for_module("chat") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.chat.get_history = AsyncMock(return_value=[(MOCK_CONV_ID, pairs)])
            mock_client_cls.return_value = mock_client

            with patch("notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["history", "--show-all", "-n", "nb_123"])

            assert result.exit_code == 0, result.output
            # Rich may wrap long lines, so strip newlines and check full content
            flat = result.output.replace("\n", "")
            assert long_q in flat
            assert long_a in flat


class TestAskExchangeIdPersistence:
    def test_ask_cmd_saves_exchange_id_to_context(self, runner, mock_auth, tmp_path):
        """ask command should persist exchange_id from result into context.json."""
        context_file = tmp_path / "context.json"
        context_file.write_text('{"notebook_id": "nb_123"}')

        ask_result = AskResult(
            answer="The answer.",
            conversation_id="conv-uuid-123",
            turn_number=1,
            is_follow_up=False,
            references=[],
            raw_response="",
            exchange_id="exch-uuid-456",
        )

        with patch_client_for_module("chat") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.chat.ask = AsyncMock(return_value=ask_result)
            mock_client.chat.get_last_conversation_id = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            with (
                patch("notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock) as mock_fetch,
                patch("notebooklm.cli.helpers.get_context_path", return_value=context_file),
            ):
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["ask", "-n", "nb_123", "test question"])

        assert result.exit_code == 0, result.output
        ctx = json.loads(context_file.read_text())
        assert ctx.get("exchange_id") == "exch-uuid-456"

    def test_ask_cmd_clears_exchange_id_on_new_conversation(self, runner, mock_auth, tmp_path):
        """--new flag should clear exchange_id from context."""
        context_file = tmp_path / "context.json"
        context_file.write_text(
            '{"notebook_id": "nb_123", "exchange_id": "old-exch-id", "conversation_id": "old-conv"}'
        )

        ask_result = AskResult(
            answer="Fresh answer.",
            conversation_id="conv-new-123",
            turn_number=1,
            is_follow_up=False,
            references=[],
            raw_response="",
            exchange_id="new-exch-uuid",
        )

        with patch_client_for_module("chat") as mock_client_cls:
            mock_client = create_mock_client()
            mock_client.chat.ask = AsyncMock(return_value=ask_result)
            mock_client_cls.return_value = mock_client

            with (
                patch("notebooklm.cli.helpers.fetch_tokens", new_callable=AsyncMock) as mock_fetch,
                patch("notebooklm.cli.helpers.get_context_path", return_value=context_file),
            ):
                mock_fetch.return_value = ("csrf", "session")
                result = runner.invoke(cli, ["ask", "-n", "nb_123", "--new", "fresh question"])

        assert result.exit_code == 0, result.output
        ctx = json.loads(context_file.read_text())
        # After --new, exchange_id should be the NEW one from the response
        assert ctx.get("exchange_id") == "new-exch-uuid"
