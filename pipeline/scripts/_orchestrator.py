"""共用編排器: 長片與 Shorts 兩支 pipeline 的流程骨架(可注入測試)。

依賴規則: 只匯入兄弟模組;不得匯入 _common facade。
`cli` 參數 = 測試接縫: 預設真 _cli,測試注入 FakeCli 即可驗證
流程順序、跳過邏輯與新鮮度閘門,不需要真實 NotebookLM。
"""

from pathlib import Path

import _cli as _cli_module
from _base import fail


def run_video_flow(
    *,
    cdir: Path,
    today: str,
    title: str,
    desc: str,
    fmt: str,
    filename_pattern: str,
    state_name: str,
    timeout: int,
    top1_date: str | None,
    urls: list[str],
    logger,
    cli=None,
    style: str | None = None,
    style_prompt: str | None = None,
) -> Path:
    """冪等影片流程: 新鮮度閘門 → 存在跳過 → notebook → 來源 → 生成 → 下載。

    回傳最終影片路徑。cli 可注入(測試用 FakeCli)。
    """
    cli = cli or _cli_module

    # 1. 新鮮度閘門 — 拒絕用昨天的新聞
    if top1_date and top1_date != today:
        fail(
            logger,
            f"top1.json 的新聞日期是 {top1_date},不是今天({today})",
            "請先執行 scripts/run_daily.py --channel 更新選題",
        )

    # 2. 當天影片已存在 → 整個跳過(冪等)
    video_path = cdir / filename_pattern.format(date=today)
    if video_path.exists():
        size = video_path.stat().st_size
        logger.info(
            f"[PASS] 當天影片已存在,整個流程跳過: {video_path} ({size / 1024 / 1024:.1f} MB)"
        )
        return video_path

    # 3. notebook + 來源(皆冪等)
    cli.ensure_notebook(cdir, today, title, state_name, logger)
    cli.sync_sources(urls, logger)

    # 4. 生成 + 下載
    cli.generate_video(
        desc, fmt, logger, timeout=timeout, style=style, style_prompt=style_prompt
    )
    logger.info(f"[OK] {fmt} 生成完成")
    cli.download_video(video_path, logger)

    if not video_path.exists():
        fail(logger, f"下載後檔案不存在: {video_path}")
    return video_path
