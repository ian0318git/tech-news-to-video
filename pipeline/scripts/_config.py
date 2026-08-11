"""設定與路徑: 目錄常數、頻道定義、旗標解析、pipeline 日期。

依賴規則: 只匯入 _base(兄弟模組);不得匯入 _common facade。
"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from _base import ROOT, fail

OUTPUT_DIR = ROOT / "output"
INPUT_DIR = ROOT / "input"
SCRIPTS_DIR = ROOT / "scripts"

NOTEBOOK_ID_FILE = OUTPUT_DIR / "notebook_id.txt"
VIDEO_FILE = OUTPUT_DIR / "video.mp4"

CHANNELS_FILE = ROOT / "config" / "channels.json"

# Pipeline 的「今天」以雪梨當地日期為準(與 cron 06:00 AEST 排程一致)
PIPELINE_TZ = ZoneInfo("Australia/Sydney")

# 影片旁白風格: 基礎英文(A2)+ 輕量澳洲口語 — 觀眾以英語學習者為主
SIMPLE_EN_STYLE = (
    "Narrate in very simple, basic English (around CEFR A2 level). "
    "Use only common everyday words. Keep sentences very short. "
    "Explain every technical term in plain, simple words — imagine the "
    "viewer is still learning English. "
    "Use a light, friendly Australian tone — relaxed and approachable, "
    "as if explaining today's tech news to a mate. "
    "A few everyday Australian expressions are welcome, but never at the "
    "cost of clarity."
)


def load_channels(logger) -> list[dict]:
    """讀取 config/channels.json 的頻道定義。"""
    if not CHANNELS_FILE.exists():
        fail(logger, f"找不到 {CHANNELS_FILE}")
    data = json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
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
        (args[i + 1] for i, a in enumerate(args) if a == flag and i + 1 < len(args)),
        default,
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
