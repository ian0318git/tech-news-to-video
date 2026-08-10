#!/usr/bin/env python3
"""風格預覽: 用今天排名第 2 的新聞 + 各頻道設定的風格,生成預覽影片。

用法:
  python scripts/preview_styles.py                  # embedded + tech 長片 + tech Shorts
  python scripts/preview_styles.py --channel tech   # 只做 tech 長片

輸出: output/preview/<slug>_<日期>.mp4、output/preview/shorts_<日期>.mp4
不碰每日 pipeline 的 state/記錄 — 純預覽;主題取自 ranking.json 第 2 名(與每日 TOP 1 不同)。
"""

import fcntl
import json
import sys

from _browser import resolve_article_url
from _cli import download_video
from _common import (
    LOGS_DIR,
    OUTPUT_DIR,
    SIMPLE_EN_STYLE,
    channel_dir,
    fail,
    flag_value,
    generate_video,
    load_channels,
    load_env,
    notebook_create,
    resolve_channel,
    setup_logging,
    source_add,
    today_str,
)

PREVIEW_DIR = OUTPUT_DIR / "preview"

SHORTS_PROMPT = (
    "Make a punchy 60-second vertical short about this news story. "
    "Open with a bold, scroll-stopping hook in the first 2-3 seconds — a provocative "
    "question or surprising fact. Keep the pacing fast with quick scene changes and "
    "on-screen keyword highlights. End with a takeaway that makes viewers want to "
    "follow the channel. High energy, confident and fun tone."
)


def iter_ranked_news(cdir, logger):
    """依排名依序 yield 新聞(跳過 TOP 1)— 來源失敗時可試下一則。"""
    ranking = json.loads((cdir / "ranking.json").read_text(encoding="utf-8"))
    raw = json.loads((cdir / "news_raw.json").read_text(encoding="utf-8"))
    for entry in (ranking.get("ranking") or [])[1:]:
        yield raw["items"][int(entry["index"])]


def _try_source(news, logger) -> str | None:
    """解析真實網址並加入來源;失敗回傳 None(呼叫端試下一則)。"""
    real = resolve_article_url(news["url"], logger)
    if not real:
        logger.warning(f"[WARN] 無法解析真實網址,試下一則: {news['title'][:50]}")
        return None
    try:
        source_add(real, logger)
        return real
    except SystemExit:
        logger.warning(f"[WARN] 來源加入失敗(可能付費牆),試下一則: {news['title'][:50]}")
        return None


def preview_long(channel, logger) -> None:
    """一支頻道的長片預覽(排名 #2 起 + 頻道風格;已產出則跳過)。"""
    cdir = channel_dir(channel)
    today = today_str()
    out = PREVIEW_DIR / f"{channel['slug']}_{today}.mp4"
    if out.exists():
        logger.info(f"[INFO] 預覽已存在,跳過: {out}")
        return

    for news in iter_ranked_news(cdir, logger):
        logger.info(f"[INFO] {channel['slug']} 預覽主題: {news['title'][:70]}")
        real = _try_source(news, logger)
        if not real:
            continue
        title = f"PREVIEW {today} {channel.get('title_prefix', '')} - {news['title']}"[:80]
        notebook_create(title, logger)
        desc = f"Summarize this news story: {news['title']}. Explain the key points clearly. {SIMPLE_EN_STYLE}"
        generate_video(
            desc,
            "explainer",
            logger,
            timeout=3600,
            style=channel.get("video_style"),
            style_prompt=channel.get("style_prompt"),
        )
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        download_video(out, logger)
        logger.info(f"[PASS] 預覽完成: {out}")
        return
    fail(logger, f"{channel['slug']} 排名 #2 起的新聞都無法加入來源")


def preview_shorts(channel, logger) -> None:
    """tech 頻道的 Shorts 預覽(排名 #2 起 + 新版抓眼球 prompt;已產出則跳過)。"""
    cdir = channel_dir(channel)
    today = today_str()
    out = PREVIEW_DIR / f"shorts_{today}.mp4"
    if out.exists():
        logger.info(f"[INFO] Shorts 預覽已存在,跳過: {out}")
        return

    for news in iter_ranked_news(cdir, logger):
        logger.info(f"[INFO] Shorts 預覽主題: {news['title'][:70]}")
        real = _try_source(news, logger)
        if not real:
            continue
        title = f"PREVIEW SHORTS {today} - {news['title']}"[:80]
        notebook_create(title, logger)
        generate_video(f"{SHORTS_PROMPT} Story: {news['title']}", "short", logger, timeout=3600)
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        download_video(out, logger)
        logger.info(f"[PASS] Shorts 預覽完成: {out}")
        return
    fail(logger, "Shorts 預覽:排名 #2 起的新聞都無法加入來源")


def acquire_pipeline_lock(logger):
    """非阻塞取得每日 pipeline 鎖 — 避免與 cron 並發生成觸發 NotebookLM rate limit。

    程序結束時 fcntl 鎖自動釋放;持有期間 15 分鐘的 catch-up 會自動跳過。
    """
    lock_file = LOGS_DIR / "pipeline.lock"
    fh = open(lock_file, "w")  # noqa: SIM115 — fh 需存活到程序結束以持有鎖
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fail(
            logger,
            "每日 pipeline 正在執行中 — 預覽請等它完成後再跑",
            "或直接執行: python scripts/preview_styles.py",
        )
    return fh


def main() -> None:
    load_env()
    logger = setup_logging("preview_styles")
    acquire_pipeline_lock(logger)  # 持有到程序結束
    args = sys.argv[1:]
    slug = flag_value(args, "--channel")
    channels = [resolve_channel(slug, logger)] if slug else load_channels(logger)

    for ch in channels:
        preview_long(ch, logger)
    if not slug:  # 預設也做 tech 的 Shorts 預覽
        preview_shorts(resolve_channel("tech", logger), logger)
    logger.info("[PASS] 風格預覽全部完成")


if __name__ == "__main__":
    main()
