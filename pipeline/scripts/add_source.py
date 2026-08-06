#!/usr/bin/env python3
"""Step 3b — 加入來源並驗證(冪等)。

加入固定測試網址 https://www.kernel.org/ 到 active notebook,
然後 source list --json 列出來源確認加入成功。

冪等設計: 若 URL 已存在於來源清單,直接跳過 add 只做驗證 —
方便單獨重跑失敗的步驟而不重複加入。
"""

import json
import sys

from _common import fail, run_cli, setup_logging

logger = setup_logging("add_source")

SOURCE_URL = "https://www.kernel.org/"


def list_sources() -> list:
    """Return the source list from `source list --json` (multiple envelope shapes)."""
    listing = run_cli(["source", "list", "--json"], logger)
    try:
        sources = json.loads(listing)
    except json.JSONDecodeError:
        fail(logger, "source list 輸出不是有效 JSON", listing)
    if isinstance(sources, list):
        return sources
    return sources.get("sources", sources.get("items", []))


def main() -> None:
    # 1. 先檢查是否已存在(冪等)
    items = list_sources()
    matches = [s for s in items if SOURCE_URL in str(s.get("url", ""))]
    if matches:
        logger.info(f"[INFO] 來源已存在({len(matches)} 筆),跳過 add,直接驗證")
    else:
        # 2. 加入來源
        out = run_cli(["source", "add", SOURCE_URL, "--timeout", "90", "--json"], logger)
        try:
            result = json.loads(out)
        except json.JSONDecodeError:
            fail(logger, "source add 輸出不是有效 JSON", out)
        logger.info(f"[INFO] source add 回應: {json.dumps(result, ensure_ascii=False)}")
        src = result.get("source", result)
        if not src.get("id"):
            fail(logger, "source add 回應中沒有 source id", out)

        # 3. 重新列出並驗證
        items = list_sources()
        matches = [s for s in items if SOURCE_URL in str(s.get("url", ""))]
        if not matches:
            fail(
                logger,
                f"來源清單中找不到 {SOURCE_URL}",
                f"完整清單:\n{json.dumps(items, ensure_ascii=False, indent=2)}",
            )

    for m in matches:
        logger.info(
            f"[PASS] 來源已確認: id={m.get('id')}  "
            f"title={m.get('title')}  status={m.get('status')}"
        )


if __name__ == "__main__":
    main()
