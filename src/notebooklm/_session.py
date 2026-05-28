"""Concrete session infrastructure for the NotebookLM API client."""

from __future__ import annotations

import asyncio  # noqa: F401 - compatibility patch surface for default sleep
import logging
import random  # noqa: F401 - tests patch this for _backoff jitter
from typing import TYPE_CHECKING

import httpx  # noqa: F401 - compatibility patch surface for AsyncClient defaults

from .auth import (
    AuthTokens,
)

if TYPE_CHECKING:
    from ._client_composed import ClientComposed
    from ._client_seams import ClientSeams
    from ._middleware import Middleware
    from ._middleware_chain import MiddlewareChainBuilder
    from ._middleware_chain_host import MiddlewareChainHost
    from ._rpc_executor import RpcExecutor
    from ._session_init import (
        SessionCollaborators,
    )
    from ._session_transport import SessionTransport

    # ADR-014 Rule 5 (Wave 4 of session-decoupling): the compile-time
    # ``Session: RpcOwner`` assertion was removed when the ``RpcOwner``
    # Protocol itself was deleted — ``RpcExecutor`` now takes its
    # collaborators directly via keyword arguments instead of reaching
    # them through a Session-shaped owner.


logger = logging.getLogger(__name__)

# Auth-snapshot canonical implementation lives on
# :class:`AuthRefreshCoordinator` (``_session_auth.py`` —
# ``AuthRefreshCoordinator.snapshot`` / ``.update_auth_tokens`` /
# ``.update_auth_headers``). PR 8 first collapsed the previously
# real-bodied ``Session._snapshot`` / ``Session.update_auth_tokens``
# into thin delegates that forwarded through ``self._auth_coord``.
# PR #4b of the session-refactor arc then inlined
# ``Session._snapshot`` entirely — every site that needs an
# :class:`AuthSnapshot` now reads
# ``self._auth_coord.snapshot(auth=self.auth)`` directly. The
# coordinator method signatures take explicit ``auth`` / ``kernel``
# collaborators (the Session-shaped ``_AuthRefreshHost`` Protocol was
# deleted in favor of per-method explicit args). Wave 3 of plan
# ``host-protocol-removal`` deleted the remaining Session-level
# ``update_auth_tokens`` / ``update_auth_headers`` delegates and the
# ``lifecycle`` property; production callers
# (:func:`refresh_auth_session`, the integration tests that previously
# poked the headers via ``core.update_auth_headers()``) now invoke
# the coordinator methods directly with explicit kwargs.
# The AST guards in ``tests/unit/test_concurrency_refresh_race.py``
# (``test_snapshot_acquires_auth_snapshot_lock`` /
# ``test_update_auth_tokens_has_no_await_inside_mutation_block``)
# inspect the coordinator's source via ``inspect.getsource(...)`` +
# AST parsing — changes to auth-snapshot invariants must be applied to
# :meth:`AuthRefreshCoordinator.update_auth_tokens` directly.


class Session:
    """Core client infrastructure for HTTP and RPC operations.

    Handles:
    - HTTP client lifecycle (open/close)
    - RPC call encoding/decoding
    - Authentication headers
    - Conversation cache

    This class is used internally by the sub-client APIs (NotebooksAPI,
    ArtifactsAPI, etc.) and should not be used directly.
    """

    _seams: ClientSeams

    def __init__(
        self,
        *,
        collaborators: SessionCollaborators,
        auth: AuthTokens,
        composed: ClientComposed,
    ) -> None:
        """Initialise the one-wave Session lifecycle forwarder.

        :class:`Session` no longer owns composition state. Phase 2 moves
        transport, executor, chain host, chain builder, and middleware
        storage to :class:`ClientComposed`; this class keeps temporary
        property forwarders so tests that still receive a ``Session``
        can read ``core._transport`` / ``core._rpc_executor`` /
        ``core._chain_host`` until Phase 3 performs the mass rewrite.

        Production callers DO NOT instantiate :class:`Session` directly
        — :class:`NotebookLMClient` constructs it after
        :func:`compose_client_internals` has fully bound
        :class:`ClientComposed`.
        Tests use the canonical
        ``tests/_helpers/session_factory.build_session_for_tests``
        helper, which returns the one-wave ``client._session``.

        Args:
            collaborators: The :class:`SessionCollaborators` bundle
                constructed by :func:`build_collaborators` inside
                :func:`compose_client_internals`.
            auth: Authentication tokens from browser login.
            composed: Client-owned composition holder. It owns the chain
                host, transport, middleware metadata, executor, and lazy
                RPC semaphore state.
        """
        self._composed = composed
        self.auth = auth

        # The collaborator bundle is stored as a private attribute so
        # :class:`NotebookLMClient` can hoist the ``metrics``
        # collaborator off the same bundle the Session uses (e.g. for
        # ``NotebookLMClient.metrics_snapshot``). The Stage A
        # accessor properties (``Session.collaborators`` /
        # ``Session.session_transport`` / ``Session.rpc_executor``) that
        # previously exposed the bundle through the Session surface
        # were deleted in this PR — :class:`NotebookLMClient` reads
        # from the :class:`ClientInternals` it received instead.
        self._collaborators = collaborators
        self._metrics_obj = collaborators.metrics
        self._drain_tracker = collaborators.drain_tracker
        self._reqid = collaborators.reqid
        self._auth_coord = collaborators.auth_coord
        self._kernel = collaborators.kernel
        self._lifecycle = collaborators.lifecycle
        self.cookie_persistence = collaborators.cookie_persistence

    @property
    def _transport(self) -> SessionTransport:
        return self._composed.transport

    @property
    def _rpc_executor(self) -> RpcExecutor:
        return self._composed.executor

    @_rpc_executor.setter
    def _rpc_executor(self, executor: RpcExecutor) -> None:
        self._composed._executor = executor

    @property
    def _chain_host(self) -> MiddlewareChainHost:
        return self._composed.chain_host

    @property
    def _chain_builder(self) -> MiddlewareChainBuilder:
        return self._composed.chain_builder

    @property
    def _middlewares(self) -> list[Middleware]:
        return self._composed.middlewares

    async def open(self) -> None:
        """Open the HTTP client connection.

        Called automatically by NotebookLMClient.__aenter__. Delegates to
        :meth:`ClientLifecycle.open` — that helper builds the
        ``httpx.AsyncClient`` (always the default transport; the
        ``NOTEBOOKLM_VCR_RECORD_ERRORS`` opt-in is enforced by
        :class:`ErrorInjectionMiddleware` at chain layer, not by wrapping
        the transport — see ADR-009 close-out notes), captures the
        running event loop into ``self._bound_loop``, and spawns the
        keepalive task. Idempotent — calling ``open()`` while already
        open is a no-op. Re-opening after a prior :meth:`close`
        intentionally replaces the loop binding; :meth:`close` does not
        unbind so an
        accidental cross-loop call after close still raises actionably.

        Wave 2 of plan ``host-protocol-removal`` narrowed
        :meth:`ClientLifecycle.open` to take explicit collaborator
        kwargs; this forwarder unpacks its own collaborator aliases
        and passes them through so the lifecycle never reaches back
        through a Session-shaped host.
        """
        await self._lifecycle.open(
            auth=self.auth,
            drain_tracker=self._drain_tracker,
            auth_coord=self._auth_coord,
            reqid=self._reqid,
            cookie_persistence=self.cookie_persistence,
        )

    async def close(self) -> None:
        """Close the HTTP client connection.

        Called automatically by NotebookLMClient.__aexit__. Delegates to
        :meth:`ClientLifecycle.close`, which:

        1. Cancels and joins the keepalive task (so the loop can't issue a
           poke against an already-closed transport).
        2. Runs registered feature drain hooks.
        3. Saves cookies one last time through ``ClientLifecycle.save_cookies``.
        4. Calls ``aclose()`` under :func:`asyncio.shield` so cancellation
           arriving mid-close cannot leak the underlying httpx transport.
        5. Nulls out ``_kernel._http_client`` so a follow-up
           :meth:`open` rebuilds the live transport against a fresh
           ``httpx.AsyncClient``.

        Stage B1 PR 2 dropped the close-time ``_rpc_executor = None``
        step that previously lived in :meth:`ClientLifecycle.close` —
        the executor is composition-root-bound and persists across
        ``close()`` → ``open()`` cycles. See
        :mod:`tests.unit.test_lifecycle_executor_reuse` for the
        regression pin.

        Wave 2 of plan ``host-protocol-removal`` narrowed
        :meth:`ClientLifecycle.close` to take explicit collaborator
        kwargs; this forwarder unpacks its own collaborator aliases
        and passes them through.
        """
        await self._lifecycle.close(
            auth_coord=self._auth_coord,
            drain_tracker=self._drain_tracker,
            cookie_persistence=self.cookie_persistence,
        )

    async def _keepalive_loop(self, interval: float) -> None:
        """Background loop that periodically pokes the identity surface.

        Thin facade over :meth:`ClientLifecycle._keepalive_loop`. Retained
        as a ``Session`` method so ``test_client_keepalive`` and other
        tests that introspect ``core._keepalive_loop`` continue to resolve.

        Wave 2 of plan ``host-protocol-removal`` narrowed
        :meth:`ClientLifecycle._keepalive_loop` to take an explicit
        ``cookie_persistence`` kwarg; this forwarder supplies the
        Session's own collaborator alias.
        """
        await self._lifecycle._keepalive_loop(
            cookie_persistence=self.cookie_persistence,
            interval=interval,
        )

    @property
    def is_open(self) -> bool:
        """Check if the HTTP client is open."""
        return self._lifecycle.is_open()

    async def drain(self, timeout: float | None = None) -> None:
        """Stop accepting new operations and wait for in-flight ones to finish.

        Narrow forward to :meth:`TransportDrainTracker.drain` so the
        ``NotebookLMClient`` composition root no longer dereferences
        ``self._session._drain_tracker`` (a private collaborator slot)
        when implementing :meth:`NotebookLMClient.drain`. The method
        body intentionally stays a one-line delegation — Session does
        not add semantics here, it just exposes the drain capability
        with a name that does not depend on the underscore-prefixed
        storage slot.
        """
        await self._drain_tracker.drain(timeout=timeout)

    # ``lifecycle`` (@property), ``update_auth_headers``, and
    # ``update_auth_tokens`` were deleted in Wave 3 of plan
    # ``host-protocol-removal``. Callers now invoke the canonical
    # collaborator methods directly with explicit kwargs
    # (``auth_coord.update_auth_tokens(auth=..., csrf=..., session_id=...)``
    # / ``auth_coord.update_auth_headers(auth=..., kernel=...)`` /
    # ``self._collaborators.lifecycle`` for the refresh path). See
    # ``docs/session-method-retention.md`` **Deleted** section.
