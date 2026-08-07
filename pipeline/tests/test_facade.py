"""facade 完整性測試: 步驟腳本從 _common 匯入的每個 symbol 都必須存在。

防止「搬了函式但忘了 re-export」的靜默斷裂 — 重構安全網。
"""

import ast
import glob
from pathlib import Path

import _common

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def test_facade_exports_every_symbol_scripts_import():
    needed = set()
    for f in glob.glob(str(SCRIPTS_DIR / "*.py")):
        tree = ast.parse(open(f, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "_common":
                needed.update(a.name for a in node.names)

    assert needed, "沒有掃到任何 from _common import — 掃描邏輯可能壞了"
    missing = sorted(needed - set(dir(_common)))
    assert not missing, f"_common facade 缺少這些 symbol: {missing}"
