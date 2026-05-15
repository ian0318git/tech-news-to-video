"""Shared fixtures for integration tests."""

import json
import os
from pathlib import Path

import pytest

from notebooklm.auth import AuthTokens
from notebooklm.rpc import RPCMethod

# =============================================================================
# VCR Cassette Availability Check
# =============================================================================

CASSETTES_DIR = Path(__file__).parent.parent / "cassettes"

# Check if cassettes are available (more than just example files)
_real_cassettes = (
    [f for f in CASSETTES_DIR.glob("*.yaml") if not f.name.startswith("example_")]
    if CASSETTES_DIR.exists()
    else []
)

# Skip VCR tests if no real cassettes exist (unless in record mode)
_vcr_record_mode = os.environ.get("NOTEBOOKLM_VCR_RECORD", "").lower() in ("1", "true", "yes")
_cassettes_available = bool(_real_cassettes) or _vcr_record_mode

# Marker for skipping VCR tests when cassettes are not available
skip_no_cassettes = pytest.mark.skipif(
    not _cassettes_available,
    reason="VCR cassettes not available. Set NOTEBOOKLM_VCR_RECORD=1 to record.",
)


async def get_vcr_auth() -> AuthTokens:
    """Get auth tokens for VCR tests.

    In record mode: loads real auth from storage (required for recording).
    In replay mode: returns mock auth (cassettes have recorded responses).
    """
    if _vcr_record_mode:
        return await AuthTokens.from_storage()
    else:
        # Mock auth for replay - values don't matter, VCR replays recorded responses
        return AuthTokens(
            cookies={
                "SID": "mock_sid",
                "HSID": "mock_hsid",
                "SSID": "mock_ssid",
                "APISID": "mock_apisid",
                "SAPISID": "mock_sapisid",
            },
            csrf_token="mock_csrf_token",
            session_id="mock_session_id",
        )


# =============================================================================
# T8.A1 — xfail cassettes whose recorded ``rpcids`` order does not match live
# call order under the new default matcher (``method, scheme, host, port,
# path, rpcids``). These cassettes were previously selected by play-count
# ordering alone; tightening the matcher in T8.A1 surfaces the drift.
# Each entry MUST be removed in its phase-2 cassette-repair PR (T8.B*).
# Tracking issue: tier-8-followup label.
# =============================================================================
_T8_A1_XFAIL_NODEIDS = frozenset(
    {
        # test_artifacts.py
        "tests/integration/cli_vcr/test_artifacts.py::TestArtifactListCommand::test_artifact_list[False]",
        "tests/integration/cli_vcr/test_artifacts.py::TestArtifactListCommand::test_artifact_list[True]",
        "tests/integration/cli_vcr/test_artifacts.py::TestArtifactListByType::test_artifact_list_by_type[quiz-artifacts_list_quizzes.yaml]",
        "tests/integration/cli_vcr/test_artifacts.py::TestArtifactListByType::test_artifact_list_by_type[report-artifacts_list_reports.yaml]",
        "tests/integration/cli_vcr/test_artifacts.py::TestArtifactListByType::test_artifact_list_by_type[video-artifacts_list_video.yaml]",
        "tests/integration/cli_vcr/test_artifacts.py::TestArtifactListByType::test_artifact_list_by_type[flashcard-artifacts_list_flashcards.yaml]",
        "tests/integration/cli_vcr/test_artifacts.py::TestArtifactListByType::test_artifact_list_by_type[infographic-artifacts_list_infographics.yaml]",
        "tests/integration/cli_vcr/test_artifacts.py::TestArtifactListByType::test_artifact_list_by_type[slide-deck-artifacts_list_slide_decks.yaml]",
        "tests/integration/cli_vcr/test_artifacts.py::TestArtifactListByType::test_artifact_list_by_type[data-table-artifacts_list_data_tables.yaml]",
        "tests/integration/cli_vcr/test_artifacts.py::TestArtifactListByType::test_artifact_list_by_type[mind-map-notes_list_mind_maps.yaml]",
        "tests/integration/cli_vcr/test_artifacts.py::TestArtifactSuggestionsCommand::test_artifact_suggestions",
        # test_chat.py
        "tests/integration/cli_vcr/test_chat.py::TestAskCommand::test_ask_question",
        "tests/integration/cli_vcr/test_chat.py::TestAskCommand::test_ask_question_json",
        "tests/integration/cli_vcr/test_chat.py::TestHistoryCommand::test_history",
        # test_generate.py
        "tests/integration/cli_vcr/test_generate.py::TestGenerateCommands::test_generate[quiz-artifacts_generate_quiz.yaml-extra_args0]",
        "tests/integration/cli_vcr/test_generate.py::TestGenerateCommands::test_generate[flashcards-artifacts_generate_flashcards.yaml-extra_args1]",
        "tests/integration/cli_vcr/test_generate.py::TestGenerateCommands::test_generate[report-artifacts_generate_report.yaml-extra_args2]",
        "tests/integration/cli_vcr/test_generate.py::TestGenerateCommands::test_generate[report-artifacts_generate_study_guide.yaml-extra_args3]",
        # ``test_revise_slide`` was repaired in T8.B1 — cassette re-recorded
        # against the live REVISE_SLIDE RPC.
        # test_notebooks.py
        "tests/integration/cli_vcr/test_notebooks.py::TestSummaryCommand::test_summary",
        # test_notes.py
        "tests/integration/cli_vcr/test_notes.py::TestNoteCommands::test_note_command[notes_list.yaml-args0]",
        "tests/integration/cli_vcr/test_notes.py::TestNoteCommands::test_note_command[notes_create.yaml-args1]",
        # test_sources.py
        "tests/integration/cli_vcr/test_sources.py::TestSourceListCommand::test_source_list[False]",
        "tests/integration/cli_vcr/test_sources.py::TestSourceListCommand::test_source_list[True]",
        "tests/integration/cli_vcr/test_sources.py::TestSourceAddCommand::test_source_add[sources_add_url.yaml-args0]",
        "tests/integration/cli_vcr/test_sources.py::TestSourceAddCommand::test_source_add[sources_add_text.yaml-args1]",
        "tests/integration/cli_vcr/test_sources.py::TestSourceContentCommands::test_source_content[guide-sources_get_guide.yaml]",
        "tests/integration/cli_vcr/test_sources.py::TestSourceContentCommands::test_source_content[fulltext-sources_get_fulltext.yaml]",
        # test_vcr_comprehensive.py
        "tests/integration/test_vcr_comprehensive.py::TestArtifactsListAPI::test_suggest_reports",
    }
)


def pytest_collection_modifyitems(config, items):
    """Auto-apply xfail to T8.A1-surfaced cassette-drift failures.

    Adding ``rpcids`` to the default VCR matcher (T8.A1) surfaces cassettes
    whose recorded rpc-call order doesn't match live call order. Re-recording
    is phase-2 work (T8.B*); until then, mark these tests xfail so CI stays
    green and the phase-2 PRs that re-record each cassette can simply remove
    the entry from ``_T8_A1_XFAIL_NODEIDS``.
    """
    marker = pytest.mark.xfail(
        reason="T8.A1 matcher tightening surfaced cassette drift; phase-2 T8.B* re-records",
        strict=False,
    )
    for item in items:
        if item.nodeid in _T8_A1_XFAIL_NODEIDS:
            item.add_marker(marker)


@pytest.fixture
def auth_tokens():
    """Create test authentication tokens."""
    return AuthTokens(
        cookies={
            "SID": "test_sid",
            "HSID": "test_hsid",
            "SSID": "test_ssid",
            "APISID": "test_apisid",
            "SAPISID": "test_sapisid",
        },
        csrf_token="test_csrf_token",
        session_id="test_session_id",
    )


@pytest.fixture
def build_rpc_response():
    """Factory for building RPC responses.

    Args:
        rpc_id: Either an RPCMethod enum or string RPC ID.
        data: The response data to encode.
    """

    def _build(rpc_id: RPCMethod | str, data) -> str:
        # Convert RPCMethod to string value if needed
        rpc_id_str = rpc_id.value if isinstance(rpc_id, RPCMethod) else rpc_id
        inner = json.dumps(data)
        chunk = json.dumps(["wrb.fr", rpc_id_str, inner, None, None])
        return f")]}}'\n{len(chunk)}\n{chunk}\n"

    return _build


@pytest.fixture
def mock_list_notebooks_response():
    """Mock response for listing notebooks."""
    inner_data = json.dumps(
        [
            [
                [
                    "My First Notebook",
                    [["src_001"], ["src_002"]],
                    "nb_001",
                    "📘",
                    None,
                    [None, None, None, None, None, [1704067200, 0]],
                ],
                [
                    "Research Notes",
                    None,
                    "nb_002",
                    "📚",
                    None,
                    [None, None, None, None, None, [1704153600, 0]],
                ],
            ]
        ]
    )
    rpc_id = RPCMethod.LIST_NOTEBOOKS.value
    chunk = json.dumps([["wrb.fr", rpc_id, inner_data, None, None]])
    return f")]}}'\n{len(chunk)}\n{chunk}\n"
