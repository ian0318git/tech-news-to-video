"""Source MCP tools.

Thin adapters over the transport-neutral ``_app.source_*`` cores: resolve the
notebook (and, where applicable, the source) reference via the Phase 1
:mod:`._resolve` helpers, drive the ``execute_source_*`` executors, and project
the typed result to the wire with :func:`to_jsonable`.

``source_add`` is a hybrid over two cores: ``url``/``text``/``file``/``youtube``
flow through ``_app.source_add`` (``build_source_add_plan`` + ``execute_source_add``);
``drive`` flows through ``_app.source_mutations.execute_source_add_drive`` (the
neutral ``source_add`` core has no Drive path). ``source_wait`` waits for one
source when ``source`` is given, else every source in the notebook.

This module imports NO ``click`` / ``rich`` / ``cli``.
"""

from __future__ import annotations

import os
import time
from typing import Any, Literal

from fastmcp import Context
from fastmcp.server.dependencies import get_http_request

from ..._app import source_add as add_core
from ..._app import source_content as content_core
from ..._app import source_mutations as mut_core
from ..._app import source_wait as wait_core
from ..._app.serialize import to_jsonable
from ...exceptions import ConfigurationError, SourceNotFoundError, ValidationError
from ...urls import is_youtube_url
from .._confirm import DESTRUCTIVE, READ_ONLY, needs_confirmation
from .._context import get_client, get_file_transfer
from .._errors import mcp_errors
from .._filelink import UPLOAD_TTL, FileTransferConfig
from .._resolve import resolve_notebook, resolve_source
from ._passthrough import passthrough_child_id
from ._preview import title_for_id

#: MCP source types. Superset of the neutral ``source_add`` core's types
#: (which lacks ``drive``); ``drive`` is dispatched to the Drive path.
_SOURCE_TYPES = ("url", "text", "file", "drive", "youtube")

#: Drive MIME choices the backend accepts (mirrors the CLI ``--mime-type``).
_DRIVE_MIME_CHOICES = ("google-doc", "google-slides", "google-sheets", "pdf")

#: The default Drive MIME choice when the caller does not specify one.
_DEFAULT_DRIVE_MIME = "google-doc"


def _source_view(source: Any) -> dict[str, Any]:
    """Serialize a Source with agent-readable string labels added.

    ``to_jsonable`` emits only dataclass fields, so the integer ``status`` and
    ``_type_code`` arrive as bare numbers and the ``kind`` *property* is dropped —
    forcing an agent to guess what ``3``/``5``/``2`` mean. Add ``kind`` (e.g.
    ``"pdf"``/``"web_page"``) and ``status_label`` (e.g. ``"ready"``/``"error"``)
    string labels alongside the raw codes.
    """
    view = to_jsonable(source)
    view["kind"] = source.kind.value
    status = source.status
    view["status_label"] = getattr(status, "name", str(status)).lower()
    return view


def register(mcp: Any) -> None:
    """Register the source tools on ``mcp``."""

    @mcp.tool(annotations=READ_ONLY)
    async def source_list(ctx: Context, notebook: str) -> dict[str, Any]:
        """List a notebook's sources. Accepts a notebook name or ID.

        Each source carries string ``kind`` / ``status_label`` labels (not just the
        raw type/status codes) so an agent never has to guess the enums.
        """
        client = get_client(ctx)
        with mcp_errors():
            nb_id = await resolve_notebook(client, notebook)
            sources = await client.sources.list(nb_id)
            return {"notebook_id": nb_id, "sources": [_source_view(s) for s in sources]}

    @mcp.tool(annotations=READ_ONLY)
    async def source_get_content(
        ctx: Context,
        notebook: str,
        source: str,
        output_format: Literal["text", "markdown"] = "text",
        max_chars: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Fetch a source's metadata AND its full indexed text content.

        Accepts a notebook/source name or ID. Returns the source metadata (incl.
        string ``kind``/``status_label``) plus the extracted ``content``, the full
        ``char_count``, and a ``truncated`` flag.

        ``output_format`` selects how the text is rendered: ``text`` (default) is
        flattened plaintext; ``markdown`` preserves headings/tables/links/emphasis
        (requires the server's ``markdownify`` extra — otherwise a clean error).

        For large sources, window the body with ``offset`` and ``max_chars`` (the
        returned ``content`` is the slice ``[offset : offset+max_chars]``);
        ``char_count`` is always the FULL length and ``truncated`` says whether the
        returned slice is shorter than the remainder. Prefer ``chat_ask`` for
        querying large sources rather than pulling the whole body.

        ``content`` is ``null`` (and ``char_count`` 0) when the source is not yet
        ready (still processing / errored) or has no extractable text, so this tool
        returns the metadata even before the body is available.
        """
        client = get_client(ctx)
        with mcp_errors():
            if max_chars is not None and max_chars < 0:
                raise ValidationError(f"max_chars must be >= 0; got {max_chars}")
            if offset < 0:
                raise ValidationError(f"offset must be >= 0; got {offset}")
            nb_id = await resolve_notebook(client, notebook)
            src_id = await resolve_source(client, nb_id, source)
            result = await content_core.execute_source_get(
                client, content_core.SourceGetPlan(notebook_id=nb_id, source_id=src_id)
            )
            # A full-UUID ref skips list resolution (the resolver trusts a full
            # id), so a non-existent id reaches ``get_or_none`` and yields a
            # ``None`` source. Surface that as NOT_FOUND rather than returning
            # ``{"source": null}`` as a success.
            if result.source is None:
                raise SourceNotFoundError(src_id)

            # Only fetch the body once the source is READY. A not-ready source
            # (still processing / errored) has no retrievable text yet, so return
            # its metadata with content=None instead of fetching. Gating on status
            # (rather than catching the fulltext fetch's SourceNotFoundError) keeps
            # a genuine "source is gone" — e.g. deleted between these two calls —
            # propagating as NOT_FOUND instead of masquerading as "no content".
            content: str | None = None
            char_count = 0
            if result.source.is_ready:
                try:
                    fulltext = await content_core.execute_source_fulltext(
                        client,
                        content_core.SourceFulltextPlan(
                            notebook_id=nb_id, source_id=src_id, output_format=output_format
                        ),
                    )
                except ImportError as exc:
                    # ``output_format='markdown'`` needs the optional ``markdownify``
                    # extra, which the server may not have installed. Surface a
                    # deterministic CONFIG error (with the install hint) rather than
                    # the bug-class UNEXPECTED a bare ImportError would project as.
                    # Restrict the remap to the markdown path: an ImportError on the
                    # text path (or a future regression) is genuinely unexpected and
                    # must keep propagating as such, not be mislabeled CONFIG.
                    if output_format != "markdown":
                        raise
                    raise ConfigurationError(str(exc)) from exc
                content = fulltext.fulltext.content or None
                char_count = fulltext.fulltext.char_count

            # Window the body if requested. ``char_count`` stays the FULL length;
            # ``truncated`` reports whether the returned slice omits any remainder.
            truncated = False
            if content is not None and (offset > 0 or max_chars is not None):
                end = len(content) if max_chars is None else offset + max_chars
                windowed = content[offset:end]
                truncated = len(windowed) < (len(content) - offset)
                # Normalize an empty slice (e.g. offset past the end) to None, matching
                # the fetch-path contract (content is null when there's nothing to show).
                content = windowed or None

            payload = to_jsonable(result)
            payload["source"] = _source_view(result.source)
            payload["content"] = content
            payload["char_count"] = char_count
            payload["truncated"] = truncated
            payload["output_format"] = output_format
            return payload

    @mcp.tool
    async def source_rename(
        ctx: Context, notebook: str, source: str, new_title: str
    ) -> dict[str, Any]:
        """Rename a source. Accepts a notebook/source name or ID."""
        client = get_client(ctx)
        with mcp_errors():
            nb_id = await resolve_notebook(client, notebook)
            src_id = await resolve_source(client, nb_id, source)
            result = await mut_core.execute_source_rename(
                client,
                mut_core.SourceRenamePlan(
                    notebook_id=nb_id, source_id=src_id, new_title=new_title, json_output=False
                ),
                resolve_source_id=passthrough_child_id,
            )
            return to_jsonable(result)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def source_delete(
        ctx: Context, notebook: str, source: str, confirm: bool = False
    ) -> dict[str, Any]:
        """Delete a source (irreversible). Accepts a notebook/source name or ID.

        Two-step confirmation: with ``confirm=False`` (default) it returns a
        ``needs_confirmation`` preview of the resolved source without deleting;
        call again with ``confirm=True`` to perform the delete.
        """
        client = get_client(ctx)
        with mcp_errors():
            nb_id = await resolve_notebook(client, notebook)
            src_id = await resolve_source(client, nb_id, source)
            if not confirm:
                title = title_for_id(await client.sources.list(nb_id), src_id)
                return needs_confirmation(
                    {
                        "action": "delete_source",
                        "notebook_id": nb_id,
                        "source_id": src_id,
                        "title": title,
                    }
                )
            await client.sources.delete(nb_id, src_id)
            return {"status": "deleted", "notebook_id": nb_id, "source_id": src_id}

    @mcp.tool(annotations=READ_ONLY)
    async def source_wait(
        ctx: Context,
        notebook: str,
        source: str | None = None,
        timeout: float = 120.0,
        interval: float = 1.0,
    ) -> dict[str, Any]:
        """Wait for sources to finish processing. Accepts a notebook name or ID.

        Waits for a single source when ``source`` (name or ID) is given, otherwise
        for every source in the notebook. Returns the ready sources, or surfaces a
        not-found / processing / timeout error.
        """
        client = get_client(ctx)
        with mcp_errors():
            if timeout < 0:
                raise ValidationError(f"timeout must be >= 0; got {timeout}")
            if interval <= 0:
                raise ValidationError(f"interval must be > 0; got {interval}")
            nb_id = await resolve_notebook(client, notebook)
            if source is not None:
                src_id = await resolve_source(client, nb_id, source)
                outcome = await wait_core.execute_source_wait(
                    client,
                    wait_core.SourceWaitPlan(
                        notebook_id=nb_id,
                        source_id=src_id,
                        timeout=timeout,
                        interval=interval,
                    ),
                )
                return _wait_outcome_payload(nb_id, outcome)
            sources = await client.sources.list(nb_id)
            source_ids = [s.id for s in sources]
            # ``wait_for_sources`` forwards **kwargs to ``wait_until_ready``,
            # whose poll-interval kwarg is ``initial_interval`` — thread the
            # advertised ``interval`` through so the all-sources branch honors it
            # just like the single-source branch above.
            ready = await client.sources.wait_for_sources(
                nb_id, source_ids, timeout=timeout, initial_interval=interval
            )
            return {"notebook_id": nb_id, "ready": to_jsonable(ready)}

    @mcp.tool
    async def source_add(
        ctx: Context,
        notebook: str,
        source_type: Literal["url", "text", "file", "drive", "youtube"],
        url: str | None = None,
        text: str | None = None,
        title: str | None = None,
        path: str | None = None,
        document_id: str | None = None,
        mime_type: str | None = None,
        allow_internal: bool = False,
    ) -> dict[str, Any]:
        """Add a source to a notebook. Accepts a notebook name or ID.

        ``source_type`` selects the input and which named argument is required:

        * ``url``     — requires ``url``.
        * ``youtube`` — requires ``url`` (a YouTube link).
        * ``text``    — requires ``text``; ``title`` optional.
        * ``file``    — over **stdio**, requires ``path`` (a local file path on the
          server host). Over the **remote (http) connector** the server's
          filesystem is unreachable, so instead the tool returns
          ``{"status": "upload_required", "url": …, "agent_upload": {…}}``. A human
          opens the short-lived signed URL in a browser and uploads the file; an
          **agent that already holds the bytes** skips the browser and POSTs them as
          the raw request body to the same URL (see the ``agent_upload`` recipe in
          the response — with ``Accept: application/json`` it returns
          ``{"status": "added", "source_id": …}``). Then confirm with ``source_wait``
          / ``source_list``. ``title`` / ``mime_type`` (carried in the signed URL)
          and the supplied ``path`` (its basename seeds the default title) are honored.
        * ``drive``   — requires ``document_id`` (Google Drive file id); ``title``
          and ``mime_type`` (one of google-doc|google-slides|google-sheets|pdf,
          default google-doc) optional.

        The other named inputs are mutually exclusive — supply only the one your
        ``source_type`` requires.
        """
        client = get_client(ctx)
        with mcp_errors():
            # ``source_type`` is a Literal — FastMCP/Pydantic rejects an unknown value
            # at the schema boundary, so no runtime membership check is needed.
            if (
                mime_type is not None
                and source_type == "drive"
                and mime_type not in _DRIVE_MIME_CHOICES
            ):
                raise ValidationError(
                    f"Invalid mime_type {mime_type!r} for drive; "
                    f"expected one of {list(_DRIVE_MIME_CHOICES)}"
                )
            nb_id = await resolve_notebook(client, notebook)

            if source_type == "file":
                cfg = get_file_transfer(ctx)
                if cfg is not None:
                    # Remote connector: broker a signed upload URL (the server path
                    # is unreachable). A supplied `path` is accepted, not opened —
                    # its basename seeds the default title.
                    return _broker_upload(cfg, nb_id, title=title, mime_type=mime_type, path=path)
                if _is_http_transport():
                    raise ValidationError(
                        "remote file transfer is not configured; set "
                        "NOTEBOOKLM_MCP_PUBLIC_URL on the server to enable it"
                    )
                # stdio: fall through to the existing local-path behavior below.

            if source_type == "drive":
                if not document_id:
                    raise ValidationError("source_type 'drive' requires 'document_id'")
                drive_result = await mut_core.execute_source_add_drive(
                    client,
                    mut_core.SourceAddDrivePlan(
                        notebook_id=nb_id,
                        file_id=document_id,
                        title=title or "",
                        mime_type=mime_type or _DEFAULT_DRIVE_MIME,  # type: ignore[arg-type]
                    ),
                )
                return to_jsonable(drive_result)

            content = _select_content(source_type, url=url, text=text, path=path)
            plan = add_core.build_source_add_plan(
                content=content,
                source_type=source_type,
                title=title,
                mime_type=mime_type,
                follow_symlinks=False,
                validate_path=add_core.validate_upload_path,
                looks_path_shaped=add_core.looks_like_path,
                allow_internal=allow_internal,
            )
            add_result = await add_core.execute_source_add(
                client,
                add_core.SourceAddExecutionPlan(notebook_id=nb_id, plan=plan),
            )
            return to_jsonable(add_result)


def _is_http_transport() -> bool:
    """Whether the current tool call arrived over the http transport.

    A remote (http) call has an active Starlette request; stdio does not
    (:func:`get_http_request` raises ``RuntimeError``). Used to tell a remote
    ``file`` add *without* file transfer configured (→ clean "not configured"
    error) apart from a stdio add (→ existing local-path behavior).
    """
    try:
        get_http_request()
    except RuntimeError:
        return False
    return True


def _broker_upload(
    cfg: FileTransferConfig,
    notebook_id: str,
    *,
    title: str | None,
    mime_type: str | None,
    path: str | None,
) -> dict[str, Any]:
    """Mint a signed upload URL for a remote ``source_add type=file``.

    The agent-supplied ``title`` / ``mime_type`` ride in the signed token (so they
    survive the browser round-trip and cannot be tampered with). When ``title`` is
    unset, the supplied ``path``'s basename seeds the default. The signer injects
    expiry; ``expires_at`` mirrors the upload TTL for the caller.
    """
    default_title = title
    if not default_title and path:
        # The agent's path may be Windows-style (``C:\\Users\\me\\report.pdf``) even
        # though this server runs on Linux, where ``os.path.basename`` won't split on
        # ``\\`` — normalize first so the default title is the real leaf.
        default_title = os.path.basename(path.replace("\\", "/")) or None
    payload: dict[str, Any] = {"nb": notebook_id}  # op stamped by upload_url
    if default_title:
        payload["title"] = default_title
    if mime_type:
        payload["mime"] = mime_type
    url = cfg.upload_url(payload)
    return {
        "status": "upload_required",
        "notebook_id": notebook_id,
        "url": url,
        "expires_at": int(time.time()) + UPLOAD_TTL,
        # An agent holding the bytes skips the browser: POST them as the raw body here.
        "agent_upload": {
            "method": "POST",
            "url": f"{url}?filename=<basename>",
            "headers": {
                "Accept": "application/json",
                "Content-Type": "<mime-type> (fallback only; ignored when mime_type was passed)",
            },
            "body": "the raw file bytes (not multipart/form-data)",
            "returns": '{"status": "added", "source_id": ...}',
            "example": (
                'curl -X POST -H "Accept: application/json" --data-binary @report.pdf '
                f'"{url}?filename=report.pdf"'
            ),
        },
    }


def _select_content(
    source_type: str, *, url: str | None, text: str | None, path: str | None
) -> str:
    """Pick the single content value the ``source_type`` requires, validating presence."""
    if source_type in {"url", "youtube"}:
        if not url:
            raise ValidationError(f"source_type {source_type!r} requires 'url'")
        # ``source_type=youtube`` advertises a YouTube link — reject a non-YouTube
        # host rather than silently adding it as a generic URL (host-parsed, not a
        # substring match: ``evil.com/youtube.com`` does NOT pass).
        if source_type == "youtube" and not is_youtube_url(url):
            raise ValidationError(
                "source_type 'youtube' requires a YouTube URL "
                "(youtube.com / youtu.be / m.youtube.com)"
            )
        return url
    if source_type == "text":
        if not text:
            raise ValidationError("source_type 'text' requires 'text'")
        return text
    if source_type == "file":
        if not path:
            raise ValidationError("source_type 'file' requires 'path'")
        return path
    raise ValidationError(f"Unknown source type {source_type!r}")  # pragma: no cover


def _wait_outcome_payload(notebook_id: str, outcome: wait_core.SourceWaitOutcome) -> dict[str, Any]:
    """Project a single-source :class:`SourceWaitOutcome` onto the wire shape."""
    if isinstance(outcome, wait_core.SourceWaitReady):
        return {
            "notebook_id": notebook_id,
            "status": "ready",
            "source": to_jsonable(outcome.source),
        }
    if isinstance(outcome, wait_core.SourceWaitNotFound):
        return {"notebook_id": notebook_id, "status": "not_found", "error": str(outcome.error)}
    if isinstance(outcome, wait_core.SourceWaitProcessingError):
        return {"notebook_id": notebook_id, "status": "failed", "error": str(outcome.error)}
    return {"notebook_id": notebook_id, "status": "timeout", "error": str(outcome.error)}
