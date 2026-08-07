"""notebooklm CLI 執行層: 子程序執行、notebook/來源編排。

依賴規則: 只匯入 _base(兄弟模組);不得匯入 _common facade。
"""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from _base import fail, save_json

# The notebooklm CLI lives in the same venv as this script's interpreter.
CLI = Path(sys.executable).parent / "notebooklm"


def run_cli(args, logger, timeout: int | None = None) -> str:
    """Run the notebooklm CLI, log stdout/stderr, return stdout.

    On non-zero exit: report stderr verbatim and stop (per POC rule).
    """
    cmd = [str(CLI), *args]
    logger.info(f"$ {CLI.name} {' '.join(args)}")
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )
    if proc.stdout:
        logger.info(proc.stdout.rstrip())
    if proc.returncode != 0:
        fail(
            logger,
            f"指令失敗 (exit {proc.returncode}): {' '.join(args)}",
            proc.stderr.rstrip(),
        )
    return proc.stdout


def run_live(args, logger, timeout: int | None = None) -> str:
    """Run the CLI streaming stdout live to console + log (for --wait polls).

    Returns the full captured stdout; fails loudly on non-zero exit.
    Timeout 是真正的安全網: 逾時會 kill 子程序並 fail(而非無限期卡住 cron)。
    """
    cmd = [str(CLI), *args]
    logger.info(f"$ {CLI.name} {' '.join(args)}")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
    )
    assert proc.stdout is not None and proc.stderr is not None

    chunks: list[str] = []

    def reader() -> None:
        for line in proc.stdout:
            chunks.append(line)
            logger.info(line.rstrip())

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    deadline = time.monotonic() + timeout if timeout else None
    while proc.poll() is None:
        if deadline and time.monotonic() > deadline:
            proc.kill()
            proc.wait(timeout=10)
            fail(logger, f"指令逾時({timeout}s),已終止: {' '.join(args)}")
        time.sleep(0.5)
    t.join(timeout=5)
    err = proc.stderr.read() or ""
    if proc.returncode != 0:
        fail(
            logger, f"指令失敗 (exit {proc.returncode}): {' '.join(args)}", err.rstrip()
        )
    return "".join(chunks)


def _list_notebooks(logger) -> list[dict]:
    out = run_cli(["list", "--json"], logger)
    data = json.loads(out)
    return (
        data if isinstance(data, list) else data.get("notebooks", data.get("items", []))
    )


def _list_sources(logger) -> list[dict]:
    out = run_cli(["source", "list", "--json"], logger)
    data = json.loads(out)
    return data if isinstance(data, list) else data.get("sources", [])


def ensure_notebook(cdir: Path, today: str, title: str, state_name: str, logger) -> str:
    """冪等: 重用當天已建的 notebook(state 檔記錄),否則建立新的。回傳 notebook id。"""
    state_file = cdir / state_name
    state = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning(f"[WARN] {state_name} 損壞,重新建立 notebook")

    if state.get("date") == today and state.get("notebook_id"):
        nb_id = state["notebook_id"]
        exists = any(
            nb.get("id", "") == nb_id or nb.get("id", "").startswith(nb_id)
            for nb in _list_notebooks(logger)
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


def sync_sources(urls: list[str], logger) -> None:
    """冪等: 加入缺漏來源,刪除 error 狀態來源(會卡住生成)。全部失敗才停。"""
    existing = {s.get("url", "") for s in _list_sources(logger)}
    ok, failed = 0, []
    for url in urls:
        if url in existing:
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
    logger.info(
        f"[INFO] 來源: {ok} 可用 / {len(failed)} 失敗{f'({failed})' if failed else ''}"
    )

    # 重新列出: add 成功但伺服器端抓取失敗的來源可能出現 error 狀態
    for s in _list_sources(logger):
        if s.get("status") == "error":
            logger.warning(
                f"[WARN] 來源 {s.get('title')} 狀態為 error,刪除: {s.get('id')}"
            )
            run_cli(["source", "delete", s["id"], "-y", "--json"], logger)
