"""Shared test fixtures."""

import json
import os
import re

import pytest

from notebooklm.rpc import RPCMethod


@pytest.fixture(autouse=True)
def _mock_keepalive_poke(request):
    """Default-mock the auth keepalive poke so tests don't trip on it.

    ``_fetch_tokens_with_jar`` makes a best-effort GET to
    ``accounts.google.com/CheckCookie`` to elicit SIDTS rotation. Tests that
    use ``httpx_mock`` would otherwise fail with "no response set" when this
    request fires. The mock is optional+reusable so tests that don't trigger
    the poke aren't penalised.

    Tests that need full control over the poke response (e.g. to assert on
    rotated Set-Cookie or simulate failure) should mark themselves with
    ``@pytest.mark.no_default_keepalive_mock`` to skip this default and
    register their own response.
    """
    if "httpx_mock" not in request.fixturenames:
        return
    if request.node.get_closest_marker("no_default_keepalive_mock"):
        return
    httpx_mock = request.getfixturevalue("httpx_mock")
    httpx_mock.add_response(
        url=re.compile(r"^https://accounts\.google\.com/CheckCookie.*$"),
        is_optional=True,
        is_reusable=True,
        status_code=204,
    )


def pytest_configure(config):
    """Register custom markers and configure test environment."""
    config.addinivalue_line(
        "markers",
        "vcr: marks tests that use VCR cassettes (may be skipped if cassettes unavailable)",
    )
    config.addinivalue_line(
        "markers",
        "no_default_keepalive_mock: skip the default accounts.google.com/CheckCookie "
        "mock so the test can register its own response",
    )
    # Disable Rich/Click formatting in tests to avoid ANSI escape codes in output
    # This ensures consistent test assertions regardless of -s flag
    # NO_COLOR disables colors, TERM=dumb disables all formatting (bold, etc.)
    # Force these values to ensure consistent behavior across all environments
    os.environ["NO_COLOR"] = "1"
    os.environ["TERM"] = "dumb"


@pytest.fixture
def sample_storage_state():
    """Sample Playwright storage state with valid cookies."""
    return {
        "cookies": [
            {"name": "SID", "value": "test_sid", "domain": ".google.com"},
            {"name": "HSID", "value": "test_hsid", "domain": ".google.com"},
            {"name": "SSID", "value": "test_ssid", "domain": ".google.com"},
            {"name": "APISID", "value": "test_apisid", "domain": ".google.com"},
            {"name": "SAPISID", "value": "test_sapisid", "domain": ".google.com"},
        ]
    }


@pytest.fixture
def sample_homepage_html():
    """Sample NotebookLM homepage HTML with tokens."""
    return """
    <!DOCTYPE html>
    <html>
    <head><title>NotebookLM</title></head>
    <body>
    <script>window.WIZ_global_data = {
        "SNlM0e": "test_csrf_token_123",
        "FdrFJe": "test_session_id_456"
    }</script>
    </body>
    </html>
    """


@pytest.fixture
def mock_list_notebooks_response():
    inner_data = json.dumps(
        [
            [
                [
                    "My First Notebook",
                    [],
                    "nb_001",
                    "📘",
                    None,
                    [None, None, None, None, None, [1704067200, 0]],
                ],
                [
                    "Research Notes",
                    [],
                    "nb_002",
                    "📚",
                    None,
                    [None, None, None, None, None, [1704153600, 0]],
                ],
            ]
        ]
    )
    rpc_id = RPCMethod.LIST_NOTEBOOKS.value
    chunk = json.dumps([["wrb.fr", rpc_id, inner_data, None, None]])
    return f")]}}'\n{len(chunk)}\n{chunk}\n"


@pytest.fixture
def build_rpc_response():
    """Factory for building RPC responses.

    Args:
        rpc_id: Either an RPCMethod enum or string RPC ID.
        data: The response data to encode.
    """

    def _build(rpc_id: RPCMethod | str, data) -> str:
        # Convert RPCMethod to string value if needed
        rpc_id_str = rpc_id.value if isinstance(rpc_id, RPCMethod) else rpc_id
        inner = json.dumps(data)
        chunk = json.dumps(["wrb.fr", rpc_id_str, inner, None, None])
        return f")]}}'\n{len(chunk)}\n{chunk}\n"

    return _build
