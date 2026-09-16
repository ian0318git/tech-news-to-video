#!/usr/bin/env python3
"""YouTube 唯讀授權(device flow) — 供頻道數據查詢使用。

用法:
  python scripts/youtube_read_auth.py           # 首次授權(唯讀)
  python scripts/youtube_read_auth.py --check   # 驗證現有唯讀權杖(會做一次 refresh)

與 youtube_auth.py 分離:此權杖只讀不寫,**不會動到生產中的上傳權杖**。
前置: output/client_secret.json 已就位(與上傳共用同一組 OAuth 憑證)。
輸出: output/youtube_read_token.json (0600)
"""

import sys

from _common import fail, setup_logging
from _youtube import load_client_secret
from _youtube_read import READ_TOKEN_PATH, ensure_read_token

logger = setup_logging("youtube_read_auth")


def main() -> None:
    if "--check" in sys.argv:
        if not READ_TOKEN_PATH.exists():
            fail(logger, "沒有唯讀 token — 請先執行 python scripts/youtube_read_auth.py")
        ensure_read_token(logger)
        logger.info("[PASS] 唯讀 token 有效(refresh 成功)")
        return

    load_client_secret(logger)  # 先驗證憑證存在,避免等到授權才發現缺檔案
    ensure_read_token(logger)
    logger.info("[PASS] 唯讀授權完成 — 可以執行 scripts/channel_stats.py")


if __name__ == "__main__":
    main()
