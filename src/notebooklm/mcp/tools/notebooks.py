"""Notebook MCP tools.

Thin adapters over the transport-neutral ``_app.notebooks`` core: resolve the
notebook reference (name OR id) via the Phase 1 :mod:`._resolve` helper, drive the
``execute_notebook_*`` executors, and project the typed result to the wire with
:func:`to_jsonable`. No business logic lives here.

The ``_app`` rename/describe executors take an injected ``resolve_notebook_id``
callable shaped for the CLI (``(client, ref, *, json_output) -> id``). The MCP
adapter has already resolved the id with :func:`resolve_notebook`, so it passes
the shared :func:`passthrough_notebook_id` resolver, which returns the
already-resolved id unchanged.

This module imports NO ``click`` / ``rich`` / ``cli``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import Context

from ..._app import notebooks as core
from ..._app.serialize import to_jsonable
from .._confirm import DESTRUCTIVE, READ_ONLY, needs_confirmation
from .._context import get_client
from .._errors import mcp_errors
from .._resolve import resolve_notebook
from ._passthrough import passthrough_notebook_id
from ._preview import title_for_id

logger = logging.getLogger(__name__)


def register(mcp: Any) -> None:
    """Register the notebook tools on ``mcp``."""

    @mcp.tool(annotations=READ_ONLY)
    async def notebook_list(ctx: Context) -> dict[str, Any]:
        """List all notebooks (id + title + metadata)."""
        client = get_client(ctx)
        with mcp_errors():
            notebooks = await client.notebooks.list()
            return {"notebooks": to_jsonable(notebooks)}

    @mcp.tool
    async def notebook_create(ctx: Context, title: str) -> dict[str, Any]:
        """Create a new notebook with the given title."""
        client = get_client(ctx)
        with mcp_errors():
            result = await core.execute_notebook_create(client, title)
            # Flatten the created notebook to a top-level shape consistent with
            # the sibling create tool (``note_create``) and ``notebook_delete``,
            # which key the notebook by ``notebook_id`` rather than nesting the
            # record under a ``notebook`` key (#1540). The remaining Notebook
            # fields (title, created_at, sources_count, is_owner, modified_at)
            # stay at the top level so no metadata is dropped.
            record = to_jsonable(result.notebook)
            notebook_id = record.pop("id")
            # CREATE_NOTEBOOK leaves created_at/modified_at null even though
            # GET_NOTEBOOK / notebook_list populate them (#1699). Do ONE
            # best-effort re-read to backfill just those two keys, skipping it
            # when both are already present (no wasted RPC) or the id is empty
            # (no ``get("")``). The create result stays authoritative for
            # id/title/etc. The fill is PER-KEY and strictly additive: a slot is
            # filled only when the create left it null AND the re-read has a
            # value, so a populated create timestamp is never touched and a
            # lagging re-read that returns null cannot REGRESS one back to null.
            # The create already committed server-side, so a re-read failure
            # (eventual-consistency NotebookNotFoundError, a transport blip) must
            # degrade to the create timestamps rather than fail the create;
            # ``except Exception`` still lets asyncio.CancelledError propagate.
            if notebook_id and (
                record.get("created_at") is None or record.get("modified_at") is None
            ):
                try:
                    fresh = to_jsonable(await client.notebooks.get(notebook_id))
                    # ``to_jsonable`` on the ``Notebook`` dataclass always yields
                    # a dict; the isinstance guard makes the ``.get`` reads
                    # explicitly safe regardless.
                    if isinstance(fresh, dict):
                        for key in ("created_at", "modified_at"):
                            if record.get(key) is None and fresh.get(key) is not None:
                                record[key] = fresh[key]
                except Exception:
                    logger.debug(
                        "notebook_create: timestamp re-read failed; returning "
                        "create result with unpopulated timestamps",
                        exc_info=True,
                    )
            return {"notebook_id": notebook_id, **record}

    @mcp.tool(annotations=READ_ONLY)
    async def notebook_describe(ctx: Context, notebook: str) -> dict[str, Any]:
        """Fetch a notebook's AI-generated description. Accepts a notebook name or ID."""
        client = get_client(ctx)
        with mcp_errors():
            nb_id = await resolve_notebook(client, notebook)
            result = await core.execute_notebook_describe(
                client, nb_id, resolve_notebook_id=passthrough_notebook_id
            )
            return to_jsonable(result)

    @mcp.tool
    async def notebook_rename(ctx: Context, notebook: str, new_title: str) -> dict[str, Any]:
        """Rename a notebook. Accepts a notebook name or ID."""
        client = get_client(ctx)
        with mcp_errors():
            nb_id = await resolve_notebook(client, notebook)
            result = await core.execute_notebook_rename(
                client, nb_id, new_title, resolve_notebook_id=passthrough_notebook_id
            )
            return to_jsonable(result)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def notebook_delete(ctx: Context, notebook: str, confirm: bool = False) -> dict[str, Any]:
        """Delete a notebook (irreversible). Accepts a notebook name or ID.

        Two-step confirmation: called with ``confirm=False`` (the default) it does
        NOT delete — it returns a ``needs_confirmation`` preview of the resolved
        notebook. Call again with ``confirm=True`` to perform the delete.
        """
        client = get_client(ctx)
        with mcp_errors():
            nb_id = await resolve_notebook(client, notebook)
            if not confirm:
                title = title_for_id(await client.notebooks.list(), nb_id)
                return needs_confirmation(
                    {"action": "delete_notebook", "notebook_id": nb_id, "title": title}
                )
            await core.execute_notebook_delete(client, nb_id)
            return {"status": "deleted", "notebook_id": nb_id}
