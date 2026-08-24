#!/usr/bin/env python3
"""清理舊 NotebookLM 專案(best-effort,永遠 exit 0)。

每日由 run_daily_cron.sh 在 auth refresh 後、產生新影片前呼叫一次。
規則: state 檔日期 < 今天 且 logs/done_<日期>.marker 存在(當天全成功)
→ 該 notebook 可安全刪除。失敗的天數沒有 marker,專案保留供手動重試。

用法: python scripts/cleanup_notebooks.py [--dry-run](只列出不刪除)
"""

import json
import sys

from _common import (
    LOGS_DIR,
    OUTPUT_DIR,
    auto_delete_enabled,
    load_channels,
    load_env,
    notebook_delete,
    setup_logging,
    today_str,
)

STATE_NAMES = ("pipeline_state.json", "shorts_state.json")


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
    if date >= today:
        logger.info(f"[INFO] {date} 是今天/未來,由 pipeline 內部 hook 處理: {nb_id}")
        return None
    if not (logs_dir / f"done_{date}.marker").exists():
        logger.info(f"[INFO] {date} 無 done marker(當天有失敗),保留 notebook: {nb_id}")
        return None
    logger.info(
        f"[INFO] {date} 已完成 → {'[DRY-RUN] 將刪除' if dry_run else '刪除'} notebook: {nb_id} ({title})"
    )
    if not dry_run:
        try:
            notebook_delete(nb_id, logger)
            logger.info(f"[OK] 已刪除 notebook: {nb_id}")
        except SystemExit:
            logger.warning(f"[WARN] notebook 刪除失敗(保留): {nb_id}")
    return nb_id


def main() -> None:
    load_env()
    logger = setup_logging("cleanup_notebooks")
    dry_run = "--dry-run" in sys.argv[1:]
    if not auto_delete_enabled():
        logger.info("[INFO] AUTO_DELETE_NOTEBOOKS 未啟用,跳過清理")
        return
    today = today_str()
    for channel in load_channels(logger):
        cdir = OUTPUT_DIR / channel["slug"]
        for name in STATE_NAMES:
            sweep_state_file(cdir / name, today, LOGS_DIR, dry_run, logger)
    # 永不 fail — 清理是 best-effort,失敗不得影響主流程


if __name__ == "__main__":
    main()
