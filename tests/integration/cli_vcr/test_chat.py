"""CLI integration tests for chat commands.

These tests exercise the full CLI → Client → RPC path using VCR cassettes.
"""

import pytest

from notebooklm.notebooklm_cli import cli

from .conftest import assert_command_success, notebooklm_vcr, parse_json_output, skip_no_cassettes

pytestmark = [pytest.mark.vcr, skip_no_cassettes]


class TestAskCommand:
    """Test 'notebooklm ask' command."""

    @notebooklm_vcr.use_cassette("chat_ask.yaml")
    def test_ask_question(self, runner, mock_auth_for_vcr, mock_context):
        """Ask a question shows response from real client."""
        result = runner.invoke(cli, ["ask", "What is this notebook about?"])
        # allow_no_context=True: cassette may not match mock notebook ID
        assert_command_success(result)

    @notebooklm_vcr.use_cassette("chat_ask.yaml")
    def test_ask_question_json(self, runner, mock_auth_for_vcr, mock_context):
        """Ask with --json flag returns JSON output."""
        result = runner.invoke(cli, ["ask", "--json", "What is this notebook about?"])
        # allow_no_context=True: cassette may not match mock notebook ID
        assert_command_success(result)

        if result.exit_code == 0:
            data = parse_json_output(result.output)
            assert data is not None, "Expected valid JSON output"
            assert isinstance(data, (list, dict))


class TestHistoryCommand:
    """Test 'notebooklm history' command."""

    @notebooklm_vcr.use_cassette("chat_get_history.yaml")
    def test_history(self, runner, mock_auth_for_vcr, mock_context):
        """History command shows chat history with Q&A previews."""
        result = runner.invoke(cli, ["history"])
        assert_command_success(result)

    @pytest.mark.skip(
        reason=(
            "Cassette not yet recorded. To record: set NOTEBOOKLM_VCR_RECORD=1 and run "
            "'notebooklm history --save' against real API. "
            "Cassette must capture GET_CONVERSATION_HISTORY + CREATE_NOTE + UPDATE_NOTE."
        )
    )
    def test_history_save(self, runner, mock_auth_for_vcr, mock_context):
        """'history --save' saves all conversation history as a note."""
        with notebooklm_vcr.use_cassette("chat_history_save.yaml"):
            result = runner.invoke(cli, ["history", "--save"])
            assert result.exit_code == 0
            assert "Saved as note" in result.output

    @pytest.mark.skip(
        reason=(
            "Cassette not yet recorded. To record: set NOTEBOOKLM_VCR_RECORD=1 and run "
            "'notebooklm history --save -c <id>' against real API. "
            "Cassette must capture GET_CONVERSATION_HISTORY + CREATE_NOTE + UPDATE_NOTE."
        )
    )
    def test_history_save_single_conversation(self, runner, mock_auth_for_vcr, mock_context):
        """'history --save -c <id>' saves one conversation as a note."""
        with notebooklm_vcr.use_cassette("chat_history_save_single.yaml"):
            result = runner.invoke(cli, ["history", "--save", "-c", "conv_001"])
            assert result.exit_code == 0
            assert "Saved as note" in result.output


class TestAskSaveAsNoteCommand:
    """Test 'notebooklm ask --save-as-note' command."""

    @pytest.mark.skip(
        reason=(
            "Cassette not yet recorded. To record: set NOTEBOOKLM_VCR_RECORD=1 and run "
            "'notebooklm ask \"...\" --save-as-note' against real API. "
            "Cassette must capture GenerateFreeFormStreamed + CREATE_NOTE + UPDATE_NOTE."
        )
    )
    def test_ask_save_as_note(self, runner, mock_auth_for_vcr, mock_context):
        """'ask --save-as-note' saves the answer as a note."""
        with notebooklm_vcr.use_cassette("chat_ask_save_as_note.yaml"):
            result = runner.invoke(cli, ["ask", "What is this about?", "--save-as-note"])
            assert result.exit_code == 0
            assert "Saved as note" in result.output
