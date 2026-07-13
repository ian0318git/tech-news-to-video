"""The ``source_add_drive_file`` MCP tool (auto-route add-from-Drive, #1884).

A discrete verb, deliberately NOT folded into ``source_add(source_type="drive")``:
that path REQUIRES an explicit ``mime_type`` and only ingests Google-native
Docs/Slides/Sheets + PDF by reference (#1827), whereas this tool downloads the
upload-only Drive types (epub/docx/txt/md/rtf/odt/csv/tsv/pdf) server-side and
uploads them. Kept in its own module (with its own ``register``) so the ceiling'd
``mcp/tools/sources.py`` doesn't grow (ADR-0025 discrete-verb rationale).

The fetch runs server-side with the profile's live cookies, so — unlike a
``source_add(source_type="file")`` on a remote (http) connector — it never emits
``upload_required``: a Drive source has no client-side bytes.

This module imports NO ``click`` / ``rich`` / ``cli``.
"""

from __future__ import annotations

from typing import Any

from fastmcp import Context

from ..._app import source_mutations as mut_core
from ..._app.serialize import to_jsonable
from ..._app.views import source_view as _source_view
from .._context import get_client
from .._errors import mcp_errors
from .._resolve import resolve_notebook


def register(mcp: Any) -> None:
    """Register the ``source_add_drive_file`` tool on ``mcp``."""

    @mcp.tool
    async def source_add_drive_file(
        ctx: Context,
        notebook: str,
        document_id: str,
        title: str | None = None,
        wait: bool = False,
    ) -> dict[str, Any]:
        """Add an upload-only Google Drive file (epub/docx/txt/md/rtf/odt/csv/tsv/pdf).

        Use this for the Drive file types NotebookLM's native Drive import can't
        ingest: the file is downloaded from Drive on the server and uploaded. For
        a Google-native Doc/Slides/Sheet (imported by reference), use
        ``source_add(source_type='drive', mime_type=…)`` instead — this tool
        returns a pointer error for those, since they aren't downloadable.

        Accepts a notebook name or ID, and a raw Drive file id or a Drive share
        URL (``/d/<id>``, ``/file/d/<id>/…``, or ``?id=<id>``). The fetch runs
        server-side with the profile's session, so it works over the remote (http)
        connector too — no ``upload_required`` step. The import is processed
        ASYNCHRONOUSLY; pass ``wait=true`` to block until it is READY (or confirm
        later with ``source_wait`` / ``source_list(status="error")``).
        """
        client = get_client(ctx)
        with mcp_errors():
            nb_id = await resolve_notebook(client, notebook)
            result = await mut_core.execute_source_add_drive_file(
                client,
                mut_core.SourceAddDriveFilePlan(
                    notebook_id=nb_id,
                    document_id=document_id,
                    title=title or None,
                    wait=wait,
                ),
            )
            payload = to_jsonable(result)
            payload["status"] = "added"
            payload["notebook_id"] = nb_id
            payload["source"] = _source_view(result.source)
            return payload
