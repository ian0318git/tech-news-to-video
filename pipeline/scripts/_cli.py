"""notebooklm CLI 契約層: typed wrappers + 低階執行器。

依賴規則: 只匯入 _base(兄弟模組);不得匯入 _common facade。

契約: 每個命令一個 wrapper,回傳標準化 dict(形狀由 tests/test_cli_contract.py 鎖定)。
run_cli/run_live 是內部實作,步驟腳本不得直接使用。
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


# ---------- 低階執行器(內部) ----------


# run_cli 的安全網: 沒人傳 timeout 時也預設 1800s — CLI 卡死不會無限卡住 cron。
# 長任務(生成)走 run_live 並帶 timeout,不受此影響。
DEFAULT_CLI_TIMEOUT = 1800


def run_cli(args, logger, timeout: int | None = DEFAULT_CLI_TIMEOUT) -> str:
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


def _last_json_object(out: str, logger, command: str) -> dict | None:
    """從多行輸出中找最後一個可解析的 JSON 物件(CLI pretty-print 的終態)。"""
    final = None
    for line in out.splitlines():
        try:
            final = json.loads(line)
        except json.JSONDecodeError:
            continue
    if final is None:
        logger.warning(f"[WARN] {command} 輸出中沒有可解析的 JSON,以 exit 0 為準")
    return final


# ---------- typed wrappers(契約) ----------


def notebook_list(logger) -> list[dict]:
    """`list --json` → 標準化 notebook 清單 [{id, title, ...}]。"""
    out = run_cli(["list", "--json"], logger)
    data = json.loads(out)
    items = data if isinstance(data, list) else data.get("notebooks")
    if not isinstance(items, list):
        fail(logger, "notebook list 回應形狀不符(預期 notebooks 陣列)", out[:500])
    return items


def notebook_create(title: str, logger) -> str:
    """`create <title> --use --json` → notebook id。"""
    out = run_cli(["create", title, "--use", "--json"], logger)
    result = json.loads(out)
    nb_id = (result.get("notebook") or {}).get("id")
    if not nb_id:
        fail(logger, "create 回應缺少 notebook.id", out[:500])
    return nb_id


def notebook_use(nb_id: str, logger) -> None:
    """`use <id>` — 設為 active context。"""
    run_cli(["use", nb_id], logger)


def login_refresh(logger) -> None:
    """`login --master-token-refresh` — 從 master token 重新 mint cookie。"""
    run_cli(["login", "--master-token-refresh"], logger)


def auth_check(logger) -> dict:
    """`auth check --test --json` → 標準化 {status, ...}。"""
    out = run_cli(["auth", "check", "--test", "--json"], logger)
    result = json.loads(out)
    if "status" not in result:
        fail(logger, "auth check 回應缺少 status 欄位", out[:500])
    return result


def source_list(logger) -> list[dict]:
    """`source list --json` → 標準化來源清單 [{id, title, url, status}]。"""
    out = run_cli(["source", "list", "--json"], logger)
    data = json.loads(out)
    items = data if isinstance(data, list) else data.get("sources")
    if not isinstance(items, list):
        fail(logger, "source list 回應形狀不符(預期 sources 陣列)", out[:500])
    return items


def source_add(url: str, logger) -> dict:
    """`source add <url> --json` → 標準化來源 {id, title, type, url}。"""
    out = run_cli(["source", "add", url, "--timeout", "90", "--json"], logger)
    result = json.loads(out)
    src = result.get("source", result)
    if not src.get("id"):
        fail(logger, "source add 回應缺少 id", out[:500])
    return src


def source_delete(src_id: str, logger) -> None:
    """`source delete <id> -y` — 刪除來源(含 error 狀態來源)。"""
    run_cli(["source", "delete", src_id, "-y", "--json"], logger)


def notebook_delete(nb_id: str, logger) -> None:
    """`delete -n <id> -y --json` — 刪除 notebook(冪等: 已刪除的也成功)。

    注意與 source_delete 不同: delete 是頂層指令,id 走 -n option。
    失敗時 run_cli 會 raise SystemExit — 呼叫端依 sync_sources 模式自行 catch。
    """
    run_cli(["delete", "-n", nb_id, "-y", "--json"], logger)


def generate_video(
    desc: str,
    fmt: str,
    logger,
    timeout: int = 1800,
    interval: int = 2,
    style: str | None = None,
    style_prompt: str | None = None,
) -> dict:
    """`generate video <desc> --format <fmt> --wait --json` → 終態 {task_id, status, ...}。

    阻塞等待生成完成(串流進度);逾時(含餘裕)會 kill 子程序並 fail。
    style/--style-prompt 僅對標準格式(explainer/brief)有效;cinematic/short 不支援。
    """
    args = [
        "generate",
        "video",
        desc,
        "--format",
        fmt,
        "--wait",
        "--timeout",
        str(timeout),
        "--interval",
        str(interval),
        "--json",
    ]
    if style:
        args += ["--style", style]
        if style_prompt:
            args += ["--style-prompt", style_prompt]
    out = run_live(args, logger, timeout=timeout + 120)
    final = _last_json_object(out, logger, "generate video")
    return final or {"status": "completed"}


def download_video(path: Path, logger) -> None:
    """`download video <path> --latest --no-clobber` — 下載最新影片。"""
    run_cli(["download", "video", str(path), "--latest", "--no-clobber"], logger)


# ---------- 編排(建立在 wrappers 之上) ----------


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
            for nb in notebook_list(logger)
        )
        if exists:
            logger.info(f"[INFO] 重用當天 notebook: {nb_id} ({title})")
            notebook_use(nb_id, logger)
            return nb_id
        logger.warning(f"[WARN] state 中的 notebook {nb_id} 已不存在,重新建立")

    nb_id = notebook_create(title, logger)
    save_json({"date": today, "notebook_id": nb_id, "title": title}, state_file)
    logger.info(f"[OK] Notebook 已建立: {nb_id}  {title}")
    return nb_id


def sync_sources(urls: list[str], logger) -> None:
    """冪等: 加入缺漏來源,刪除 error 狀態來源(會卡住生成)。全部失敗才停。"""
    existing = {s.get("url", "") for s in source_list(logger)}
    ok, failed = 0, []
    for url in urls:
        if url in existing:
            logger.info(f"[INFO] 來源已存在,跳過: {url}")
            ok += 1
            continue
        try:
            source_add(url, logger)
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
    listed = source_list(logger)
    errors = [s for s in listed if s.get("status") == "error"]
    remaining = [s for s in listed if s.get("status") != "error"]
    if errors:
        if not remaining:
            # 全部 error → 刪光會在空 notebook 上生成 — 提早停,避免晚期模糊失敗
            fail(
                logger,
                "所有來源皆為 error 狀態,停止(避免在空 notebook 上生成)",
                "\n".join(f"{s.get('title')} ({s.get('id')})" for s in errors),
            )
        for s in errors:
            logger.warning(
                f"[WARN] 來源 {s.get('title')} 狀態為 error,刪除: {s.get('id')}"
            )
            source_delete(s["id"], logger)
