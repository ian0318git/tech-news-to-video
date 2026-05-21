"""CLI exit-path allowlist enforcement."""

from __future__ import annotations

import ast
from pathlib import Path

from notebooklm.cli.error_handler import (
    ALLOWED_CLICK_EXCEPTION_SITES,
    ALLOWED_RAW_SYSEXIT_SITES,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_ROOT = REPO_ROOT / "src" / "notebooklm" / "cli"

AllowedSite = tuple[str, int]


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _allowed_sites(entries: list[tuple[str, int, str]]) -> set[AllowedSite]:
    return {(path, line) for path, line, _reason in entries}


def _format_sites(sites: set[AllowedSite]) -> str:
    return "\n".join(f"{path}:{line}" for path, line in sorted(sites))


def _raw_system_exit_sites() -> set[AllowedSite]:
    sites: set[AllowedSite] = set()
    for path in sorted(CLI_ROOT.rglob("*.py")):
        if path.name == "error_handler.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            if _call_name(node.exc.func) == "SystemExit":
                sites.add((rel_path, node.lineno))
    return sites


def _click_exception_call_sites() -> set[AllowedSite]:
    sites: set[AllowedSite] = set()
    for path in sorted(CLI_ROOT.rglob("*.py")):
        if path.name == "error_handler.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node.func) == "click.ClickException":
                sites.add((rel_path, node.lineno))
    return sites


def test_raw_system_exit_sites_are_error_handler_owned_or_allowlisted() -> None:
    """Raw ``SystemExit`` outside ``error_handler.py`` must stay exceptional."""
    actual = _raw_system_exit_sites()
    allowed = _allowed_sites(ALLOWED_RAW_SYSEXIT_SITES)

    assert len(actual) <= 5
    assert actual <= allowed, "Unallowlisted raw SystemExit sites:\n" + _format_sites(
        actual - allowed
    )
    assert allowed <= actual, "Stale raw SystemExit allowlist entries:\n" + _format_sites(
        allowed - actual
    )


def test_click_exception_sites_match_input_validation_allowlist() -> None:
    """``click.ClickException`` is limited to documented input-validation sites."""
    actual = _click_exception_call_sites()
    allowed = _allowed_sites(ALLOWED_CLICK_EXCEPTION_SITES)

    assert actual <= allowed, "Unallowlisted click.ClickException sites:\n" + _format_sites(
        actual - allowed
    )
    assert allowed <= actual, "Stale click.ClickException allowlist entries:\n" + _format_sites(
        allowed - actual
    )
