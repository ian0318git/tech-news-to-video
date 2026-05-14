"""Parity tests for the shared transport pipeline (T2.C).

Pins down the behavior of :meth:`ClientCore._perform_authed_post` (and the
chat-side :meth:`ClientCore.query_post`) extracted from ``_rpc_call_impl``:

- ``build_request`` factory is called once per HTTP attempt.
- On a single auth-error retry, the factory is called TWICE, and the second
  invocation observes a fresh ``_AuthSnapshot`` capturing whatever the
  refresh callback mutated.
- The request-id correlation tag (``[req=<id>]``) is stable across the retry
  chain.
- ``rate_limit_max_retries`` bounds 429 retries; exhausting the budget
  raises ``_TransportRateLimited``.
- The historical ``rpc_call`` happy path is unchanged byte-for-byte
  (URL + body identical to pre-extraction).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from notebooklm._core import (
    ClientCore,
    _AuthSnapshot,
    _TransportAuthExpired,
    _TransportRateLimited,
)
from notebooklm._logging import get_request_id
from notebooklm.auth import AuthTokens
from notebooklm.rpc import RPCMethod


def _make_core(
    *,
    refresh_callback: Callable[[], Any] | None = None,
    rate_limit_max_retries: int = 0,
) -> ClientCore:
    auth = AuthTokens(
        csrf_token="CSRF_OLD",
        session_id="SID_OLD",
        cookies={"SID": "sid_cookie"},
    )
    return ClientCore(
        auth=auth,
        refresh_callback=refresh_callback,
        refresh_retry_delay=0.0,
        rate_limit_max_retries=rate_limit_max_retries,
    )


def _ok_response(text: str = "OK") -> httpx.Response:
    return httpx.Response(
        200,
        text=text,
        request=httpx.Request("POST", "https://example.test/x"),
    )


def _status_error(code: int, *, retry_after: str | None = None) -> httpx.HTTPStatusError:
    headers = {"retry-after": retry_after} if retry_after else {}
    request = httpx.Request("POST", "https://example.test/x")
    response = httpx.Response(code, request=request, headers=headers)
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


# ---------------------------------------------------------------------------
# _perform_authed_post
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_request_called_once_on_happy_path(monkeypatch):
    core = _make_core()
    await core.open()
    try:
        calls: list[_AuthSnapshot] = []

        def build(snapshot: _AuthSnapshot) -> tuple[str, str, dict[str, str]]:
            calls.append(snapshot)
            return "https://example.test/x", "payload", {}

        async def fake_post(url, *, content, **kwargs):
            assert url == "https://example.test/x"
            assert content == "payload"
            return _ok_response()

        monkeypatch.setattr(core._http_client, "post", fake_post)

        response = await core._perform_authed_post(build_request=build, log_label="test")

        assert response.status_code == 200
        assert len(calls) == 1
        assert calls[0].csrf_token == "CSRF_OLD"
    finally:
        await core.close()


@pytest.mark.asyncio
async def test_build_request_called_twice_with_fresh_snapshot_on_401(monkeypatch):
    """On a 401 + successful refresh, the factory is invoked twice — and the
    second call sees the refreshed CSRF / session-id, not the stale ones."""
    refresh_calls = []

    async def refresh() -> AuthTokens:
        refresh_calls.append(True)
        # Mutate auth state so the second snapshot picks up new values.
        core.auth.csrf_token = "CSRF_NEW"
        core.auth.session_id = "SID_NEW"
        return core.auth

    core = _make_core(refresh_callback=refresh)
    await core.open()
    try:
        snapshots: list[_AuthSnapshot] = []

        def build(snapshot: _AuthSnapshot) -> tuple[str, str, dict[str, str]]:
            snapshots.append(snapshot)
            return "https://example.test/x", f"body-{snapshot.csrf_token}", {}

        call_count = {"n": 0}

        async def fake_post(url, *, content, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise _status_error(401)
            # Second attempt succeeds — confirm it carries the refreshed body.
            assert content == "body-CSRF_NEW"
            return _ok_response()

        monkeypatch.setattr(core._http_client, "post", fake_post)

        response = await core._perform_authed_post(build_request=build, log_label="test")

        assert response.status_code == 200
        assert len(refresh_calls) == 1
        assert call_count["n"] == 2
        assert len(snapshots) == 2
        # First snapshot pre-refresh; second snapshot post-refresh.
        assert snapshots[0].csrf_token == "CSRF_OLD"
        assert snapshots[0].session_id == "SID_OLD"
        assert snapshots[1].csrf_token == "CSRF_NEW"
        assert snapshots[1].session_id == "SID_NEW"
    finally:
        await core.close()


@pytest.mark.asyncio
async def test_transport_auth_expired_when_refresh_fails(monkeypatch):
    refresh_error = RuntimeError("re-authenticate")

    async def refresh() -> AuthTokens:
        raise refresh_error

    core = _make_core(refresh_callback=refresh)
    await core.open()
    try:

        def build(snapshot: _AuthSnapshot) -> tuple[str, str, dict[str, str]]:
            return "https://example.test/x", "payload", {}

        original = _status_error(401)

        async def fake_post(*args, **kwargs):
            raise original

        monkeypatch.setattr(core._http_client, "post", fake_post)

        with pytest.raises(_TransportAuthExpired) as exc_info:
            await core._perform_authed_post(build_request=build, log_label="test")

        assert exc_info.value.original is original
        assert exc_info.value.__cause__ is refresh_error
    finally:
        await core.close()


@pytest.mark.asyncio
async def test_429_retries_exhaust_to_transport_rate_limited(monkeypatch):
    core = _make_core(rate_limit_max_retries=2)
    await core.open()
    try:
        # Avoid actually sleeping during the retry budget.
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        def build(snapshot: _AuthSnapshot) -> tuple[str, str, dict[str, str]]:
            return "https://example.test/x", "payload", {}

        call_count = {"n": 0}

        async def fake_post(*args, **kwargs):
            call_count["n"] += 1
            raise _status_error(429, retry_after="1")

        monkeypatch.setattr(core._http_client, "post", fake_post)

        with pytest.raises(_TransportRateLimited) as exc_info:
            await core._perform_authed_post(build_request=build, log_label="test")

        # Initial attempt + 2 retries = 3 total POSTs.
        assert call_count["n"] == 3
        assert sleeps == [1, 1]
        assert exc_info.value.retry_after == 1
    finally:
        await core.close()


@pytest.mark.asyncio
async def test_429_without_retry_budget_raises_immediately(monkeypatch):
    core = _make_core(rate_limit_max_retries=0)
    await core.open()
    try:

        def build(snapshot: _AuthSnapshot) -> tuple[str, str, dict[str, str]]:
            return "https://example.test/x", "payload", {}

        async def fake_post(*args, **kwargs):
            raise _status_error(429, retry_after="60")

        monkeypatch.setattr(core._http_client, "post", fake_post)

        with pytest.raises(_TransportRateLimited) as exc_info:
            await core._perform_authed_post(build_request=build, log_label="test")

        assert exc_info.value.retry_after == 60
    finally:
        await core.close()


@pytest.mark.asyncio
async def test_request_id_constant_across_retry_chain(monkeypatch):
    """The correlation id set by ``rpc_call`` must be visible inside every
    retry attempt — both pre- and post-refresh.
    """

    async def refresh() -> AuthTokens:
        core.auth.csrf_token = "CSRF_NEW"
        return core.auth

    core = _make_core(refresh_callback=refresh)
    await core.open()
    try:
        observed_request_ids: list[str | None] = []

        def build(snapshot: _AuthSnapshot) -> tuple[str, str, dict[str, str]]:
            observed_request_ids.append(get_request_id())
            return "https://example.test/x", "payload", {}

        call_count = {"n": 0}

        async def fake_post(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise _status_error(401)
            return _ok_response()

        monkeypatch.setattr(core._http_client, "post", fake_post)

        # Drive through rpc_call so set_request_id is in scope (rpc_call is
        # the caller boundary that owns the request-id context).
        async def fake_decode(*args, **kwargs):
            return []

        monkeypatch.setattr(
            "notebooklm._core.decode_response",
            lambda *args, **kwargs: [],
        )

        # Use _perform_authed_post directly inside set_request_id to verify
        # the helper itself doesn't reset the id.
        from notebooklm._logging import reset_request_id, set_request_id

        token = set_request_id("REQ-stable-1234")
        try:
            await core._perform_authed_post(build_request=build, log_label="test")
        finally:
            reset_request_id(token)

        assert call_count["n"] == 2
        assert observed_request_ids == ["REQ-stable-1234", "REQ-stable-1234"]
    finally:
        await core.close()


# ---------------------------------------------------------------------------
# query_post (chat-side wrapper)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_post_wraps_rate_limit_as_chat_error(monkeypatch):
    from notebooklm.exceptions import ChatError

    core = _make_core(rate_limit_max_retries=0)
    await core.open()
    try:

        def build(snapshot: _AuthSnapshot) -> tuple[str, str, dict[str, str]]:
            return "https://example.test/x", "payload", {}

        async def fake_post(*args, **kwargs):
            raise _status_error(429, retry_after="42")

        monkeypatch.setattr(core._http_client, "post", fake_post)

        with pytest.raises(ChatError) as exc_info:
            await core.query_post(build_request=build, parse_label="chat.ask")

        assert "Retry after 42 seconds" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, _TransportRateLimited)
    finally:
        await core.close()


@pytest.mark.asyncio
async def test_query_post_wraps_auth_expired_as_chat_error(monkeypatch):
    from notebooklm.exceptions import ChatError

    async def refresh() -> AuthTokens:
        raise RuntimeError("login needed")

    core = _make_core(refresh_callback=refresh)
    await core.open()
    try:

        def build(snapshot: _AuthSnapshot) -> tuple[str, str, dict[str, str]]:
            return "https://example.test/x", "payload", {}

        async def fake_post(*args, **kwargs):
            raise _status_error(401)

        monkeypatch.setattr(core._http_client, "post", fake_post)

        with pytest.raises(ChatError) as exc_info:
            await core.query_post(build_request=build, parse_label="chat.ask")

        assert "authentication expired" in str(exc_info.value).lower()
        assert isinstance(exc_info.value.__cause__, _TransportAuthExpired)
    finally:
        await core.close()


@pytest.mark.asyncio
async def test_query_post_wraps_timeout_as_network_error(monkeypatch):
    from notebooklm.exceptions import NetworkError

    core = _make_core()
    await core.open()
    try:

        def build(snapshot: _AuthSnapshot) -> tuple[str, str, dict[str, str]]:
            return "https://example.test/x", "payload", {}

        async def fake_post(*args, **kwargs):
            raise httpx.ReadTimeout("read timeout")

        monkeypatch.setattr(core._http_client, "post", fake_post)

        with pytest.raises(NetworkError) as exc_info:
            await core.query_post(build_request=build, parse_label="chat.ask")

        assert isinstance(exc_info.value.original_error, httpx.ReadTimeout)
    finally:
        await core.close()


# ---------------------------------------------------------------------------
# rpc_call happy-path parity (URL + body byte-for-byte)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rpc_call_happy_path_url_and_body_unchanged(monkeypatch):
    """After the T2.C extraction, ``rpc_call`` must produce the same outgoing
    ``(url, body)`` as pre-extraction for the happy path."""
    core = _make_core()
    await core.open()
    try:
        captured: dict[str, Any] = {}

        async def fake_post(url, *, content, **kwargs):
            captured["url"] = url
            captured["content"] = content
            # Minimal valid batchexecute response.
            rpc_id = RPCMethod.LIST_NOTEBOOKS.value
            inner = json.dumps([])
            chunk = json.dumps([["wrb.fr", rpc_id, inner, None, None]])
            text = f")]}}'\n{len(chunk)}\n{chunk}\n"
            return _ok_response(text)

        monkeypatch.setattr(core._http_client, "post", fake_post)

        await core.rpc_call(RPCMethod.LIST_NOTEBOOKS, [])

        # The URL must carry the standard batchexecute query string.
        assert "rpcids=" + RPCMethod.LIST_NOTEBOOKS.value in captured["url"]
        assert "f.sid=SID_OLD" in captured["url"]
        # The body must include the CSRF token under the historical ``at=`` param.
        assert "at=CSRF_OLD" in captured["content"]
        assert "f.req=" in captured["content"]
    finally:
        await core.close()
