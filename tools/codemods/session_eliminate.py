"""Temporary codemod for Session elimination.

This is intentionally narrow and repo-specific. It is added, tested, run,
committed, and deleted in the same Phase 3 PR.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


HELPER_REPLACEMENTS = {
    "from _helpers.session_factory import build_session_for_tests": (
        "from _helpers.client_factory import build_client_shell_for_tests"
    ),
    "from _helpers.client_factory import build_client_for_tests": (
        "from _helpers.client_factory import build_client_shell_for_tests"
    ),
    "build_session_for_tests": "build_client_shell_for_tests",
    "build_client_for_tests": "build_client_shell_for_tests",
}

STRING_TARGET_REPLACEMENTS = {
    "notebooklm._session.asyncio.sleep": "notebooklm._session_helpers.asyncio.sleep",
    "notebooklm._session.random.uniform": "notebooklm._backoff._random.uniform",
    "notebooklm._session.httpx.AsyncClient": "notebooklm._session_init.httpx.AsyncClient",
}

SESSION_CHAIN_REPLACEMENTS = {
    "auth": "_auth",
    "cookie_persistence": "_collaborators.cookie_persistence",
    "_kernel": "_collaborators.kernel",
    "_lifecycle": "_collaborators.lifecycle",
    "_auth_coord": "_collaborators.auth_coord",
    "_drain_tracker": "_collaborators.drain_tracker",
    "_reqid": "_collaborators.reqid",
    "_metrics_obj": "_collaborators.metrics",
    "_transport": "_composed.transport",
    "_chain_host": "_composed.chain_host",
    "_chain_builder": "_composed.chain_builder",
    "_middlewares": "_composed.middlewares",
    "_rpc_executor": "_rpc_executor",
    "_seams": "_seams",
}

DIRECT_SHELL_REPLACEMENTS = {
    "auth": "_auth",
    "cookie_persistence": "_collaborators.cookie_persistence",
    "_kernel": "_collaborators.kernel",
    "_lifecycle": "_collaborators.lifecycle",
    "_auth_coord": "_collaborators.auth_coord",
    "_drain_tracker": "_collaborators.drain_tracker",
    "_reqid": "_collaborators.reqid",
    "_metrics_obj": "_collaborators.metrics",
    "_transport": "_composed.transport",
    "_chain_host": "_composed.chain_host",
    "_chain_builder": "_composed.chain_builder",
    "_middlewares": "_composed.middlewares",
    "_rpc_executor": "_rpc_executor",
    "_seams": "_seams",
}


@dataclass(frozen=True)
class UnsupportedPattern:
    file: str
    line: int
    pattern_kind: str
    suggested_manual_target: str


def _line_number(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def _replace_helper_names(source: str) -> str:
    for before, after in HELPER_REPLACEMENTS.items():
        source = source.replace(before, after)
    return source


def _replace_session_type_import(source: str) -> str:
    if "from notebooklm._session import Session" not in source:
        return source
    source = source.replace(
        "from notebooklm._session import Session",
        "from notebooklm.client import NotebookLMClient",
    )
    return re.sub(r"\bSession\b", "NotebookLMClient", source)


def _replace_string_targets(source: str) -> str:
    for before, after in STRING_TARGET_REPLACEMENTS.items():
        source = source.replace(before, after)
    return source


def _replace_direct_session_chains(source: str) -> str:
    for old_attr, new_path in SESSION_CHAIN_REPLACEMENTS.items():
        source = source.replace(f"._session.{old_attr}", f".{new_path}")
    return source


def _assigned_shell_vars(source: str) -> set[str]:
    shell_vars: set[str] = set()
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*build_client_shell_for_tests\(", source):
        shell_vars.add(match.group(1))
    return shell_vars


def _rewrite_session_aliases(source: str, shell_vars: set[str]) -> tuple[str, set[str]]:
    alias_re = re.compile(
        r"^(?P<indent>\s*)(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
        r"(?P<client>[A-Za-z_][A-Za-z0-9_]*)\._session\s*$",
        re.MULTILINE,
    )
    aliases: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        alias = match.group("alias")
        client = match.group("client")
        aliases.add(alias)
        return f"{match.group('indent')}{alias} = {client}"

    rewritten = alias_re.sub(replace, source)
    return rewritten, shell_vars | aliases


def _replace_shell_var_reaches(source: str, shell_vars: Iterable[str]) -> str:
    for var in sorted(set(shell_vars), key=len, reverse=True):
        for old_attr, new_path in DIRECT_SHELL_REPLACEMENTS.items():
            source = re.sub(
                rf"\b{re.escape(var)}\.{re.escape(old_attr)}\b",
                f"{var}.{new_path}",
                source,
            )
        source = re.sub(
            rf"await {re.escape(var)}\.drain\((?P<args>[^)]*)\)",
            rf"await {var}._collaborators.drain_tracker.drain(\g<args>)",
            source,
        )
        source = re.sub(
            rf"\b{re.escape(var)}\.is_open\b",
            f"{var}._collaborators.lifecycle.is_open()",
            source,
        )
    return source


def _report_remaining(source: str, file: str) -> list[UnsupportedPattern]:
    checks = [
        (r"\._session\b", "session_attribute", "rewrite to client collaborator/composed holder"),
        (r"notebooklm\._session(\.|['\"])", "deleted_module_string", "use surviving sibling module"),
        (r"build_(?:session|client)_for_tests", "old_helper_name", "use build_client_shell_for_tests"),
        (r"from notebooklm\._session import", "session_import", "use NotebookLMClient typing"),
        (r"\bSession\b", "session_type_name", "use NotebookLMClient or remove stale prose"),
    ]
    unsupported: list[UnsupportedPattern] = []
    for pattern, kind, target in checks:
        for match in re.finditer(pattern, source):
            unsupported.append(
                UnsupportedPattern(
                    file=file,
                    line=_line_number(source, match.start()),
                    pattern_kind=kind,
                    suggested_manual_target=target,
                )
            )
    return unsupported


def transform_source(source: str, *, file: str = "<memory>") -> tuple[str, list[UnsupportedPattern]]:
    rewritten = _replace_session_type_import(source)
    rewritten = _replace_helper_names(rewritten)
    rewritten = _replace_string_targets(rewritten)
    shell_vars = _assigned_shell_vars(rewritten)
    rewritten, shell_vars = _rewrite_session_aliases(rewritten, shell_vars)
    rewritten = _replace_direct_session_chains(rewritten)
    rewritten = _replace_shell_var_reaches(rewritten, shell_vars)
    unsupported = _report_remaining(rewritten, file)
    return rewritten, unsupported


def iter_python_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(
                p
                for p in path.rglob("*.py")
                if "__pycache__" not in p.parts and not p.name.endswith(".pyc")
            )
        elif path.suffix == ".py":
            files.append(path)
    return sorted(set(files))


def run(paths: Iterable[Path], *, apply: bool) -> tuple[bool, list[UnsupportedPattern]]:
    changed = False
    unsupported: list[UnsupportedPattern] = []
    for path in iter_python_files(paths):
        source = path.read_text(encoding="utf-8")
        rewritten, file_unsupported = transform_source(source, file=path.as_posix())
        unsupported.extend(file_unsupported)
        if rewritten != source:
            changed = True
            if apply:
                path.write_text(rewritten, encoding="utf-8")
    return changed, unsupported


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.apply == args.check:
        parser.error("exactly one of --apply or --check is required")

    changed, unsupported = run(args.paths, apply=args.apply)
    if args.report is not None:
        args.report.write_text(
            json.dumps([asdict(item) for item in unsupported], indent=2) + "\n",
            encoding="utf-8",
        )
    if unsupported:
        return 2
    if args.check and changed:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
