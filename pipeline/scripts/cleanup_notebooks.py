#!/usr/bin/env python3
"""清理舊 NotebookLM 專案(best-effort,永遠 exit 0)。

每日由 run_daily_cron.sh 在 auth refresh 後、產生新影片前呼叫一次。
兩道掃描,同一安全閘門:
1. state 檔掃描: output/<channel>/*_state.json 的 notebook_id
2. 清單掃描: notebooklm list 中標題為 pipeline 格式(前綴日期)的專案 —
   涵蓋 state 檔被覆寫而失聯的歷史專案(本功能上線前累積的)
共同規則: 日期 < 今天 且 logs/done_<日期>.marker 存在(當天全成功)才刪;
失敗的天數沒有 marker,專案保留供手動重試。非 pipeline 標題(個人專案)一律不碰。

用法: python scripts/cleanup_notebooks.py [--dry-run](只列出不刪除)
"""

import json
import re
import sys

from _common import (
    LOGS_DIR,
    OUTPUT_DIR,
    auto_delete_enabled,
    load_channels,
    load_env,
    notebook_delete,
    notebook_list,
    setup_logging,
    today_str,
)

STATE_NAMES = ("pipeline_state.json", "shorts_state.json")

# pipeline 標題格式: "2026-08-24 ..." 或 "Shorts 2026-08-24 ..."(日期永遠在開頭)
TITLE_DATE_PATTERN = re.compile(r"^(?:Shorts )?(\d{4}-\d{2}-\d{2}) ")


def _delete_if_safe(nb_id, title, date, today, logs_dir, dry_run, logger) -> bool:
    """共同安全閘門: 日期 < 今天 且 done marker 存在才刪;失敗只警告。

    回傳 True = 已刪除(dry-run 視為會刪),False = 保留。
    """
    if date >= today:
        logger.info(f"[INFO] {date} 是今天/未來,保留 notebook: {nb_id}")
        return False
    if not (logs_dir / f"done_{date}.marker").exists():
        logger.info(f"[INFO] {date} 無 done marker(當天有失敗),保留 notebook: {nb_id}")
        return False
    logger.info(
        f"[INFO] {date} 已完成 → {'[DRY-RUN] 將刪除' if dry_run else '刪除'} notebook: "
        f"{nb_id} ({title[:50]})"
    )
    if not dry_run:
        try:
            notebook_delete(nb_id, logger)
            logger.info(f"[OK] 已刪除 notebook: {nb_id}")
        except SystemExit:
            logger.warning(f"[WARN] notebook 刪除失敗(保留): {nb_id}")
    return True


def sweep_state_file(state_file, today, logs_dir, dry_run, logger) -> str | None:
    """掃描單一 state 檔;回傳(將)刪除的 notebook id,未處理回傳 None。"""
    if not state_file.exists():
        return None
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning(f"[WARN] {state_file.name} 損壞,跳過")
        return None
    date, nb_id, title = state.get("date"), state.get("notebook_id"), state.get("title", "")
    if not date or not nb_id:
        logger.warning(f"[WARN] {state_file} 缺少 date/notebook_id,跳過")
        return None
    if _delete_if_safe(nb_id, title, date, today, logs_dir, dry_run, logger):
        return nb_id
    return None


def sweep_listed_notebooks(items, today, logs_dir, dry_run, logger, seen_ids=None) -> int:
    """清單掃描: 標題為 pipeline 格式(前綴日期)且通過閘門 → 刪除。回傳處理數。"""
    seen = set(seen_ids or ())
    handled = 0
    for n in items:
        nb_id = n.get("id")
        if not nb_id or nb_id in seen:
            continue
        m = TITLE_DATE_PATTERN.match(n.get("title", ""))
        if not m:
            continue  # 非 pipeline 標題(個人專案)— 一律不碰
        date = m.group(1)
        seen.add(nb_id)
        handled += 1
        _delete_if_safe(nb_id, n.get("title", ""), date, today, logs_dir, dry_run, logger)
    return handled


def main() -> None:
    load_env()
    logger = setup_logging("cleanup_notebooks")
    dry_run = "--dry-run" in sys.argv[1:]
    if not auto_delete_enabled():
        logger.info("[INFO] AUTO_DELETE_NOTEBOOKS 未啟用,跳過清理")
        return
    today = today_str()
    seen = set()
    for channel in load_channels(logger):
        cdir = OUTPUT_DIR / channel["slug"]
        for name in STATE_NAMES:
            seen.add(sweep_state_file(cdir / name, today, LOGS_DIR, dry_run, logger))
    seen.discard(None)
    # 清單掃描: 涵蓋 state 檔失聯的歷史 pipeline 專案
    try:
        items = notebook_list(logger)
    except SystemExit:
        logger.warning("[WARN] notebook list 失敗,跳過清單掃描")
        items = []
    sweep_listed_notebooks(items, today, LOGS_DIR, dry_run, logger, seen_ids=seen)
    # 永不 fail — 清理是 best-effort,失敗不得影響主流程


if __name__ == "__main__":
    main()
