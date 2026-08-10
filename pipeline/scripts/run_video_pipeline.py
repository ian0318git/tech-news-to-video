#!/usr/bin/env python3
"""端到端長片(per-channel): 新聞 → NotebookLM 長片(冪等)。

用法: python scripts/run_video_pipeline.py [--channel <slug>]
薄 wrapper: 流程骨架在 _orchestrator.run_video_flow。
"""

import json
import sys

from _common import (
    SIMPLE_EN_STYLE,
    channel_dir,
    fail,
    flag_value,
    load_env,
    resolve_channel,
    run_video_flow,
    setup_logging,
    today_str,
)

WAIT_TIMEOUT = 1800  # 秒,影片生成等待預算


def main() -> None:
    load_env()
    logger = setup_logging("video_pipeline")
    args = sys.argv[1:]
    slug = flag_value(args, "--channel")
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
    prefix = channel.get("title_prefix", "Daily News")
    title = f"{today} {prefix} - {news.get('title', '')}"[:80]
    desc = (
        f"Summarize today's top {channel['keyword']} news: {news.get('title')}. "
        f"Explain the key points clearly. {SIMPLE_EN_STYLE}"
    )
    logger.info(f"[INFO] 頻道 {channel['slug']} 今天主題: {news.get('title')}")

    video_path = run_video_flow(
        cdir=cdir,
        today=today,
        title=title,
        desc=desc,
        fmt="explainer",
        filename_pattern="video_{date}.mp4",
        state_name="pipeline_state.json",
        timeout=WAIT_TIMEOUT,
        top1_date=top1.get("date"),
        urls=urls,
        logger=logger,
        style=channel.get("video_style"),
        style_prompt=channel.get("style_prompt"),
    )
    size = video_path.stat().st_size
    logger.info(f"[PASS] 端到端完成: {video_path}  ({size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
