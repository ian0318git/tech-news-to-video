"""Unit tests for the notebook MCP tools.

Drives each tool through the in-memory FastMCP ``Client`` against a server bound
to the mocked ``NotebookLMClient`` (the ``mcp_call`` fixture), asserting the
serialized ``structured_content``. Covers the happy path, name-vs-id resolution
reaching the tool, the confirm preview-then-delete flow, and error projection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest

# Skip cleanly when the `mcp` extra (fastmcp) is absent; see conftest.py.
pytest.importorskip("fastmcp")

from fastmcp.exceptions import ToolError  # noqa: E402 - after importorskip guard

from notebooklm.exceptions import NotebookNotFoundError  # noqa: E402 - after importorskip guard

from .conftest import AsyncMock  # noqa: E402 - after importorskip guard


@dataclass
class FakeNotebook:
    id: str
    title: str


@dataclass
class FakeNotebookFull:
    """A create-result-shaped notebook carrying the metadata the GET re-read adds.

    Mirrors the real :class:`notebooklm.types.Notebook` field set so
    ``to_jsonable`` emits the full flat shape (including ``created_at`` /
    ``modified_at``) the #1699 enrichment surfaces.
    """

    id: str
    title: str
    created_at: datetime | None = None
    sources_count: int = 0
    is_owner: bool = True
    modified_at: datetime | None = None


@dataclass
class FakeDescription:
    summary: str


NB_ID = "11111111-1111-1111-1111-111111111111"
NB2_ID = "22222222-2222-2222-2222-222222222222"
CREATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
MODIFIED_AT = datetime(2026, 1, 3, 4, 5, 6, tzinfo=timezone.utc)


async def test_notebook_list(mcp_call, mock_client) -> None:
    mock_client.notebooks.list = AsyncMock(return_value=[FakeNotebook(id=NB_ID, title="Research")])
    result = await mcp_call("notebook_list")
    assert result.structured_content == {"notebooks": [{"id": NB_ID, "title": "Research"}]}
    mock_client.notebooks.list.assert_awaited_once_with()


async def test_notebook_create_backfills_only_timestamps(mcp_call, mock_client) -> None:
    """CREATE_NOTEBOOK returns null timestamps (#1699); a single GET re-read
    backfills ONLY created_at/modified_at.

    The create result is authoritative for id/title/sources_count/is_owner — the
    re-read overwrites just the two timestamp keys, so a divergent GET payload
    cannot clobber the rest. The flat shape (#1540) exposes the id as
    ``notebook_id``.
    """
    mock_client.notebooks.create = AsyncMock(
        return_value=FakeNotebookFull(id=NB_ID, title="New", sources_count=0, is_owner=True)
    )
    # GET intentionally diverges on the non-timestamp fields to prove only the
    # two timestamp keys are taken from it.
    mock_client.notebooks.get = AsyncMock(
        return_value=FakeNotebookFull(
            id=NB_ID,
            title="Stale",
            created_at=CREATED_AT,
            sources_count=9,
            is_owner=False,
            modified_at=MODIFIED_AT,
        )
    )
    result = await mcp_call("notebook_create", {"title": "New"})
    assert result.structured_content == {
        "notebook_id": NB_ID,
        "title": "New",  # from create, NOT the divergent GET
        "created_at": CREATED_AT.isoformat(),  # backfilled from GET
        "sources_count": 0,  # from create
        "is_owner": True,  # from create
        "modified_at": MODIFIED_AT.isoformat(),  # backfilled from GET
    }
    mock_client.notebooks.create.assert_awaited_once_with("New")
    # The caller needs no follow-up: exactly one internal GET re-read by id.
    mock_client.notebooks.get.assert_awaited_once_with(NB_ID)


async def test_notebook_create_reread_failure_falls_back(mcp_call, mock_client) -> None:
    """A failed GET re-read must not fail a successful create (#1699).

    The create already committed server-side, so the cosmetic timestamp backfill
    is best-effort: when ``get`` raises (e.g. an eventual-consistency
    NotebookNotFoundError), the tool returns the create result with its
    still-null timestamps instead of propagating the error.
    """
    mock_client.notebooks.create = AsyncMock(return_value=FakeNotebookFull(id=NB_ID, title="New"))
    mock_client.notebooks.get = AsyncMock(side_effect=NotebookNotFoundError(NB_ID))
    result = await mcp_call("notebook_create", {"title": "New"})
    assert result.structured_content == {
        "notebook_id": NB_ID,
        "title": "New",
        "created_at": None,
        "sources_count": 0,
        "is_owner": True,
        "modified_at": None,
    }
    mock_client.notebooks.create.assert_awaited_once_with("New")
    mock_client.notebooks.get.assert_awaited_once_with(NB_ID)


async def test_notebook_create_reread_still_null_stays_null(mcp_call, mock_client) -> None:
    """Best-effort: if GET itself returns still-null timestamps (propagation
    lag), the output stays null — no worse than today, no error (#1699).
    """
    mock_client.notebooks.create = AsyncMock(return_value=FakeNotebookFull(id=NB_ID, title="New"))
    mock_client.notebooks.get = AsyncMock(return_value=FakeNotebookFull(id=NB_ID, title="New"))
    result = await mcp_call("notebook_create", {"title": "New"})
    assert result.structured_content == {
        "notebook_id": NB_ID,
        "title": "New",
        "created_at": None,
        "sources_count": 0,
        "is_owner": True,
        "modified_at": None,
    }
    mock_client.notebooks.create.assert_awaited_once_with("New")
    mock_client.notebooks.get.assert_awaited_once_with(NB_ID)


async def test_notebook_create_skips_reread_when_timestamps_present(mcp_call, mock_client) -> None:
    """No wasted RPC: when CREATE already returns both timestamps, the tool
    skips the GET re-read entirely (#1699).
    """
    mock_client.notebooks.create = AsyncMock(
        return_value=FakeNotebookFull(
            id=NB_ID, title="New", created_at=CREATED_AT, modified_at=MODIFIED_AT
        )
    )
    mock_client.notebooks.get = AsyncMock()
    result = await mcp_call("notebook_create", {"title": "New"})
    assert result.structured_content == {
        "notebook_id": NB_ID,
        "title": "New",
        "created_at": CREATED_AT.isoformat(),
        "sources_count": 0,
        "is_owner": True,
        "modified_at": MODIFIED_AT.isoformat(),
    }
    mock_client.notebooks.create.assert_awaited_once_with("New")
    mock_client.notebooks.get.assert_not_awaited()


async def test_notebook_create_reread_backfills_missing_without_regressing(
    mcp_call, mock_client
) -> None:
    """Per-key, non-null backfill in a single mixed re-read (#1699).

    The create result carries ``created_at`` but not ``modified_at`` (one null
    slot fires the re-read guard). The re-read then LAGS — it returns null for
    the already-populated ``created_at`` while finally supplying
    ``modified_at``. The populated ``created_at`` must be preserved (the lagging
    null must NOT regress it), and the missing ``modified_at`` must be
    backfilled from the re-read — both in the same call, no exception.
    """
    mock_client.notebooks.create = AsyncMock(
        return_value=FakeNotebookFull(
            id=NB_ID, title="New", created_at=CREATED_AT, modified_at=None
        )
    )
    mock_client.notebooks.get = AsyncMock(
        return_value=FakeNotebookFull(
            id=NB_ID, title="New", created_at=None, modified_at=MODIFIED_AT
        )
    )
    result = await mcp_call("notebook_create", {"title": "New"})
    assert result.structured_content == {
        "notebook_id": NB_ID,
        "title": "New",
        "created_at": CREATED_AT.isoformat(),  # kept; re-read's lagging null ignored
        "sources_count": 0,
        "is_owner": True,
        "modified_at": MODIFIED_AT.isoformat(),  # backfilled from the re-read
    }
    mock_client.notebooks.create.assert_awaited_once_with("New")
    mock_client.notebooks.get.assert_awaited_once_with(NB_ID)


async def test_notebook_create_skips_reread_when_id_empty(mcp_call, mock_client) -> None:
    """No ``get("")`` on a degenerate empty-id create result (#1699).

    ``Notebook.from_api_response`` can parse a malformed row to an empty id; the
    re-read guard requires a non-empty id, so such a create skips the follow-up
    entirely rather than issuing a meaningless ``get("")``.
    """
    mock_client.notebooks.create = AsyncMock(return_value=FakeNotebookFull(id="", title="New"))
    mock_client.notebooks.get = AsyncMock()
    result = await mcp_call("notebook_create", {"title": "New"})
    assert result.structured_content == {
        "notebook_id": "",
        "title": "New",
        "created_at": None,
        "sources_count": 0,
        "is_owner": True,
        "modified_at": None,
    }
    mock_client.notebooks.create.assert_awaited_once_with("New")
    mock_client.notebooks.get.assert_not_awaited()


async def test_notebook_describe_by_id(mcp_call, mock_client) -> None:
    mock_client.notebooks.get_description = AsyncMock(
        return_value=FakeDescription(summary="A summary")
    )
    result = await mcp_call("notebook_describe", {"notebook": NB_ID})
    assert result.structured_content == {
        "notebook_id": NB_ID,
        "description": {"summary": "A summary"},
    }
    mock_client.notebooks.get_description.assert_awaited_once_with(NB_ID)


async def test_notebook_describe_resolves_by_name(mcp_call, mock_client) -> None:
    """A non-id ``notebook`` ref resolves by exact title before the executor runs."""
    mock_client.notebooks.list = AsyncMock(
        return_value=[FakeNotebook(id=NB_ID, title="My Notebook")]
    )
    mock_client.notebooks.get_description = AsyncMock(return_value=FakeDescription(summary="s"))
    result = await mcp_call("notebook_describe", {"notebook": "My Notebook"})
    assert result.structured_content["notebook_id"] == NB_ID
    mock_client.notebooks.get_description.assert_awaited_once_with(NB_ID)


async def test_notebook_rename(mcp_call, mock_client) -> None:
    mock_client.notebooks.rename = AsyncMock(return_value=None)
    result = await mcp_call("notebook_rename", {"notebook": NB_ID, "new_title": "Renamed"})
    assert result.structured_content == {"notebook_id": NB_ID, "new_title": "Renamed"}
    mock_client.notebooks.rename.assert_awaited_once_with(NB_ID, "Renamed")


async def test_notebook_delete_without_confirm_previews(mcp_call, mock_client) -> None:
    """confirm=False returns a needs_confirmation preview and does NOT delete."""
    mock_client.notebooks.list = AsyncMock(return_value=[FakeNotebook(id=NB_ID, title="Doomed")])
    mock_client.notebooks.delete = AsyncMock(return_value=None)
    result = await mcp_call("notebook_delete", {"notebook": NB_ID})
    assert result.structured_content == {
        "status": "needs_confirmation",
        "preview": {"action": "delete_notebook", "notebook_id": NB_ID, "title": "Doomed"},
    }
    mock_client.notebooks.delete.assert_not_called()


async def test_notebook_delete_with_confirm_deletes(mcp_call, mock_client) -> None:
    mock_client.notebooks.delete = AsyncMock(return_value=None)
    result = await mcp_call("notebook_delete", {"notebook": NB_ID, "confirm": True})
    assert result.structured_content == {"status": "deleted", "notebook_id": NB_ID}
    mock_client.notebooks.delete.assert_awaited_once_with(NB_ID)


async def test_notebook_delete_confirm_preview_then_delete(mcp_call, mock_client) -> None:
    """Two-step flow: preview first, then the confirmed delete runs."""
    mock_client.notebooks.list = AsyncMock(return_value=[FakeNotebook(id=NB2_ID, title="Target")])
    mock_client.notebooks.delete = AsyncMock(return_value=None)

    preview = await mcp_call("notebook_delete", {"notebook": "Target"})
    assert preview.structured_content["status"] == "needs_confirmation"
    assert preview.structured_content["preview"]["notebook_id"] == NB2_ID
    mock_client.notebooks.delete.assert_not_called()

    confirmed = await mcp_call("notebook_delete", {"notebook": "Target", "confirm": True})
    assert confirmed.structured_content == {"status": "deleted", "notebook_id": NB2_ID}
    mock_client.notebooks.delete.assert_awaited_once_with(NB2_ID)


async def test_notebook_describe_not_found_projects_tool_error(mcp_call, mock_client) -> None:
    def _raise(*_a: Any, **_k: Any) -> Any:
        raise NotebookNotFoundError(NB_ID)

    mock_client.notebooks.get_description = AsyncMock(side_effect=_raise)
    with pytest.raises(ToolError) as excinfo:
        await mcp_call("notebook_describe", {"notebook": NB_ID})
    assert "NOT_FOUND" in str(excinfo.value)
