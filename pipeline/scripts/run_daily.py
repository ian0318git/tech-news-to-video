#!/usr/bin/env python3
"""Daily pipeline(per-channel): fetch_news → rank_news → collect_sources。

用法:
  python scripts/run_daily.py                      # 跑 config 中所有頻道
  python scripts/run_daily.py --channel tech       # 只跑 tech 頻道
  python scripts/run_daily.py --skip-fetch         # 跳過抓取

每一支子腳本都有獨立 log(logs/<step>.log),失敗即停。
"""

import subprocess
import sys

from _common import flag_value, load_channels, load_env, setup_logging

logger = setup_logging("run_daily")

SCRIPTS = {"fetch": "fetch_news.py", "rank": "rank_news.py", "collect": "collect_sources.py"}


def run(script: str, args: list[str] | None = None) -> None:
    cmd = [sys.executable, f"{sys.path[0]}/{script}", *(args or [])]
    logger.info(f"===== 執行 {script} {' '.join(args or [])} =====")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        logger.error(f"[FAIL] {script} 失敗 (exit {proc.returncode}) — 請查 logs/{script.replace('.py', '')}.log")
        sys.exit(proc.returncode)
    logger.info(f"===== {script} 完成 =====")


def main() -> None:
    load_env()
    args = sys.argv[1:]
    slug = flag_value(args, "--channel")
    skip = {arg.removeprefix("--skip-") for arg in args if arg.startswith("--skip-")}
    steps = [s for s in SCRIPTS if s not in skip]
    if not steps:
        logger.error("所有步驟都被跳過,沒有事可做")
        sys.exit(1)

    channels = load_channels(logger)
    targets = [c for c in channels if c["slug"] == slug] if slug else channels
    for ch in targets:
        logger.info(f"########## 頻道: {ch['slug']} ({ch['keyword']}) ##########")
        for step in steps:
            run(SCRIPTS[step], ["--channel", ch["slug"]])
    logger.info("[PASS] Daily pipeline 完成 — 下一步: scripts/run_video_pipeline.py --channel <slug>")


if __name__ == "__main__":
    main()
