#!/usr/bin/env python3
"""為影片加上品牌片頭/片尾(ffmpeg concat,冪等)。

用法: python scripts/brand_video.py --file <video.mp4>
輸出: <video>.branded.mp4(同名同目錄);已存在且不舊於來源則跳過。
前置: 先執行 scripts/make_branding.py 產生 output/branding/intro.mp4、outro.mp4。
"""

import os
import subprocess
import sys
from pathlib import Path

from _common import OUTPUT_DIR, fail, flag_value, load_env, setup_logging

logger = setup_logging("brand_video")

BRAND_DIR = OUTPUT_DIR / "branding"
FFMPEG = Path.home() / "bin" / "ffmpeg"
if not FFMPEG.exists():
    FFMPEG = Path("/usr/bin/ffmpeg")


def brand(file: Path, logger) -> Path:
    """拼接 intro + 影片 + outro → <檔名>.branded.mp4。回傳品牌檔路徑。

    片頭/片尾可經 .env 的 BRAND_INTRO / BRAND_OUTRO 覆寫(支援自製素材)。
    """
    intro = Path(os.environ.get("BRAND_INTRO", BRAND_DIR / "intro.mp4"))
    outro = Path(os.environ.get("BRAND_OUTRO", BRAND_DIR / "outro.mp4"))
    for p in (intro, outro):
        if not p.exists():
            fail(logger, f"缺少品牌動畫: {p} — 請先執行 scripts/make_branding.py 或設定 .env")

    out = file.with_name(file.stem + ".branded" + file.suffix)
    if out.exists() and out.stat().st_mtime >= file.stat().st_mtime:
        logger.info(f"[INFO] 品牌檔已存在且未過期,跳過: {out}")
        return out

    logger.info(f"[INFO] 拼接品牌動畫: {file}")
    cmd = [
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(intro), "-i", str(file), "-i", str(outro),
        # 保留音訊(旁白 + 片頭尾音樂)— 之前 a=0 造成靜音 bug
        "-filter_complex",
        (
            "[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=24,setpts=PTS-STARTPTS[v0];"
            "[1:v]fps=24,setpts=PTS-STARTPTS[v1];"
            "[2:v]scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=24,setpts=PTS-STARTPTS[v2];"
            "[0:a]aresample=44100[a0];[1:a]aresample=44100[a1];[2:a]aresample=44100[a2];"
            "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]"
        ),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "fast",
        "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(out),
    ]
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        fail(logger, f"ffmpeg 拼接失敗 (exit {proc.returncode})")
    if not out.exists():
        fail(logger, f"拼接後檔案不存在: {out}")
    logger.info(f"[PASS] 品牌檔: {out}")
    return out


def main() -> None:
    load_env()
    args = sys.argv[1:]
    file_arg = flag_value(args, "--file")
    if not file_arg:
        fail(logger, "需要 --file <video.mp4>")
    file = Path(file_arg)
    if not file.exists():
        fail(logger, f"影片檔不存在: {file}")
    brand(file, logger)


if __name__ == "__main__":
    main()
