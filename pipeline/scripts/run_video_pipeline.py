#!/usr/bin/env python3
"""End-to-end(per-channel): 把 daily pipeline 產出的 sources.json 變成一支影片(冪等)。

用法: python scripts/run_video_pipeline.py [--channel <slug>]

冪等設計(可安全重跑,從上次失敗點繼續):
- Notebook: 用 output/<slug>/pipeline_state.json 記住當天建過的 notebook,
  重跑時直接重複使用,不重複建立
- 來源: 只加入尚未存在的 URL;error 狀態來源自動刪除
- 影片: 當天影片檔已存在 → 跳過生成與下載

流程: 建/重用 notebook → 加入缺漏來源 → 生成 Video Overview(explainer, --wait)
→ 下載成 output/<slug>/video_<日期>.mp4
"""

import json
import sys
from datetime import datetime, timezone

from _common import (
    channel_dir,
    fail,
    flag_value,
    load_env,
    resolve_channel,
    run_cli,
    run_live,
    save_json,
    setup_logging,
)

logger = setup_logging("video_pipeline")

WAIT_TIMEOUT = 1800  # 秒,影片生成等待預算


def list_notebooks() -> list[dict]:
    """`notebooklm list --json` → notebook 清單(支援多種回傳形狀)。"""
    out = run_cli(["list", "--json"], logger)
    data = json.loads(out)
    if isinstance(data, list):
        return data
    return data.get("notebooks", data.get("items", []))


def list_sources() -> list[dict]:
    out = run_cli(["source", "list", "--json"], logger)
    data = json.loads(out)
    if isinstance(data, list):
        return data
    return data.get("sources", [])


def resolve_notebook(today: str, title: str, cdir) -> str:
    """重複利用當天已建的 notebook;否則建立新的。回傳 notebook id。"""
    state_file = cdir / "pipeline_state.json"
    state = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("[WARN] pipeline_state.json 損壞,重新建立 notebook")

    if state.get("date") == today and state.get("notebook_id"):
        nb_id = state["notebook_id"]
        exists = any(
            nb.get("id", "") == nb_id or nb.get("id", "").startswith(nb_id)
            for nb in list_notebooks()
        )
        if exists:
            logger.info(f"[INFO] 重用當天 notebook: {nb_id} ({title})")
            run_cli(["use", nb_id], logger)
            return nb_id
        logger.warning(f"[WARN] state 中的 notebook {nb_id} 已不存在,重新建立")

    out = run_cli(["create", title, "--use", "--json"], logger)
    result = json.loads(out)
    nb_id = (result.get("notebook") or {}).get("id")
    if not nb_id:
        fail(logger, "無法取得 notebook id", out)
    save_json({"date": today, "notebook_id": nb_id, "title": title}, state_file)
    logger.info(f"[OK] Notebook 已建立: {nb_id}  {title}")
    return nb_id


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

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    news = top1.get("news", top1)
    prefix = channel.get("title_prefix", "Daily News")
    title = f"{today} {prefix} - {news.get('title', '')}"[:80]
    logger.info(f"[INFO] 頻道 {channel['slug']} 今天主題: {news.get('title')}")

    # 0. 影片已存在 → 全部跳過(最優先,避免重跑時亂建 notebook/加來源)
    video_path = cdir / f"video_{today}.mp4"
    if video_path.exists():
        size = video_path.stat().st_size
        logger.info(f"[PASS] 當天影片已存在,整個流程跳過: {video_path} ({size / 1024 / 1024:.1f} MB)")
        return

    # 1. Notebook(冪等: 重用或建立)
    nb_id = resolve_notebook(today, title, cdir)

    # 2. 來源(冪等: 只加缺漏的;error 來源刪除)
    existing_urls = {s.get("url", "") for s in list_sources()}
    ok, failed = 0, []
    for url in urls:
        if url in existing_urls:
            logger.info(f"[INFO] 來源已存在,跳過: {url}")
            ok += 1
            continue
        try:
            run_cli(["source", "add", url, "--timeout", "90", "--json"], logger)
            ok += 1
        except SystemExit:
            failed.append(url)
            logger.warning(f"[WARN] 來源加入失敗(記錄,繼續): {url}")
    if ok == 0:
        fail(logger, "沒有可用來源(全部已存在但無效,或全部加入失敗)", "\n".join(failed))
    logger.info(f"[INFO] 來源: {ok} 可用 / {len(failed)} 失敗{f'({failed})' if failed else ''}")

    # 2b. error 狀態來源會卡住生成 — 刪除
    for s in list_sources():
        if s.get("status") == "error":
            logger.warning(f"[WARN] 來源 {s.get('title')} 狀態為 error,刪除: {s.get('id')}")
            run_cli(["source", "delete", s["id"], "-y", "--json"], logger)

    # 3. 生成 + 下載(前面的檢查已保證影片不存在)
    desc = f"Summarize today's top {channel['keyword']} news: {news.get('title')}. Explain the key points clearly."
    run_live(
        ["generate", "video", desc, "--format", "explainer", "--wait",
         "--timeout", str(WAIT_TIMEOUT), "--interval", "2", "--json"],
        logger,
        timeout=WAIT_TIMEOUT + 120,
    )
    logger.info("[OK] Video Overview 生成完成")

    # 4. 下載
    run_cli(["download", "video", str(video_path), "--latest", "--no-clobber"], logger)
    if not video_path.exists():
        fail(logger, f"下載後檔案不存在: {video_path}")
    size = video_path.stat().st_size
    logger.info(f"[PASS] 端到端完成: {video_path}  ({size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
