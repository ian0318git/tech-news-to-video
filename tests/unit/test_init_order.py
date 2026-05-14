"""Init-order regression tests for ``ArtifactsAPI`` / ``NotesAPI`` (T6.F).

Before T6.F, :class:`ArtifactsAPI` required ``notes_api=client.notes`` at
construction time, so :class:`NotesAPI` had to be built first. The shared
:mod:`_mind_map` module decouples the two APIs — these tests pin that
invariant down so the load-bearing init order can't silently come back.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from notebooklm._artifacts import ArtifactsAPI
from notebooklm._notes import NotesAPI
from notebooklm.auth import AuthTokens
from notebooklm.client import NotebookLMClient


@pytest.fixture
def mock_auth() -> AuthTokens:
    return AuthTokens(
        cookies={"SID": "test"},
        csrf_token="csrf",
        session_id="session",
    )


def test_client_exposes_artifacts_and_notes(mock_auth: AuthTokens) -> None:
    """The client should construct both APIs regardless of order."""
    client = NotebookLMClient(mock_auth)
    assert isinstance(client.artifacts, ArtifactsAPI)
    assert isinstance(client.notes, NotesAPI)


def test_artifacts_constructible_without_notes_api(mock_auth: AuthTokens) -> None:
    """``ArtifactsAPI`` must be constructible without ``notes_api`` — that is
    the whole point of the T6.F decoupling."""
    core = MagicMock()
    api = ArtifactsAPI(core)
    assert api is not None
    # The legacy private attribute must not leak back: code that depends on
    # ``self._notes`` would re-introduce the coupling.
    assert not hasattr(api, "_notes")


def test_artifacts_accepts_legacy_notes_api_kwarg(mock_auth: AuthTokens) -> None:
    """Existing callers passing ``notes_api=`` must keep working as a no-op
    for the deprecation cycle."""
    core = MagicMock()
    notes = NotesAPI(core)
    api = ArtifactsAPI(core, notes_api=notes)
    assert api is not None
    # Even when supplied, the legacy attribute is intentionally not stored.
    assert not hasattr(api, "_notes")


def test_artifacts_before_notes_construction_order(mock_auth: AuthTokens) -> None:
    """Both construction orders must succeed and produce working APIs."""
    core = MagicMock()
    artifacts_first = ArtifactsAPI(core)
    notes_first = NotesAPI(core)
    # Build in the opposite order too, just to make the symmetry explicit.
    notes_then = NotesAPI(core)
    artifacts_then = ArtifactsAPI(core)
    assert artifacts_first is not None
    assert notes_first is not None
    assert artifacts_then is not None
    assert notes_then is not None


# ---------------------------------------------------------------------------
# Mind-map regression — ``generate_mind_map`` + ``list`` + ``download_mind_map``
# must keep working without an explicit ``NotesAPI`` injection.
# ---------------------------------------------------------------------------


def _make_core_for_mind_map_flow() -> tuple[MagicMock, list[tuple[Any, Any]]]:
    """Build a ``MagicMock`` core whose ``rpc_call`` returns canned mind-map
    responses keyed on the RPC method.

    Returns ``(core, calls)`` where ``calls`` is a list of ``(method, params)``
    tuples populated as the test exercises the API.
    """
    calls: list[tuple[Any, Any]] = []

    mind_map_payload = {
        "name": "Mind Map Title",
        "children": [{"name": "child"}],
    }
    mind_map_json = json.dumps(mind_map_payload)

    async def fake_rpc_call(method: Any, params: Any, **_: Any) -> Any:
        calls.append((method, params))
        name = getattr(method, "name", str(method))
        if name == "GENERATE_MIND_MAP":
            return [[mind_map_json]]
        if name == "CREATE_NOTE":
            return [["note_abc"]]
        if name == "UPDATE_NOTE":
            return None
        if name == "GET_NOTES_AND_MIND_MAPS":
            return [
                [
                    [
                        "note_abc",
                        ["note_abc", mind_map_json, [], None, "Mind Map Title"],
                    ]
                ]
            ]
        if name == "LIST_ARTIFACTS":
            return [[]]
        return None

    core = MagicMock()
    core.rpc_call = AsyncMock(side_effect=fake_rpc_call)
    core.get_source_ids = AsyncMock(return_value=["src_1"])
    return core, calls


@pytest.mark.asyncio
async def test_generate_mind_map_works_without_notes_injection() -> None:
    """``generate_mind_map`` must persist the mind map via ``_mind_map``
    primitives, not via an injected ``NotesAPI``."""
    core, calls = _make_core_for_mind_map_flow()
    api = ArtifactsAPI(core)

    result = await api.generate_mind_map("nb_123", source_ids=["src_1"])

    assert isinstance(result, dict)
    assert result["note_id"] == "note_abc"
    assert result["mind_map"]["name"] == "Mind Map Title"

    # The flow must have gone GENERATE_MIND_MAP -> CREATE_NOTE -> UPDATE_NOTE
    method_names = [getattr(m, "name", str(m)) for m, _ in calls]
    assert "GENERATE_MIND_MAP" in method_names
    assert "CREATE_NOTE" in method_names
    assert "UPDATE_NOTE" in method_names


@pytest.mark.asyncio
async def test_artifacts_list_pulls_mind_maps_without_notes_injection(
    tmp_path: Any,
) -> None:
    """``ArtifactsAPI.list`` must read mind maps through ``_mind_map`` —
    no ``NotesAPI`` reference required."""
    core, _ = _make_core_for_mind_map_flow()
    api = ArtifactsAPI(core)

    artifacts = await api.list("nb_123")
    # One mind map should surface from GET_NOTES_AND_MIND_MAPS.
    assert any(a.kind.name == "MIND_MAP" for a in artifacts)


@pytest.mark.asyncio
async def test_download_mind_map_works_without_notes_injection(
    tmp_path: Any,
) -> None:
    """``download_mind_map`` reaches into mind-map storage via ``_mind_map``
    rather than ``self._notes``."""
    core, _ = _make_core_for_mind_map_flow()
    api = ArtifactsAPI(core)

    output = tmp_path / "mm.json"
    returned = await api.download_mind_map("nb_123", str(output))

    assert returned == str(output)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["name"] == "Mind Map Title"
