#!/usr/bin/env python3
"""Step 3a — 建立測試 Notebook。

建立 "Embedded Linux Daily News - POC",並把 notebook id 寫到
output/notebook_id.txt(供後續腳本與手動檢查使用)。
"""

import json
from pathlib import Path

from _common import NOTEBOOK_ID_FILE, fail, run_cli, save_json, setup_logging

logger = setup_logging("create_notebook")

TITLE = "Embedded Linux Daily News - POC"


def main() -> None:
    # --use: 建立後設為 active context,後續 source/generate 指令自動套用
    out = run_cli(["create", TITLE, "--use", "--json"], logger)
    try:
        result = json.loads(out)
    except json.JSONDecodeError:
        fail(logger, "create 輸出不是有效 JSON", out)

    nb = result.get("notebook") or {}
    nb_id = result.get("notebook_id") or nb.get("id") or result.get("id")
    if not nb_id:
        fail(logger, "無法從回應取得 notebook id", out)

    NOTEBOOK_ID_FILE.write_text(str(nb_id), encoding="utf-8")
    save_json(result, Path(__file__).resolve().parent.parent / "output" / "create_notebook.json")
    logger.info(f"[PASS] Notebook 已建立: id={nb_id}  title={TITLE!r}")
    logger.info(f"      notebook id 已存到 {NOTEBOOK_ID_FILE}")


if __name__ == "__main__":
    main()
