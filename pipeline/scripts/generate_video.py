#!/usr/bin/env python3
"""Step 4a — 生成 Video Overview(Explainer 格式)。

以 --wait 阻塞等待生成完成,即時印出進度/狀態,
--timeout 1800 = 30 分鐘的等待預算(video 預設值)。
"""

import json

from _common import run_live, setup_logging

logger = setup_logging("generate_video")

DESCRIPTION = "Summarize the embedded Linux daily news from the notebook sources"
FORMAT = "explainer"  # Explainer,不選 Cinematic — 先求簡單能跑
WAIT_TIMEOUT = 1800  # 秒
POLL_INTERVAL = 2  # 秒


def main() -> None:
    out = run_live(
        [
            "generate",
            "video",
            DESCRIPTION,
            "--format",
            FORMAT,
            "--wait",
            "--timeout",
            str(WAIT_TIMEOUT),
            "--interval",
            str(POLL_INTERVAL),
            "--json",
        ],
        logger,
        timeout=WAIT_TIMEOUT + 120,  # 給 CLI 一些收尾餘裕
    )

    # CLI 可能輸出多行 pretty JSON — 找最後一個可解析的 JSON 物件作為終態
    final = None
    for line in out.splitlines():
        try:
            final = json.loads(line)
        except json.JSONDecodeError:
            continue
    if final is not None:
        logger.info(f"[INFO] 最終狀態: {json.dumps(final, ensure_ascii=False)}")
    else:
        logger.warning("[WARN] 輸出中沒有可解析的 JSON,以 CLI exit 0 為準")
        last_line = [ln for ln in out.strip().splitlines() if ln.strip()]
        if last_line:
            logger.info(f"[INFO] 最後輸出: {last_line[-1]}")

    logger.info("[PASS] Video Overview 生成完成(進入下載階段)")


if __name__ == "__main__":
    main()
