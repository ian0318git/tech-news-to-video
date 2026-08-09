"""共用 facade — 統一再匯出內部模組,步驟腳本唯一的匯入點。

依賴規則:
- 內部模組(_base / _config / _cli / _gemini)不得匯入本檔案,只能匯入兄弟模組
- 步驟腳本只能從本檔案匯入(未來刪除 facade 時,一次過改所有匯入點)
- 低階 run_cli/run_live 不經 facade 外洩 — 一律使用 typed wrappers
"""

from _base import LOGS_DIR, ROOT, fail, load_env, save_json, setup_logging
from _cli import (
    auth_check,
    download_video,
    ensure_notebook,
    generate_video,
    login_refresh,
    notebook_create,
    notebook_list,
    notebook_use,
    source_add,
    source_delete,
    source_list,
    sync_sources,
)
from _config import (
    CHANNELS_FILE,
    INPUT_DIR,
    NOTEBOOK_ID_FILE,
    OUTPUT_DIR,
    PIPELINE_TZ,
    SCRIPTS_DIR,
    VIDEO_FILE,
    channel_dir,
    flag_value,
    load_channels,
    resolve_channel,
    today_str,
)
from _gemini import gemini_json
from _orchestrator import run_video_flow

__all__ = [
    "CHANNELS_FILE",
    "INPUT_DIR",
    "LOGS_DIR",
    "NOTEBOOK_ID_FILE",
    "OUTPUT_DIR",
    "PIPELINE_TZ",
    "ROOT",
    "SCRIPTS_DIR",
    "VIDEO_FILE",
    "auth_check",
    "channel_dir",
    "download_video",
    "ensure_notebook",
    "fail",
    "flag_value",
    "gemini_json",
    "generate_video",
    "load_channels",
    "load_env",
    "login_refresh",
    "notebook_create",
    "notebook_list",
    "notebook_use",
    "resolve_channel",
    "run_video_flow",
    "save_json",
    "setup_logging",
    "source_add",
    "source_delete",
    "source_list",
    "sync_sources",
    "today_str",
]
