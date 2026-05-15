"""CLI integration tests for generate commands.

These tests exercise the full CLI → Client → RPC path using VCR cassettes.
"""

import pytest

from notebooklm.notebooklm_cli import cli

from .conftest import assert_command_success, notebooklm_vcr, skip_no_cassettes

pytestmark = [pytest.mark.vcr, skip_no_cassettes]


class TestGenerateCommands:
    """Test 'notebooklm generate' commands."""

    @pytest.mark.parametrize(
        ("command", "cassette", "extra_args"),
        [
            ("quiz", "artifacts_generate_quiz.yaml", []),
            ("flashcards", "artifacts_generate_flashcards.yaml", []),
            ("report", "artifacts_generate_report.yaml", ["--format", "briefing-doc"]),
            ("report", "artifacts_generate_study_guide.yaml", ["--format", "study-guide"]),
        ],
    )
    def test_generate(self, runner, mock_auth_for_vcr, mock_context, command, cassette, extra_args):
        """Generate commands work with real client."""
        with notebooklm_vcr.use_cassette(cassette):
            result = runner.invoke(cli, ["generate", command, *extra_args])
            assert_command_success(result)

    def test_revise_slide(self, runner, mock_auth_for_vcr, mock_context):
        """revise-slide command sends REVISE_SLIDE RPC with correct args.

        Uses an explicit ``-n <36-char UUID>`` so ``resolve_notebook_id``
        short-circuits (its prefix-resolution path needs ``LIST_NOTEBOOKS``
        which the cassette doesn't carry). The UUID value doesn't have to
        match what was recorded — the VCR matcher only compares path +
        rpcids, not source-path query parameters. The artifact_id is
        likewise passed verbatim through the request body, which the
        matcher ignores.
        """
        with notebooklm_vcr.use_cassette("artifacts_revise_slide.yaml"):
            result = runner.invoke(
                cli,
                [
                    "generate",
                    "revise-slide",
                    "Move the title up",
                    "-n",
                    "00000000-0000-0000-0000-000000000000",
                    "--artifact",
                    "00000000-0000-0000-0000-000000000001",
                    "--slide",
                    "0",
                ],
            )
            assert_command_success(result)
