#!/usr/bin/env python3
"""End-to-end(per-channel): 把 daily pipeline 產出的 sources.json 變成一支長片影片(冪等)。

用法: python scripts/run_video_pipeline.py [--channel <slug>]

冪等: notebook 重用(pipeline_state.json)、來源去重、當天影片已存在即整個跳過。
流程: 建/重用 notebook → 加入缺漏來源 → 生成 Video Overview(explainer, --wait)
→ 下載成 output/<slug>/video_<日期>.mp4
"""

import json
import sys

from _common import (
    channel_dir,
    ensure_notebook,
    fail,
    flag_value,
    load_env,
    resolve_channel,
    run_cli,
    run_live,
    setup_logging,
    sync_sources,
    today_str,
)

logger = setup_logging("video_pipeline")

WAIT_TIMEOUT = 1800  # 秒,影片生成等待預算


def main() -> None:
    load_env()
    args = sys.argv[1:]
    slug = flag_value(args, "--channel")
    channel = resolve_channel(slug, logger)
    cdir = channel_dir(channel)

    top1_path, sources_path = cdir / "top1.json", cdir / "sources.json"
    for p in (top1_path, sources_path):
        if not p.exists():
            fail(logger, f"找不到 {p} — 請先執行 scripts/run_daily.py --channel {channel['slug']}")

    top1 = json.loads(top1_path.read_text(encoding="utf-8"))
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    urls = [s["url"] for s in sources.get("sources", [])]
    if not urls:
        fail(logger, "sources.json 沒有可加來源")

    today = today_str()
    if top1.get("date") and top1["date"] != today:
        fail(
            logger,
            f"top1.json 的新聞日期是 {top1['date']},不是今天({today})",
            "請先執行 scripts/run_daily.py --channel 更新選題",
        )
    news = top1.get("news", top1)
    prefix = channel.get("title_prefix", "Daily News")
    title = f"{today} {prefix} - {news.get('title', '')}"[:80]
    logger.info(f"[INFO] 頻道 {channel['slug']} 今天主題: {news.get('title')}")

    # 0. 影片已存在 → 全部跳過(最優先,避免重跑時亂建 notebook/加來源)
    video_path = cdir / f"video_{today}.mp4"
    if video_path.exists():
        size = video_path.stat().st_size
        logger.info(
            f"[PASS] 當天影片已存在,整個流程跳過: {video_path} ({size / 1024 / 1024:.1f} MB)"
        )
        return

    # 1. Notebook + 2. 來源(皆冪等)
    ensure_notebook(cdir, today, title, "pipeline_state.json", logger)
    sync_sources(urls, logger)

    # 3. 生成 + 下載
    desc = f"Summarize today's top {channel['keyword']} news: {news.get('title')}. Explain the key points clearly."
    run_live(
        [
            "generate",
            "video",
            desc,
            "--format",
            "explainer",
            "--wait",
            "--timeout",
            str(WAIT_TIMEOUT),
            "--interval",
            "2",
            "--json",
        ],
        logger,
        timeout=WAIT_TIMEOUT + 120,
    )
    logger.info("[OK] Video Overview 生成完成")

    run_cli(["download", "video", str(video_path), "--latest", "--no-clobber"], logger)
    if not video_path.exists():
        fail(logger, f"下載後檔案不存在: {video_path}")
    size = video_path.stat().st_size
    logger.info(f"[PASS] 端到端完成: {video_path}  ({size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
