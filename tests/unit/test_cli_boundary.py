"""Lint: cli/ files must not import notebooklm._* (private modules).

Allowed:
- intra-cli imports like `from ._encoding import ...` or `from ._firefox_containers import ...`
- imports of non-underscored siblings/parents (e.g., `from ..types import ...`, `from ..research import ...`)
"""

from __future__ import annotations

import ast
import pathlib

CLI_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "notebooklm" / "cli"


def _violations(tree: ast.AST) -> list[str]:
    bad: list[str] = []
    for node in ast.walk(tree):
        # `from X import …`
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level == 0:
                # `from notebooklm._foo import ...`
                parts = mod.split(".")
                if len(parts) >= 2 and parts[0] == "notebooklm" and parts[1].startswith("_"):
                    bad.append(f"from {mod} import ...")
                # `from notebooklm import _foo` — private module via imported names.
                # Dunders like `__version__` are public package attrs by convention.
                if mod == "notebooklm":
                    for alias in node.names:
                        if alias.name.startswith("_") and not alias.name.startswith("__"):
                            bad.append(f"from notebooklm import {alias.name}")
            elif node.level >= 2:
                # `from .._foo import ...` (parent-package private)
                if mod.startswith("_"):
                    bad.append(f"from {'.' * node.level}{mod} import ...")
                # `from .. import _foo` — private parent module via imported names.
                # Dunders like `__version__` are public package attrs by convention.
                if mod == "":
                    for alias in node.names:
                        if alias.name.startswith("_") and not alias.name.startswith("__"):
                            bad.append(f"from {'.' * node.level} import {alias.name}")
            # level == 1 is intra-cli; underscore there is fine (cli's own private modules)
        # `import X` / `import X as Y`
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if len(parts) >= 2 and parts[0] == "notebooklm" and parts[1].startswith("_"):
                    bad.append(f"import {alias.name}")
    return bad


def test_no_private_module_imports_in_cli():
    offenders: list[tuple[str, list[str]]] = []
    for path in sorted(CLI_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bad = _violations(tree)
        if bad:
            offenders.append((str(path.relative_to(CLI_ROOT.parent)), bad))
    assert not offenders, (
        "CLI must not import notebooklm._* (private). "
        "Promote needed symbols to a public module (config/urls/log/research) "
        f"and import from there.\nOffenders: {offenders}"
    )
