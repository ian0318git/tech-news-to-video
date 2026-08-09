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

from _common import OUTPUT_DIR, ROOT, fail, flag_value, load_env, setup_logging

logger = setup_logging("brand_video")

BRAND_DIR = OUTPUT_DIR / "branding"
FFMPEG = Path.home() / "bin" / "ffmpeg"
if not FFMPEG.exists():
    FFMPEG = Path("/usr/bin/ffmpeg")


def _duration(file: Path) -> float | None:
    proc = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-i", str(file), "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    m = __import__("re").search(r"Duration: (\d+):(\d+):(\d+\.\d+)", proc.stderr)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def detect_end_card(file: Path, logger) -> float | None:
    """偵測 NotebookLM 結束卡起點 — 音訊為主、靜止畫面為輔。

    結束卡 = 旁白結束後的一段靜音+品牌畫面(可能有動畫,freezedetect 抓不全):
    1. 音訊: 找「延伸到片尾」的最後一段靜音的起點(旁白結束點)
    2. 視訊: freezedetect 最後一段完全靜止的起點
    取兩者較早者;安全條件: 位於最後 15% 且尾段 ≥1s。
    """
    duration = _duration(file)
    if not duration:
        return None

    # 1. 音訊: 尾部靜音(延伸到片尾)的起點
    sil_proc = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-i", str(file),
         "-af", "silencedetect=noise=-35dB:d=0.8,ametadata=print:key=lavfi.silence_start",
         "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    starts = [float(m) for m in __import__("re").findall(r"silence_start: ([0-9.]+)", sil_proc.stderr)]
    audio_trim = max((s for s in starts if duration - s >= 1.0), default=None)

    # 2. 視訊: 最後一段完全靜止的起點
    frz_proc = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-i", str(file),
         "-vf", "freezedetect=n=-50dB:d=1.5,metadata=print:key=lavfi.freezedetect.freeze_start",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    freezes = [float(m) for m in __import__("re").findall(r"freeze_start=([0-9.]+)", frz_proc.stderr)]
    video_trim = max((f for f in freezes if duration - f >= 1.0), default=None)

    candidates = [t for t in (audio_trim, video_trim) if t is not None and t >= duration * 0.85]
    if not candidates:
        logger.info(f"[INFO] 未偵測到尾部結束卡(影片 {duration:.1f}s)")
        return None
    trim = min(candidates)  # 取較早者: 完整移除含動畫的結束卡
    logger.info(
        f"[INFO] 結束卡偵測: 音訊尾靜音 {audio_trim}, 靜止尾段 {video_trim} → 裁切點 {trim:.1f}s"
    )
    return trim


def brand(file: Path, logger) -> Path:
    """拼接 intro + 影片 + outro → <檔名>.branded.mp4。回傳品牌檔路徑。

    片頭/片尾可經 .env 的 BRAND_INTRO / BRAND_OUTRO 覆寫(支援自製素材)。
    """
    def resolve(p: str | Path) -> Path:
        p = Path(p)
        return p if p.is_absolute() else ROOT / p  # .env 相對路徑對齊專案根目錄

    intro = resolve(os.environ.get("BRAND_INTRO", BRAND_DIR / "intro.mp4"))
    outro = resolve(os.environ.get("BRAND_OUTRO", BRAND_DIR / "outro.mp4"))
    for p in (intro, outro):
        if not p.exists():
            fail(logger, f"缺少品牌動畫: {p} — 請先執行 scripts/make_branding.py 或設定 .env")

    out = file.with_name(file.stem + ".branded" + file.suffix)
    if out.exists() and out.stat().st_mtime >= file.stat().st_mtime:
        logger.info(f"[INFO] 品牌檔已存在且未過期,跳過: {out}")
        return out

    logger.info(f"[INFO] 拼接品牌動畫: {file}")
    trim_end = detect_end_card(file, logger)  # 裁掉 NotebookLM 結束卡
    mid_v = f"[1:v]trim=end={trim_end},setpts=PTS-STARTPTS[v1]" if trim_end else "[1:v]fps=24,setpts=PTS-STARTPTS[v1]"
    mid_a = f"[1:a]atrim=end={trim_end},asetpts=PTS-STARTPTS[a1]" if trim_end else "[1:a]aresample=44100[a1]"
    cmd = [
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(intro), "-i", str(file), "-i", str(outro),
        # 保留音訊(旁白 + 片頭尾音樂)— 之前 a=0 造成靜音 bug
        "-filter_complex",
        (
            "[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=24,setpts=PTS-STARTPTS[v0];"
            f"{mid_v};"
            "[2:v]scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=24,setpts=PTS-STARTPTS[v2];"
            f"[0:a]aresample=44100[a0];{mid_a};[2:a]aresample=44100[a2];"
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
