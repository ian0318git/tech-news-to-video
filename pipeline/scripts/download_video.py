#!/usr/bin/env python3
"""Step 4b — 下載 Video Overview 成 output/video.mp4。

用 --latest 抓最新一個 video artifact(--no-clobber 避免覆蓋已有檔案)。
"""

import os
import sys

from _common import VIDEO_FILE, fail, run_cli, setup_logging

logger = setup_logging("download_video")


def main() -> None:
    run_cli(["download", "video", str(VIDEO_FILE), "--latest", "--no-clobber"], logger)

    if not VIDEO_FILE.exists():
        fail(logger, f"下載後檔案不存在: {VIDEO_FILE}")

    size = VIDEO_FILE.stat().st_size
    logger.info(f"[PASS] 影片已下載: {VIDEO_FILE}")
    logger.info(f"      大小: {size:,} bytes ({size / 1024 / 1024:.1f} MB)")
    if size == 0:
        fail(logger, "檔案大小為 0 bytes — 下載可能失敗")
    if size < 1024 * 1024:  # < 1MB 對影片而言異常小
        logger.warning(f"[WARN] 檔案大小 {size:,} bytes 小於 1MB,請用 file 指令檢查格式")


if __name__ == "__main__":
    main()
