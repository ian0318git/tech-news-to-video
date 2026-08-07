#!/usr/bin/env python3
"""Step 2 — master-token 認證檢查。

前置條件: master_token.json 已 scp 到 ~/.notebooklm/profiles/default/
(見 AUTH_MASTER_TOKEN.md Step 2)。

做的事:
1. 確認 master_token.json 就位、權限為 0600、NOTEBOOKLM_MASTER_TOKEN_JSON 未設定
2. notebooklm login --master-token-refresh  (從 token mint 出 session cookie)
3. notebooklm auth check --test --json  → 驗證 "status": "ok"

失敗即停,不會往下猜。
"""

import json
import os
from pathlib import Path

from _common import fail, run_cli, setup_logging

logger = setup_logging("check_auth")

TOKEN_FILE = Path.home() / ".notebooklm" / "profiles" / "default" / "master_token.json"


def main() -> None:
    # 1. master_token.json 就位檢查
    if not TOKEN_FILE.exists():
        fail(
            logger,
            f"master_token.json 不存在: {TOKEN_FILE}",
            "請依 AUTH_MASTER_TOKEN.md 的 Step 1–2 產生並 scp 到此 VM",
        )
    perms = oct(TOKEN_FILE.stat().st_mode & 0o777)
    if perms != "0o600":
        fail(
            logger,
            f"master_token.json 權限應為 600,目前 {perms}",
            f"請執行: chmod 600 {TOKEN_FILE}",
        )
    if "NOTEBOOKLM_MASTER_TOKEN_JSON" in os.environ:
        fail(
            logger,
            "環境變數 NOTEBOOKLM_MASTER_TOKEN_JSON 已設定,login 會拒絕執行",
            "請先執行: unset NOTEBOOKLM_MASTER_TOKEN_JSON",
        )
    logger.info(f"[OK] master_token.json 就位 ({perms}, {TOKEN_FILE})")

    # 2. 從 master token mint 出 session cookie
    run_cli(["login", "--master-token-refresh"], logger)

    # 3. 網路層認證驗證
    out = run_cli(["auth", "check", "--test", "--json"], logger)
    try:
        result = json.loads(out)
    except json.JSONDecodeError:
        fail(logger, "auth check 輸出不是有效 JSON", out)

    status = result.get("status")
    if status == "ok":
        logger.info("[PASS] 認證檢查通過: status = ok")
    else:
        fail(
            logger,
            f"認證檢查未通過: status = {status}",
            json.dumps(result, ensure_ascii=False, indent=2),
        )


if __name__ == "__main__":
    main()
