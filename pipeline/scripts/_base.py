"""基礎設施: 專案根目錄、logging、fail、env 載入、原子 JSON 寫入。

依賴規則: 本模組不依賴任何其他內部模組。
內部模組不得匯入 _common facade — 只能匯入本模組或兄弟模組。
"""

import json
import logging
import os
import sys
from pathlib import Path

# 專案根目錄(本檔案在 scripts/ 下,上一層即根)
ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"


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


def save_json(payload: dict, path: Path) -> None:
    """原子寫入 JSON(temp 檔 + os.replace),避免中途被 kill 造成檔案截斷。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
