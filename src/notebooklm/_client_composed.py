"""Client-owned composition holder state."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager, nullcontext
from typing import TYPE_CHECKING, Any, TypeVar

from ._session_config import DEFAULT_MAX_CONCURRENT_RPCS

_T = TypeVar("_T")

if TYPE_CHECKING:
    from ._middleware import Middleware
    from ._middleware_chain import MiddlewareChainBuilder
    from ._middleware_chain_host import MiddlewareChainHost
    from ._rpc_executor import RpcExecutor
    from ._session_init import SessionCollaborators, WiredMiddleware
    from ._session_transport import SessionTransport


class ClientComposed:
    """Mutable holder for composition state that is migrating off ``Session``."""

    def __init__(
        self,
        *,
        max_concurrent_rpcs: int | None = DEFAULT_MAX_CONCURRENT_RPCS,
    ) -> None:
        if max_concurrent_rpcs is not None and max_concurrent_rpcs < 1:
            raise ValueError(f"max_concurrent_rpcs must be >= 1, got {max_concurrent_rpcs!r}")
        self.max_concurrent_rpcs = max_concurrent_rpcs
        self._rpc_semaphore: asyncio.Semaphore | None = None
        self._transport: SessionTransport | None = None
        self._executor: RpcExecutor | None = None
        self._chain_host: MiddlewareChainHost | None = None
        self._chain_builder: MiddlewareChainBuilder | None = None
        self._middlewares: list[Middleware] | None = None
        # Avoid a plain `.collaborators` attribute here: the ADR-014 lint
        # reserves that name for the deleted Stage A Session accessor.
        self._session_collaborators: SessionCollaborators | None = None

    @staticmethod
    def _require_bound(attr_name: str, value: _T | None) -> _T:
        if value is None:
            raise RuntimeError(f"ClientComposed not fully constructed: {attr_name} is None")
        return value

    @property
    def transport(self) -> SessionTransport:
        return self._require_bound("_transport", self._transport)

    @property
    def executor(self) -> RpcExecutor:
        return self._require_bound("_executor", self._executor)

    @property
    def chain_host(self) -> MiddlewareChainHost:
        return self._require_bound("_chain_host", self._chain_host)

    @property
    def chain_builder(self) -> MiddlewareChainBuilder:
        return self._require_bound("_chain_builder", self._chain_builder)

    @property
    def middlewares(self) -> list[Middleware]:
        return self._require_bound("_middlewares", self._middlewares)

    @property
    def session_collaborators(self) -> SessionCollaborators:
        return self._require_bound("_session_collaborators", self._session_collaborators)

    def bind_transport(self, transport: SessionTransport) -> None:
        if self._transport is not None:
            raise RuntimeError("ClientComposed._transport already bound")
        self._transport = transport

    def bind_executor(self, executor: RpcExecutor) -> None:
        if self._executor is not None:
            raise RuntimeError("ClientComposed._executor already bound")
        self._executor = executor

    def bind_chain_host(self, chain_host: MiddlewareChainHost) -> None:
        if self._chain_host is not None:
            raise RuntimeError("ClientComposed._chain_host already bound")
        self._chain_host = chain_host

    def bind_chain_metadata(self, wired: WiredMiddleware) -> None:
        if self._chain_builder is not None:
            raise RuntimeError("ClientComposed._chain_metadata already bound")
        self._chain_builder = wired.chain_builder
        self._middlewares = wired.middlewares

    def bind_session_collaborators(self, collaborators: SessionCollaborators) -> None:
        if self._session_collaborators is not None:
            raise RuntimeError("ClientComposed._session_collaborators already bound")
        self._session_collaborators = collaborators

    def get_rpc_semaphore(self) -> AbstractAsyncContextManager[Any]:
        """Return the lazy per-client RPC semaphore, or a no-op context."""
        if self.max_concurrent_rpcs is None:
            return nullcontext()
        if self._rpc_semaphore is None:
            self._rpc_semaphore = asyncio.Semaphore(self.max_concurrent_rpcs)
        return self._rpc_semaphore


__all__ = ["ClientComposed"]
