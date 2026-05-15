"""Smoke test for the Tier 7 concurrency integration harness.

Demonstrates that:

1. ``ConcurrentMockTransport`` correctly records peak concurrent
   in-flight requests under a 100-way ``asyncio.gather`` fan-out.
2. ``ClientCore`` can be wired with the mock transport via the same
   "replace ``_http_client`` after ``open()``" pattern used in
   ``tests/unit/conftest.py::make_core``.
3. All 100 fan-out RPC calls complete successfully (each returns the
   default empty-list response).

Future-knob note (max_concurrent_rpcs)
--------------------------------------
The eventual ``ClientCore(max_concurrent_rpcs=...)`` knob is added in
PR T7.H1. When that lands, this smoke test should be updated to
construct the core with ``max_concurrent_rpcs=None`` so the asyncio
semaphore is *explicitly* disabled — proving the harness still
demonstrates true 100-way fan-out at the transport boundary even when
the production default may have been clamped to a lower bound.

Until T7.H1 lands the knob doesn't exist, so this test uses the
current ``ClientCore.__init__`` signature unchanged. Future contributors
who modify this file should:

  - Pass ``max_concurrent_rpcs=None`` explicitly to ``ClientCore``.
  - Keep the 100-way ``asyncio.gather``.
  - Keep the ``>= 80`` peak-inflight assertion (asyncio scheduling is
    not perfectly parallel; the margin absorbs CI jitter).

Performance budget
------------------
Wall-clock target: < 2s locally, < 5s in CI. The transport's per-request
delay (50ms default) is the dominant cost; 100 requests serialized
would take 5s, but at 100-way fan-out they overlap and should complete
in ~50–200ms of wall time.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from notebooklm._core import ClientCore
from notebooklm.auth import AuthTokens
from notebooklm.rpc import RPCMethod

from .conftest import ConcurrentMockTransport

# T8.D11 — concurrency-harness smoke tests against a mock transport; no
# HTTP, no cassette. Opt out of the tier-enforcement hook in
# tests/integration/conftest.py.
pytestmark = pytest.mark.allow_no_vcr


def _make_auth() -> AuthTokens:
    """Synthetic auth tokens — values don't matter, the mock transport
    ignores them. Mirrors ``tests/unit/conftest.py::make_core`` defaults
    so a regression in either place surfaces consistently.
    """
    return AuthTokens(
        csrf_token="CSRF_TEST",
        session_id="SID_TEST",
        cookies={"SID": "test_sid_cookie"},
    )


async def _open_core_with_transport(transport: ConcurrentMockTransport) -> ClientCore:
    """Open a ``ClientCore`` and swap in the mock transport.

    Mirrors the documented pattern from ``tests/unit/conftest.py``:
    ``ClientCore.open()`` builds its own ``httpx.AsyncClient`` and we
    can't override the transport via the constructor. So we open
    normally, then close-and-replace the underlying client with one
    that routes through our recording transport.

    Once ``ClientCore(transport=...)`` exists (or ``max_concurrent_rpcs``
    grows a transport hook) this can be simplified.
    """
    core = ClientCore(auth=_make_auth())
    await core.open()
    assert core._http_client is not None
    prior_cookies = core._http_client.cookies
    await core._http_client.aclose()
    core._http_client = httpx.AsyncClient(
        cookies=prior_cookies,
        transport=transport,
        timeout=httpx.Timeout(connect=1.0, read=5.0, write=5.0, pool=1.0),
    )
    return core


async def test_harness_100_way_fanout_records_peak_inflight(
    mock_transport_concurrent: ConcurrentMockTransport,
) -> None:
    """100-way ``asyncio.gather`` over ``rpc_call`` — all complete, peak >= 80.

    The threshold is ``>= 80`` (not ``== 100``) because asyncio task
    scheduling is not perfectly parallel: a few coroutines may complete
    before the last few enter the transport. ``80`` is comfortably above
    "the gather is broken / serialized" (which would show ~1) and below
    the theoretical maximum, leaving ~20% headroom for CI jitter.
    """
    transport = mock_transport_concurrent
    transport.set_delay(0.05)  # 50ms per request — long enough to stack

    core = await _open_core_with_transport(transport)
    try:
        start = time.perf_counter()
        results = await asyncio.gather(
            *[core.rpc_call(RPCMethod.LIST_NOTEBOOKS, []) for _ in range(100)]
        )
        elapsed = time.perf_counter() - start
    finally:
        await core.close()

    # All 100 completed (the gather doesn't hide exceptions because
    # return_exceptions defaults to False — any failure would have
    # already raised).
    assert len(results) == 100, f"expected 100 results, got {len(results)}"

    # The default response decodes to ``[]`` for LIST_NOTEBOOKS.
    assert all(r == [] for r in results), (
        f"expected all-empty list responses, got first divergent: "
        f"{next((r for r in results if r != []), None)!r}"
    )

    # Transport observed all 100 wire requests.
    assert transport.request_count() == 100, (
        f"transport saw {transport.request_count()} requests, expected 100"
    )

    # Peak in-flight was high — the harness is genuinely fanning out.
    # Lower bound 80: asyncio scheduling isn't perfectly parallel, allow
    # ~20% slack for CI jitter. Upper bound 100: we only fired 100
    # requests; a peak >100 means the counter is broken (e.g. enter()
    # called twice per request).
    peak = transport.get_peak_inflight()
    assert 80 <= peak <= 100, (
        f"peak in-flight was {peak}; expected 80 <= peak <= 100 for a "
        f"100-way asyncio.gather. A peak near 1 means the requests "
        f"serialized (check for a missing `await asyncio.sleep` in the "
        f"transport or an unintended global lock); a peak above 100 "
        f"means the in-flight counter is double-incrementing."
    )

    # All in-flight requests have drained.
    assert transport.get_inflight_count() == 0, (
        f"transport still reports {transport.get_inflight_count()} in-flight after gather completed"
    )

    # Performance budget: warn-loud if we blew past 5s. Test target is
    # <2s locally; this assertion is the CI safety net.
    assert elapsed < 5.0, (
        f"smoke test took {elapsed:.2f}s; budget is <5s in CI / <2s locally. "
        f"Either CI is heavily loaded or the harness regressed (the per-request "
        f"delay should overlap, not serialize, across 100 gather'd coroutines)."
    )


async def test_barrier_factory_releases_n_arrivers(
    barrier_factory,
) -> None:
    """Sanity check: N arrivers all unblock once the Nth arrives."""
    barrier = barrier_factory(3)

    async def arrive_then_return(label: str) -> str:
        await barrier.arrive()
        return label

    results = await asyncio.gather(
        arrive_then_return("a"),
        arrive_then_return("b"),
        arrive_then_return("c"),
    )
    assert sorted(results) == ["a", "b", "c"]
    assert barrier.is_set
    assert barrier.arrived_count == 3


async def test_cancellation_helper_surfaces_label_on_timeout(
    cancellation_helper,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A timeout re-raises and logs the label."""

    async def _hangs() -> None:
        await asyncio.sleep(10)

    with (
        caplog.at_level("ERROR"),
        pytest.raises((TimeoutError, asyncio.TimeoutError)),
    ):
        await cancellation_helper(_hangs(), timeout=0.05, label="hang-coro")

    assert any("hang-coro" in record.message for record in caplog.records), (
        f"Expected 'hang-coro' in error log; got records: {[r.message for r in caplog.records]}"
    )
