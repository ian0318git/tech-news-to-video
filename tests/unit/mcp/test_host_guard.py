"""Behavioral tests for the MCP loopback Host-header guard (#1869)."""

from __future__ import annotations

import pytest

from notebooklm.mcp._host_guard import LoopbackHostGuardMiddleware


class _Recorder:
    """A trivial ASGI app that records whether it was reached."""

    def __init__(self) -> None:
        self.reached = False

    async def __call__(self, scope, receive, send) -> None:
        self.reached = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _http_scope(host: str | None) -> dict:
    headers = [(b"host", host.encode())] if host is not None else []
    return {"type": "http", "headers": headers}


async def _run(mw: LoopbackHostGuardMiddleware, scope: dict) -> int | None:
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b""}

    async def send(message: dict) -> None:
        sent.append(message)

    await mw(scope, receive, send)
    starts = [m for m in sent if m["type"] == "http.response.start"]
    return starts[0]["status"] if starts else None


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["evil.example", "attacker.com:9420", "0.0.0.0", "", None])
async def test_non_loopback_host_is_rejected(host: str | None) -> None:
    app = _Recorder()
    status = await _run(LoopbackHostGuardMiddleware(app, allow_external=False), _http_scope(host))
    assert status == 403
    assert app.reached is False  # request never reached the wrapped app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host", ["127.0.0.1", "127.0.0.1:9420", "localhost", "[::1]", "[::1]:9420"]
)
async def test_loopback_host_passes_through(host: str) -> None:
    app = _Recorder()
    status = await _run(LoopbackHostGuardMiddleware(app, allow_external=False), _http_scope(host))
    assert status == 200
    assert app.reached is True


@pytest.mark.asyncio
async def test_allow_external_bypasses_the_guard() -> None:
    # When an external bind is opted into (auth is mandatory there), the guard must
    # NOT reject a non-loopback Host — the bearer/OAuth layer owns access control.
    app = _Recorder()
    status = await _run(
        LoopbackHostGuardMiddleware(app, allow_external=True), _http_scope("evil.example")
    )
    assert status == 200
    assert app.reached is True


@pytest.mark.asyncio
async def test_non_http_scope_passes_through() -> None:
    # Lifespan/websocket scopes have no Host to validate and must not be blocked.
    app = _Recorder()
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "lifespan.startup"}

    async def send(message: dict) -> None:
        sent.append(message)

    await LoopbackHostGuardMiddleware(app, allow_external=False)(
        {"type": "lifespan"}, receive, send
    )
    assert app.reached is True
