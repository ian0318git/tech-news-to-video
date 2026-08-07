"""Shared helpers for POC step scripts.

Each step script logs to logs/<step>.log (fixed name, overwritten per run)
and mirrors the same output to the console.
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Pipeline 的「今天」以雪梨當地日期為準(與 cron 06:00 AEST 排程一致)
PIPELINE_TZ = ZoneInfo("Australia/Sydney")

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"
OUTPUT_DIR = ROOT / "output"
INPUT_DIR = ROOT / "input"
SCRIPTS_DIR = ROOT / "scripts"

NOTEBOOK_ID_FILE = OUTPUT_DIR / "notebook_id.txt"
VIDEO_FILE = OUTPUT_DIR / "video.mp4"

CHANNELS_FILE = ROOT / "config" / "channels.json"


def load_channels(logger) -> list[dict]:
    """讀取 config/channels.json 的頻道定義。"""
    if not CHANNELS_FILE.exists():
        fail(logger, f"找不到 {CHANNELS_FILE}")
    import json as _json

    data = _json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
    channels = data.get("channels", data) if isinstance(data, dict) else data
    if not isinstance(channels, list) or not channels:
        fail(logger, f"{CHANNELS_FILE} 沒有頻道定義")
    return channels


def resolve_channel(slug: str | None, logger) -> dict:
    """依 --channel slug 解析頻道;沒指定就用第一個。"""
    channels = load_channels(logger)
    if not slug:
        return channels[0]
    for c in channels:
        if c.get("slug") == slug:
            return c
    fail(logger, f"未知頻道: {slug!r}", f"可用頻道: {[c['slug'] for c in channels]}")


def flag_value(args: list[str], flag: str, default: str | None = None) -> str | None:
    """回傳旗標的下一個參數值(如 --channel tech → 'tech');旗標不存在回傳 default。"""
    return next(
        (args[i + 1] for i, a in enumerate(args) if a == flag and i + 1 < len(args)), default
    )


def today_str() -> str:
    """Pipeline 的「今天」= 雪梨當地日期(2026-08-07 格式)。

    06:00 AEST 排程 = 前一日 20:00 UTC;若用 UTC 日期,檔名會落在「昨天」,
    導致 06:00 產出的影片被命名成前一天、且重跑時誤判已存在而跳過。
    """
    return datetime.now(PIPELINE_TZ).strftime("%Y-%m-%d")


def channel_dir(channel: dict) -> Path:
    """每個頻道有自己的 output/<slug>/ 目錄,避免產出互相覆蓋。"""
    d = OUTPUT_DIR / channel["slug"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _list_notebooks(logger) -> list[dict]:
    out = run_cli(["list", "--json"], logger)
    data = json.loads(out)
    return data if isinstance(data, list) else data.get("notebooks", data.get("items", []))


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
    logger.info(f"[INFO] 來源: {ok} 可用 / {len(failed)} 失敗{f'({failed})' if failed else ''}")

    # 重新列出: add 成功但伺服器端抓取失敗的來源可能出現 error 狀態
    for s in _list_sources(logger):
        if s.get("status") == "error":
            logger.warning(f"[WARN] 來源 {s.get('title')} 狀態為 error,刪除: {s.get('id')}")
            run_cli(["source", "delete", s["id"], "-y", "--json"], logger)


# The notebooklm CLI lives in the same venv as this script's interpreter.
CLI = Path(sys.executable).parent / "notebooklm"


def setup_logging(step: str) -> logging.Logger:
    """Console + logs/<step>.log dual-output logger."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(step)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(LOGS_DIR / f"{step}.log", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def fail(logger: logging.Logger, message: str, details: str = "") -> None:
    """Stop on failure: report, log, and exit non-zero. Never guess onward."""
    logger.error(f"[FAIL] {message}")
    if details:
        logger.error(details)
    sys.exit(1)


def run_cli(args, logger: logging.Logger, timeout: int | None = None) -> str:
    """Run the notebooklm CLI, log stdout/stderr, return stdout.

    On non-zero exit: report stderr verbatim and stop (per POC rule).
    """
    cmd = [str(CLI), *args]
    logger.info(f"$ {CLI.name} {' '.join(args)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.stdout:
        logger.info(proc.stdout.rstrip())
    if proc.returncode != 0:
        fail(logger, f"指令失敗 (exit {proc.returncode}): {' '.join(args)}", proc.stderr.rstrip())
    return proc.stdout


def run_live(args, logger: logging.Logger, timeout: int | None = None) -> str:
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
        fail(logger, f"指令失敗 (exit {proc.returncode}): {' '.join(args)}", err.rstrip())
    return "".join(chunks)


def read_notebook_id(logger) -> str:
    """Read the notebook id written by create_notebook.py."""
    if not NOTEBOOK_ID_FILE.exists():
        fail(logger, "找不到 notebook id。請先執行 scripts/create_notebook.py")
    return NOTEBOOK_ID_FILE.read_text().strip()


def save_json(payload: dict, path: Path) -> None:
    """原子寫入 JSON(temp 檔 + os.replace),避免中途被 kill 造成檔案截斷。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_env() -> None:
    """Minimal .env loader (no external dep). Existing env vars win."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def gemini_json(
    prompt: str, logger: logging.Logger, model: str = "gemini-2.5-flash", temperature: float = 0.2
) -> dict:
    """Call Gemini with a JSON-response prompt; return the parsed JSON object.

    Requires GEMINI_API_KEY in the environment (loaded from .env via load_env).
    """
    import httpx

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        fail(
            logger,
            "GEMINI_API_KEY 未設定",
            "請在 .env 加入一行 GEMINI_API_KEY=<key>(取得: https://aistudio.google.com/apikey)",
        )
    logger.info(f"[INFO] 呼叫 Gemini ({model}) ...")
    retries = 2  # 暫時性錯誤(429/5xx)重試,backoff 5s/10s
    for attempt in range(retries + 1):
        try:
            resp = httpx.post(
                GEMINI_API_URL.format(model=model),
                params={"key": api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": temperature,
                    },
                },
                timeout=90.0,
            )
            resp.raise_for_status()
            break
        except httpx.HTTPStatusError:
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                import time as _time

                wait = 5 * (attempt + 1)
                logger.warning(
                    f"[WARN] Gemini HTTP {resp.status_code},{wait}s 後重試 ({attempt + 1}/{retries}) ..."
                )
                _time.sleep(wait)
                continue
            fail(logger, f"Gemini API 錯誤 (HTTP {resp.status_code})", resp.text[:2000])
        except httpx.HTTPError as exc:
            fail(logger, "Gemini API 連線失敗", str(exc))

    try:
        data = resp.json()
    except json.JSONDecodeError:
        fail(logger, "Gemini 回傳 200 但 body 不是 JSON(可能是閘道錯誤頁)", resp.text[:2000])
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        fail(
            logger,
            "Gemini 回應缺少 candidates/content",
            json.dumps(data, ensure_ascii=False)[:2000],
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        fail(logger, "Gemini 回傳內容不是 JSON", text[:2000])
