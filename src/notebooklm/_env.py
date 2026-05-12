"""Runtime environment helpers for NotebookLM endpoints."""

from __future__ import annotations

import os
from urllib.parse import urlparse

DEFAULT_BASE_URL = "https://notebooklm.google.com"
PERSONAL_BASE_HOST = "notebooklm.google.com"
ENTERPRISE_BASE_HOST = "notebooklm.cloud.google.com"

_ALLOWED_BASE_HOSTS = frozenset({PERSONAL_BASE_HOST, ENTERPRISE_BASE_HOST})


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
