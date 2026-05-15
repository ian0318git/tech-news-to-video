"""Unit tests for ``tests/vcr_config.py`` custom VCR matchers.

The ``_freq_body_matcher`` decodes the form-encoded ``f.req`` payload that
streaming endpoints (notably streaming chat) use to disambiguate otherwise
identical POSTs. See the matcher's docstring for the full match-rule rationale.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

# Load ``tests/vcr_config.py`` via ``importlib`` rather than mutating
# ``sys.path``. The ``tests`` directory is not a package (no ``__init__.py``),
# so a plain ``from tests.vcr_config import _freq_body_matcher`` fails; a
# ``sys.path`` insertion would work but is module-load-time side-effectful and
# would silently shadow any future top-level module named ``vcr_config``.
# Loading by file path keeps the dependency localized to this test module
# (mirrors the pattern used in ``tests/unit/test_cookie_redaction.py``).
_vcr_config_path = Path(__file__).resolve().parent.parent / "vcr_config.py"
_spec = importlib.util.spec_from_file_location("tests_vcr_config", _vcr_config_path)
assert _spec is not None and _spec.loader is not None, (
    f"Could not load tests/vcr_config.py from {_vcr_config_path}"
)
_vcr_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_vcr_config)
_freq_body_matcher: Callable[[Any, Any], bool] = _vcr_config._freq_body_matcher


class _StubRequest:
    """Minimal stand-in for a ``vcr.request.Request`` with just the ``body`` attr.

    The matcher reads only ``request.body``, so we don't need any of the other
    request plumbing for these unit tests.
    """

    def __init__(self, body: Any) -> None:
        self.body = body


def _build_freq_body(params: list[Any]) -> str:
    """Build the same ``application/x-www-form-urlencoded`` body shape the
    NotebookLM streaming-chat endpoint sends.

    The wire format is ``f.req=<url-encoded JSON envelope>&at=<csrf>`` where the
    JSON envelope is ``[null, "<inner_json>"]`` and ``<inner_json>`` is itself a
    JSON-encoded list of positional parameters.
    """
    inner_json = json.dumps(params, separators=(",", ":"))
    envelope = json.dumps([None, inner_json], separators=(",", ":"))
    return f"f.req={quote(envelope, safe='')}&at=mock_csrf_token"


# Canonical 9-param shape for the streaming-chat endpoint:
#   slot 0: leading null
#   slot 1: question text
#   slot 2: null
#   slot 3: feature bitmask
#   slot 4: conversation_id  <- legitimately varies; matcher MUST ignore
#   slot 5: null
#   slot 6: null
#   slot 7: notebook_id      <- matcher MUST check
#   slot 8: trailing flag
def _nine_params(
    question: str = "What is this notebook about?",
    conv_id: str = "conv_abc",
    notebook_id: str = "nb_xyz",
) -> list[Any]:
    return [None, question, None, [2], conv_id, None, None, notebook_id, 1]


def test_freq_matcher_identical_nine_param_match() -> None:
    """Two requests with the exact same 9-param shape match."""
    params = _nine_params()
    r1 = _StubRequest(_build_freq_body(params))
    r2 = _StubRequest(_build_freq_body(params))
    assert _freq_body_matcher(r1, r2) is True


def test_freq_matcher_param_count_mismatch_nine_vs_five() -> None:
    """A 9-param request must not match a 5-param request (C3 regression)."""
    nine = _nine_params()
    five = [None, "What is this notebook about?", None, [2], "conv_abc"]
    r1 = _StubRequest(_build_freq_body(nine))
    r2 = _StubRequest(_build_freq_body(five))
    assert _freq_body_matcher(r1, r2) is False


def test_freq_matcher_notebook_id_mismatch_at_slot_seven() -> None:
    """Differing notebook_id at slot 7 must NOT match (distinct interactions)."""
    p1 = _nine_params(notebook_id="nb_alpha")
    p2 = _nine_params(notebook_id="nb_beta")
    r1 = _StubRequest(_build_freq_body(p1))
    r2 = _StubRequest(_build_freq_body(p2))
    assert _freq_body_matcher(r1, r2) is False


def test_freq_matcher_conversation_id_difference_still_matches() -> None:
    """Differing conversation_id at slot 4 DOES match — conv_id varies per replay."""
    p1 = _nine_params(conv_id="conv_recorded_at_t1")
    p2 = _nine_params(conv_id="conv_recorded_at_t2")
    r1 = _StubRequest(_build_freq_body(p1))
    r2 = _StubRequest(_build_freq_body(p2))
    assert _freq_body_matcher(r1, r2) is True


def test_freq_matcher_handles_bytes_body() -> None:
    """The matcher should transparently decode ``bytes`` request bodies.

    VCR's request.body is bytes for recorded requests, so we exercise that path
    explicitly to prevent a TypeError regression in production replay.
    """
    params = _nine_params()
    body_text = _build_freq_body(params)
    r1 = _StubRequest(body_text.encode("utf-8"))
    r2 = _StubRequest(body_text.encode("utf-8"))
    assert _freq_body_matcher(r1, r2) is True


def test_freq_matcher_both_bodies_unparseable_defers_to_other_matchers() -> None:
    """Two requests neither carrying f.req return True (defer to other matchers).

    Covers the (unlikely) case where this opt-in matcher is consulted for a
    non-streaming request. Returning True keeps VCR's other matchers
    (method/path/etc.) in charge of the decision; returning False would
    incorrectly block matches on every non-streaming request the cassette
    contains.
    """
    r1 = _StubRequest("at=foo&other=bar")
    r2 = _StubRequest("at=baz&other=qux")
    assert _freq_body_matcher(r1, r2) is True


def test_freq_matcher_one_unparseable_one_parseable_rejects() -> None:
    """A parseable f.req body must not match a body that lacks f.req.

    Structurally different requests should not be silently collapsed even when
    one side is "no f.req at all".
    """
    parseable = _StubRequest(_build_freq_body(_nine_params()))
    no_f_req = _StubRequest("at=foo&other=bar")
    assert _freq_body_matcher(parseable, no_f_req) is False
    assert _freq_body_matcher(no_f_req, parseable) is False
