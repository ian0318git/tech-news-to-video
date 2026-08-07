#!/usr/bin/env python3
"""Daily pipeline(per-channel): fetch_news → rank_news → collect_sources。

用法:
  python scripts/run_daily.py                      # 跑 config 中所有頻道
  python scripts/run_daily.py --channel tech       # 只跑 tech 頻道
  python scripts/run_daily.py --skip-fetch         # 跳過抓取

子步驟以 import 呼叫(同程序執行,注入 argv)— 不 spawn 子程序,
路徑與 cwd / 啟動方式無關;腳本改名時錯誤在 import 當下顯現。
"""

import importlib
import sys
from pathlib import Path

# 確保 scripts/ 在 import 路徑上 — 與 cwd、啟動方式無關
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _common import (  # noqa: E402 — sys.path 插入必須在匯入前
    flag_value,
    load_channels,
    load_env,
    resolve_channel,
    setup_logging,
)

STEPS = {"fetch": "fetch_news", "rank": "rank_news", "collect": "collect_sources"}


def run(module_name: str, args: list[str], logger) -> None:
    """同程序呼叫步驟腳本的 main()(注入 argv)。非零 exit code 向外傳播。"""
    logger.info(f"===== 執行 {module_name} {' '.join(args)} =====")
    module = importlib.import_module(module_name)
    old_argv = sys.argv
    sys.argv = [f"{module_name}.py", *args]
    try:
        module.main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code != 0:
            logger.error(
                f"[FAIL] {module_name} 失敗 (exit {code}) — 請查 logs/{module_name}.log"
            )
            sys.exit(code)
    finally:
        sys.argv = old_argv
    logger.info(f"===== {module_name} 完成 =====")


def main() -> None:
    load_env()
    logger = setup_logging("run_daily")
    args = sys.argv[1:]
    slug = flag_value(args, "--channel")
    skip = {arg.removeprefix("--skip-") for arg in args if arg.startswith("--skip-")}
    steps = [s for s in STEPS if s not in skip]
    if not steps:
        logger.error("所有步驟都被跳過,沒有事可做")
        sys.exit(1)

    # 未知 slug 會 fail,不會靜默 PASS
    targets = [resolve_channel(slug, logger)] if slug else load_channels(logger)
    for ch in targets:
        logger.info(f"########## 頻道: {ch['slug']} ({ch['keyword']}) ##########")
        for step in steps:
            run(STEPS[step], ["--channel", ch["slug"]], logger)
    logger.info(
        "[PASS] Daily pipeline 完成 — 下一步: scripts/run_video_pipeline.py --channel <slug>"
    )


if __name__ == "__main__":
    main()
