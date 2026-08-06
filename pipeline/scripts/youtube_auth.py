#!/usr/bin/env python3
"""YouTube 首次認證(device flow)與 token 檢查。

用法:
  python scripts/youtube_auth.py             # 首次授權(會印出網址+代碼)
  python scripts/youtube_auth.py --check     # 驗證現有 token(會做一次 refresh)

前置: output/client_secret.json 已就位(Google Cloud → OAuth 用戶端 ID → 桌面應用程式)。
輸出: output/youtube_token.json (0600)
"""

import json
import sys

from _common import OUTPUT_DIR, fail, setup_logging
from _youtube import TOKEN_PATH, ensure_access_token, load_client_secret

logger = setup_logging("youtube_auth")


def main() -> None:
    if "--check" in sys.argv:
        if not TOKEN_PATH.exists():
            fail(logger, f"沒有 token — 請先執行 python scripts/youtube_auth.py")
        token = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
        logger.info(f"[INFO] token 存在: 帳戶需在下次上傳時由 API 驗證")
        ensure_access_token(logger)
        logger.info("[PASS] token 有效(refresh 成功)")
        return

    load_client_secret(logger)  # 先驗證憑證存在,避免等到授權才發現缺檔案
    ensure_access_token(logger)
    logger.info("[PASS] 首次認證完成 — 可以執行 scripts/youtube_upload.py")


if __name__ == "__main__":
    main()
