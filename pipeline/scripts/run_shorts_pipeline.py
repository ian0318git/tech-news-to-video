#!/usr/bin/env python3
"""每日 Shorts(per-channel): 60 秒直式影片(冪等)。

用法: python scripts/run_shorts_pipeline.py [--channel tech]
薄 wrapper: 流程骨架在 _orchestrator.run_video_flow。

注意: NotebookLM Short 功能為 Pro/Ultra 限定、英文、分階段開放;生成可能 30+ 分鐘。
"""

import json
import sys

from _common import (
    channel_dir,
    fail,
    flag_value,
    load_env,
    resolve_channel,
    run_video_flow,
    setup_logging,
    today_str,
)

WAIT_TIMEOUT = 3600  # 秒,Shorts 生成等待預算(官方:可能超過 30 分鐘)

SHORTS_PROMPT = (
    "Make a punchy 60-second vertical short about this news story. "
    "Hook viewers in the first 3 seconds, explain what happened and why it matters. "
    "Keep it fast-paced and easy to follow."
)


def main() -> None:
    load_env()
    logger = setup_logging("shorts_pipeline")
    args = sys.argv[1:]
    slug = flag_value(args, "--channel", "tech")
    channel = resolve_channel(slug, logger)
    cdir = channel_dir(channel)

    top1_path, sources_path = cdir / "top1.json", cdir / "sources.json"
    for p in (top1_path, sources_path):
        if not p.exists():
            fail(
                logger,
                f"找不到 {p} — 請先執行 scripts/run_daily.py --channel {channel['slug']}",
            )
    top1 = json.loads(top1_path.read_text(encoding="utf-8"))
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    urls = [s["url"] for s in sources.get("sources", [])]
    if not urls:
        fail(logger, "sources.json 沒有可加來源")

    today = today_str()
    news = top1.get("news", top1)
    title = f"Shorts {today} - {news.get('title', '')}"[:80]
    desc = f"{SHORTS_PROMPT} Story: {news.get('title')}"
    logger.info(f"[INFO] Shorts 主題: {news.get('title')}")

    video_path = run_video_flow(
        cdir=cdir,
        today=today,
        title=title,
        desc=desc,
        fmt="short",
        filename_pattern="shorts_{date}.mp4",
        state_name="shorts_state.json",
        timeout=WAIT_TIMEOUT,
        top1_date=top1.get("date"),
        urls=urls,
        logger=logger,
    )
    size = video_path.stat().st_size
    logger.info(f"[PASS] Shorts 完成: {video_path}  ({size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
