#!/usr/bin/env python3
"""每日 Shorts(60 秒直式影片,冪等)。

用法: python scripts/run_shorts_pipeline.py [--channel tech]
用指定頻道(預設 tech)的今日 TOP 1 新聞製作一支 Shorts:
建/重用 notebook → 加入缺漏來源 → generate --format short(--wait,預算 60 分鐘)
→ 下載成 output/<slug>/shorts_<日期>.mp4

注意: NotebookLM Short 功能為 Pro/Ultra 限定、英文、分階段開放;生成可能 30+ 分鐘。
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

logger = setup_logging("shorts_pipeline")

WAIT_TIMEOUT = 3600  # 秒,Shorts 生成等待預算(官方:可能超過 30 分鐘)

SHORTS_PROMPT = (
    "Make a punchy 60-second vertical short about this news story. "
    "Hook viewers in the first 3 seconds, explain what happened and why it matters. "
    "Keep it fast-paced and easy to follow."
)


def main() -> None:
    load_env()
    args = sys.argv[1:]
    slug = flag_value(args, "--channel", "tech")
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
    title = f"Shorts {today} - {news.get('title', '')}"[:80]
    logger.info(f"[INFO] Shorts 主題: {news.get('title')}")

    # 0. 當天 shorts 已存在 → 整個跳過
    video_path = cdir / f"shorts_{today}.mp4"
    if video_path.exists():
        size = video_path.stat().st_size
        logger.info(
            f"[PASS] 當天 Shorts 已存在,整個流程跳過: {video_path} ({size / 1024 / 1024:.1f} MB)"
        )
        return

    # 1. Notebook + 2. 來源(皆冪等)
    ensure_notebook(cdir, today, title, "shorts_state.json", logger)
    sync_sources(urls, logger)

    # 3. 生成 Short + 4. 下載
    desc = f"{SHORTS_PROMPT} Story: {news.get('title')}"
    run_live(
        [
            "generate",
            "video",
            desc,
            "--format",
            "short",
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
    logger.info("[OK] Short 生成完成")

    run_cli(["download", "video", str(video_path), "--latest", "--no-clobber"], logger)
    if not video_path.exists():
        fail(logger, f"下載後檔案不存在: {video_path}")
    size = video_path.stat().st_size
    logger.info(f"[PASS] Shorts 完成: {video_path}  ({size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
