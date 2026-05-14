"""Mind-map RPC primitives shared by ``ArtifactsAPI`` and ``NotesAPI``.

Mind maps live in the "notes" backend (same ``GET_NOTES_AND_MIND_MAPS`` /
``CREATE_NOTE`` / ``UPDATE_NOTE`` / ``DELETE_NOTE`` RPCs as user notes) but
they are AI-generated artifacts from the caller's perspective. This module
hosts the low-level RPC primitives so neither :class:`NotesAPI` nor
:class:`ArtifactsAPI` needs to import the other.

Functions here take a :class:`ClientCore` as their first argument and return
raw RPC-shaped data (lists). Higher-level dataclass parsing stays in
:mod:`_notes` / :mod:`_artifacts`.
"""

from __future__ import annotations

import logging
from typing import Any

from ._core import ClientCore
from .rpc import RPCMethod
from .types import Note

logger = logging.getLogger(__name__)


async def fetch_all_notes_and_mind_maps(core: ClientCore, notebook_id: str) -> list[Any]:
    """Fetch all notes + mind maps in a notebook.

    Both notes and mind maps share the same backend collection; callers
    filter by content shape (``"children":`` / ``"nodes":`` for mind maps).

    Args:
        core: The shared :class:`ClientCore`.
        notebook_id: The notebook ID to query.

    Returns:
        Raw list of items; each item is a list whose first element is the
        item ID. Empty list on missing/unexpected payloads.
    """
    params = [notebook_id]
    result = await core.rpc_call(
        RPCMethod.GET_NOTES_AND_MIND_MAPS,
        params,
        source_path=f"/notebook/{notebook_id}",
        allow_null=True,
    )
    if result and isinstance(result, list) and len(result) > 0 and isinstance(result[0], list):
        notes_list = result[0]
        valid_notes = []
        for item in notes_list:
            if isinstance(item, list) and len(item) > 0 and isinstance(item[0], str):
                valid_notes.append(item)
        return valid_notes
    return []


def is_deleted(item: list[Any]) -> bool:
    """Return True if a note/mind-map item is soft-deleted (``status=2``).

    Deleted items have structure ``['id', None, 2]`` — content is None and
    the third position holds the status sentinel.
    """
    if not isinstance(item, list) or len(item) < 3:
        return False
    return item[1] is None and item[2] == 2


def extract_content(item: list[Any]) -> str | None:
    """Extract the content string from a note/mind-map item.

    Handles both the legacy ``[id, content]`` and the current
    ``[id, [id, content, metadata, None, title]]`` shapes.
    """
    if len(item) <= 1:
        return None

    if isinstance(item[1], str):
        return item[1]
    if isinstance(item[1], list) and len(item[1]) > 1 and isinstance(item[1][1], str):
        return item[1][1]
    return None


async def list_mind_maps(core: ClientCore, notebook_id: str) -> list[Any]:
    """List raw mind-map items in a notebook (excluding soft-deleted ones).

    Mind maps are stored in the same collection as notes but contain JSON
    data with ``"children"`` or ``"nodes"`` keys. This function filters that
    collection down to mind-map-shaped entries.

    Args:
        core: The shared :class:`ClientCore`.
        notebook_id: The notebook ID to query.

    Returns:
        List of raw mind-map items (each a list, first element is the ID).
    """
    all_items = await fetch_all_notes_and_mind_maps(core, notebook_id)
    mind_maps: list[Any] = []
    for item in all_items:
        if is_deleted(item):
            continue
        content = extract_content(item)
        if content and ('"children":' in content or '"nodes":' in content):
            mind_maps.append(item)
    return mind_maps


async def update_note(
    core: ClientCore,
    notebook_id: str,
    note_id: str,
    content: str,
    title: str,
) -> None:
    """Update a note/mind-map row's content and title in place."""
    logger.debug("Updating note %s in notebook %s", note_id, notebook_id)
    params = [
        notebook_id,
        note_id,
        [[[content, title, [], 0]]],
    ]
    await core.rpc_call(
        RPCMethod.UPDATE_NOTE,
        params,
        source_path=f"/notebook/{notebook_id}",
        allow_null=True,
    )


async def create_note(
    core: ClientCore,
    notebook_id: str,
    title: str = "New Note",
    content: str = "",
) -> Note:
    """Create a note (or mind-map row) and set its content + title.

    Google's ``CREATE_NOTE`` ignores the title param, so this function
    always follows up with an ``UPDATE_NOTE`` to set both content and
    title. Returns a :class:`Note` dataclass for consistency with the
    higher-level :class:`NotesAPI`.

    Args:
        core: The shared :class:`ClientCore`.
        notebook_id: The notebook ID to create the note in.
        title: The desired title.
        content: The desired body content (mind-map JSON, plain text, ...).

    Returns:
        The created :class:`Note` with the assigned ID.
    """
    logger.debug("Creating note in notebook %s: %s", notebook_id, title)
    # The server currently ignores this slot (the follow-up UPDATE_NOTE is
    # what actually persists the title) but pass the caller-supplied title
    # rather than a hardcoded literal so the wire payload reflects the user
    # intent if Google ever starts honoring it.
    params = [notebook_id, "", [1], None, title]
    result = await core.rpc_call(
        RPCMethod.CREATE_NOTE,
        params,
        source_path=f"/notebook/{notebook_id}",
    )

    note_id: str | None = None
    if result and isinstance(result, list) and len(result) > 0:
        if isinstance(result[0], list) and len(result[0]) > 0:
            note_id = result[0][0]
        elif isinstance(result[0], str):
            note_id = result[0]

    if note_id:
        # CREATE_NOTE ignores the title param server-side, so set it via
        # UPDATE_NOTE alongside the actual content payload.
        await update_note(core, notebook_id, note_id, content, title)

    return Note(
        id=note_id or "",
        notebook_id=notebook_id,
        title=title,
        content=content,
    )
