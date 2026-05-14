"""Tests for the public shim modules introduced by the private-module boundary plan.

Each section is owned by a different PR; please keep the markers below intact when
appending so concurrent PRs can merge cleanly.

Plan: .sisyphus/plans/private-module-boundary.md
"""

from __future__ import annotations

import importlib
from types import ModuleType

import pytest

# ---------------------------------------------------------------------------
# PR-A section: notebooklm.research public surface
# ---------------------------------------------------------------------------


def test_research_module_exposes_documented_helpers():
    """notebooklm.research re-exports the three free helpers used by the CLI."""
    from notebooklm.research import (
        extract_report_urls,
        normalize_url,
        select_cited_sources,
    )

    assert callable(extract_report_urls)
    assert callable(normalize_url)
    assert callable(select_cited_sources)


def test_cited_source_selection_is_on_public_surface():
    """CitedSourceSelection lives in notebooklm.types and on the top-level package."""
    from notebooklm import CitedSourceSelection as TopLevel
    from notebooklm.types import CitedSourceSelection

    assert TopLevel is CitedSourceSelection


def test_research_select_cited_sources_returns_public_dataclass():
    """select_cited_sources returns the public CitedSourceSelection dataclass."""
    from notebooklm.research import select_cited_sources
    from notebooklm.types import CitedSourceSelection

    result = select_cited_sources([], "")
    assert isinstance(result, CitedSourceSelection)
    assert result.used_fallback is True


def test_research_api_backward_compat_classmethod_delegates():
    """notebooklm._research.ResearchAPI.select_cited_sources still works."""
    from notebooklm._research import ResearchAPI
    from notebooklm.types import CitedSourceSelection

    result = ResearchAPI.select_cited_sources([], "")
    assert isinstance(result, CitedSourceSelection)


def test_research_api_reexports_cited_source_selection_for_back_compat():
    """notebooklm._research.CitedSourceSelection continues to resolve."""
    from notebooklm._research import CitedSourceSelection as Legacy
    from notebooklm.types import CitedSourceSelection

    assert Legacy is CitedSourceSelection


# ---------------------------------------------------------------------------
# PR-D section: notebooklm.config / notebooklm.urls / notebooklm.log public shims
# ---------------------------------------------------------------------------


def test_config_shim_exposes_documented_names(monkeypatch):
    # Guard against a NOTEBOOKLM_BASE_URL override leaking from the env,
    # so the assertion stays valid on developer machines and overridden CI.
    monkeypatch.delenv("NOTEBOOKLM_BASE_URL", raising=False)
    from notebooklm import config

    assert config.get_base_url() == config.DEFAULT_BASE_URL
    assert config.DEFAULT_BASE_URL == "https://notebooklm.google.com"


def test_urls_shim_exposes_documented_names():
    from notebooklm.urls import is_youtube_url

    assert is_youtube_url("https://www.youtube.com/watch?v=x") is True


def test_log_shim_exposes_install_redaction():
    from notebooklm.log import install_redaction

    assert callable(install_redaction)


# ---------------------------------------------------------------------------
# PR-T1.D section: __all__ contract tests for the public shim modules.
#
# Enforces, for each shim, that:
#   1. ``__all__`` exists.
#   2. Every name in ``__all__`` resolves via ``getattr``.
#   3. No name in ``__all__`` is private (leading underscore).
#   4. ``__all__`` is sorted case-insensitively (drift catcher).
#   5. ``__all__`` matches the actual re-exported public surface — no orphans,
#      no missing entries.
#   6. ``__all__`` contains no duplicate entries.
# ---------------------------------------------------------------------------


# (shim_module_name, internal_module_name)
# Note: notebooklm.research has targeted smoke tests in the PR-A section above
# and is intentionally excluded from this generic contract sweep.
_SHIM_PAIRS = [
    ("notebooklm.config", "notebooklm._env"),
    ("notebooklm.urls", "notebooklm._url_utils"),
    ("notebooklm.log", "notebooklm._logging"),
]


def _actual_reexports(shim: ModuleType, internal: ModuleType) -> set[str]:
    """Return public names on ``shim`` that point at the same object on ``internal``.

    A name is considered "re-exported" when both modules expose an attribute
    of the same identity. This catches accidental shadowing (a shim defining
    its own value) as well as truly re-exported symbols.

    Note: names imported under ``typing.TYPE_CHECKING`` are not visible to
    ``dir()`` at runtime, so type-only re-exports won't be detected. None of
    the current shims use TYPE_CHECKING re-exports.
    """
    sentinel = object()
    names: set[str] = set()
    for name in dir(shim):
        if name.startswith("_"):
            continue
        shim_obj = getattr(shim, name, sentinel)
        internal_obj = getattr(internal, name, sentinel)
        if shim_obj is sentinel or internal_obj is sentinel:
            continue
        if shim_obj is internal_obj:
            names.add(name)
    return names


@pytest.mark.parametrize(
    ("shim_name", "internal_name"),
    _SHIM_PAIRS,
    ids=[shim for shim, _ in _SHIM_PAIRS],
)
def test_public_shim_all_contract(shim_name: str, internal_name: str) -> None:
    shim = importlib.import_module(shim_name)
    internal = importlib.import_module(internal_name)

    # 1. __all__ exists.
    assert hasattr(shim, "__all__"), f"{shim_name} is missing __all__"
    all_list = shim.__all__
    assert isinstance(all_list, list), (
        f"{shim_name}.__all__ must be a list, got {type(all_list).__name__}"
    )

    # 2. Every name in __all__ is importable.
    for name in all_list:
        assert hasattr(shim, name), f"{shim_name}.__all__ references missing attribute {name!r}"

    # 3. No private names in __all__.
    private = [n for n in all_list if n.startswith("_")]
    assert not private, f"{shim_name}.__all__ leaks private names: {private}"

    # 4. __all__ sorted case-insensitively (drift catcher).
    expected_order = sorted(all_list, key=str.lower)
    assert list(all_list) == expected_order, (
        f"{shim_name}.__all__ is not sorted case-insensitively.\n"
        f"  actual:   {list(all_list)}\n"
        f"  expected: {expected_order}"
    )

    # 5. __all__ matches the actual public surface of the shim.
    declared = set(all_list)
    reexported = _actual_reexports(shim, internal)
    missing = reexported - declared
    orphans = declared - reexported
    assert not missing, (
        f"{shim_name}.__all__ is missing names re-exported from {internal_name}: {sorted(missing)}"
    )
    assert not orphans, (
        f"{shim_name}.__all__ contains orphans not re-exported from {internal_name}: "
        f"{sorted(orphans)}"
    )

    # 6. Length sanity: no duplicates in __all__.
    assert len(all_list) == len(declared), (
        f"{shim_name}.__all__ contains duplicates: {sorted(all_list)}"
    )
