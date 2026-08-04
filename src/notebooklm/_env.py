"""Internal environment/default resolvers for NotebookLM runtime behavior.

Centralises lookup of environment variables that influence the live behavior
of the client. Keeping these here avoids scattering ``os.environ.get`` calls
across the codebase and gives each override a single, documented entry point.

This is an implementation module. Public configuration imports stay on
``notebooklm.config``, which deliberately re-exports only the supported subset
of endpoint/language helpers from here.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

DEFAULT_BASE_URL = "https://notebook.google.com"
PERSONAL_BASE_HOST = "notebook.google.com"
ENTERPRISE_BASE_HOST = "notebooklm.cloud.google.com"

# The pre-rebrand personal host. Still served, still selectable via
# ``NOTEBOOKLM_BASE_URL``, and documented as the rollback lever for the #2067
# default flip -- Google dual-serves ``batchexecute`` on both personal hosts
# (ADR-0028), so pointing back at this one is a supported configuration rather
# than a workaround.
#
# It has its own literal-valued constant on purpose. Naming the default and the
# non-default host with one constant apiece is what keeps
# :data:`PERSONAL_APP_HOSTS` a two-element set; folding them together collapses
# it to one element and silently un-fixes #2015/#2020/#2038.
#
# Must stay a direct string literal: ``tests/_guardrails/
# test_app_host_literals_centralized.py`` reads it out of this module by AST.
PERSONAL_LEGACY_HOST = "notebooklm.google.com"

# Both hosts the personal app is served from. Built from the two literal-valued
# constants above -- never derive either from the other, and never derive them
# from this set: a ``frozenset`` cannot say which host plays which role, and the
# AST lint above requires both to be plain literals.
PERSONAL_APP_HOSTS = frozenset({PERSONAL_BASE_HOST, PERSONAL_LEGACY_HOST})

# Guard rather than derivation (#2067). If a future edit makes the two constants
# equal, this fails at import instead of silently shrinking every accept-set
# built from ``PERSONAL_APP_HOSTS``.
#
# Deliberately a raise, not an ``assert``: ``python -O`` strips assertions, so
# an assert would evaporate in exactly the optimized deployments where a silent
# one-element accept-set is hardest to notice. The guardrail test covers this at
# development time; this covers it at runtime.
if len(PERSONAL_APP_HOSTS) != 2:  # pragma: no cover - import-time invariant
    raise RuntimeError(
        "PERSONAL_BASE_HOST and PERSONAL_LEGACY_HOST must name different hosts; "
        "a one-element PERSONAL_APP_HOSTS silently breaks the login accept-set"
    )

_ALLOWED_BASE_HOSTS = PERSONAL_APP_HOSTS | {ENTERPRISE_BASE_HOST}


def get_base_url() -> str:
    """Return the configured NotebookLM base URL.

    ``NOTEBOOKLM_BASE_URL`` is constrained to known Google-owned NotebookLM hosts
    because the value is used for authenticated requests.
    """
    configured = os.environ.get("NOTEBOOKLM_BASE_URL")
    raw = (configured.strip() if configured is not None else DEFAULT_BASE_URL).rstrip("/")
    if not raw:
        raw = DEFAULT_BASE_URL
    parsed = urlparse(raw)
    path = parsed.path.rstrip("/")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("NOTEBOOKLM_BASE_URL has an invalid port") from exc
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or host is None
        or host not in _ALLOWED_BASE_HOSTS
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        allowed = ", ".join(sorted(_ALLOWED_BASE_HOSTS))
        raise ValueError(f"NOTEBOOKLM_BASE_URL must use https and one of: {allowed}")
    return f"https://{host}"


def get_base_host() -> str:
    """Return the configured NotebookLM host."""
    return urlparse(get_base_url()).hostname or PERSONAL_BASE_HOST


DEFAULT_BL = "boq_labs-tailwind-frontend_20260301.03_p0"


def get_default_bl() -> str:
    """Return the NotebookLM ``bl`` (build label) URL parameter value.

    Reads the ``NOTEBOOKLM_BL`` environment variable; surrounding whitespace
    is stripped. Unset, empty, or whitespace-only values fall back to
    :data:`DEFAULT_BL`.

    The ``bl`` parameter is sent on the chat streaming endpoint
    (``ChatAPI.ask``) and pins the frontend build the request is associated
    with. Override via ``NOTEBOOKLM_BL`` when chasing a regression tied to
    a specific build snapshot.
    """
    raw = os.environ.get("NOTEBOOKLM_BL", "") or ""
    return raw.strip() or DEFAULT_BL


def get_default_language() -> str:
    """Return the user's preferred interface language.

    Reads the ``NOTEBOOKLM_HL`` environment variable. Surrounding whitespace
    is stripped; unset, empty, or whitespace-only values fall back to ``"en"``.

    This value is threaded into two places:

    * The ``hl`` URL query parameter on every batchexecute RPC call
      (``RpcExecutor.build_url`` and
      ``_chat.wire.build_streaming_chat_request``).
    * Language-aware ``ArtifactsAPI.generate_*`` calls when callers pass
      ``language=None`` to opt in to environment/default resolution. Omitting
      ``language`` in the public Python API keeps the historical ``"en"``
      artifact-language default.
    """
    raw = os.environ.get("NOTEBOOKLM_HL", "") or ""
    return raw.strip() or "en"
