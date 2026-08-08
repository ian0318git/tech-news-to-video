#!/usr/bin/env python3
"""生成品牌片頭/片尾動畫(一次性資產,FFmpeg 程序化生成)。

用法: python scripts/make_branding.py
輸出: output/branding/intro.mp4、outro.mp4(1920x1080,30fps)

文字用 ASS 字幕渲染(靜態 ffmpeg 無 drawtext,但支援 subtitles/libass):
- 深炭藍→靛藍漸層背景 + 緩慢移動的青色光暈
- 片頭: "TechSnack Daily" 淡入 + 青色強調線展開 + 副標
- 片尾: "See you tomorrow" + SUBSCRIBE + 頻道 handle
"""

import subprocess
from pathlib import Path

from _common import OUTPUT_DIR, fail, setup_logging

logger = setup_logging("make_branding")

BRAND_DIR = OUTPUT_DIR / "branding"
FONT = "DejaVu Sans"  # libass 用字體名稱
W, H, FPS = 1280, 720, 24  # 對齊 NotebookLM 輸出(1280x720@24fps)
FFMPEG = Path.home() / "bin" / "ffmpeg"
if not FFMPEG.exists():
    FFMPEG = Path("/usr/bin/ffmpeg")

BG_C0 = "0x0b1024"   # 深炭藍
BG_C1 = "0x232a4d"   # 靛藍
ACCENT = "0x2dd4bf"  # 青綠(與 embedded HUD 同色系)

# ASS 顏色(格式 &HAABBGGRR)
WHITE = "&H00FFFFFF"
ACCENT_ASS = "&H00BFD42D"   # 0x2dd4bf → BGR
GRAY = "&H00D0B49F"         # 0x9fb4d0 → BGR

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{FONT},72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,0,5,0,0,0,1
Style: Sub,{FONT},26,&H00D0B49F,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,5,0,0,0,1
Style: Accent,{FONT},64,&H00BFD42D,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,0,5,0,0,0,1
Style: Handle,{FONT},24,&H00D0B49F,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(sec: float) -> str:
    """秒 → ASS 時間戳 (H:MM:SS.cc)。"""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int((sec - int(sec)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def write_ass(path: Path, lines: list[tuple[float, float, str, str]]) -> None:
    """lines: [(開始秒, 結束秒, Style 名, 文字)]"""
    events = []
    for start, end, style, text in lines:
        events.append(
            f"Dialogue: 0,{_ts(start)},{_ts(end)},{style},,0,0,0,,"
            f"{{\\fad(600,400)\\pos(960,0)}}{text}"
        )
    path.write_text(ASS_HEADER.format(W=W, H=H, FONT=FONT) + "\n".join(events) + "\n", encoding="utf-8")


def build(name: str, duration: float, ass_lines: list, accent_bar: bool = False) -> None:
    ass_file = BRAND_DIR / f"{name}.ass"
    write_ass(ass_file, ass_lines)
    out = BRAND_DIR / f"{name}.mp4"

    filters = [
        f"subtitles={ass_file}",
    ]
    if accent_bar:
        filters.append(
            f"drawbox=x=(w-600)/2:y=285:w='min(400,max(0,(t-1.2)*330))':h=4"
            f":color={ACCENT}@0.9:t=fill"
        )
    # gradients 是源濾鏡(無輸入)— 作為輸入 0;光暈色塊作為輸入 1;-t 控制長度
    cmd = [
        "-f", "lavfi", "-i",
        f"gradients=size={W}x{H}:rate={FPS}:speed=0.02:c0={BG_C0}:c1={BG_C1}:nb_colors=2",
        "-f", "lavfi", "-i", f"color=c={ACCENT}@0.05:s=480x480:r={FPS}",
        "-filter_complex",
        (
            f"[1:v]format=rgba,gblur=sigma=140[glow];[0:v]{','.join(filters)}[bg];"
            f"[bg][glow]overlay=x='640+170*sin(t*0.6)':y='360+120*cos(t*0.5)'"
        ),
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        str(out),
    ]
    logger.info(f"$ ffmpeg build {name} ...")
    proc = subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", *cmd], check=False)
    if proc.returncode != 0:
        fail(logger, f"ffmpeg 失敗 (exit {proc.returncode})")
    logger.info(f"[PASS] {name}: {out}")


def main() -> None:
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    build(
        "intro",
        5.0,
        [
            (0.7, 5.0, "Main", "TechSnack Daily"),
            (1.6, 5.0, "Sub", "DAILY TECH NEWS BRIEFING"),
        ],
        accent_bar=True,
    )
    build(
        "outro",
        4.0,
        [
            (0.6, 4.0, "Sub", "See you tomorrow"),
            (1.4, 4.0, "Accent", "SUBSCRIBE"),
            (2.0, 4.0, "Handle", "youtube.com/@techsnack-daily"),
        ],
    )
    logger.info("[PASS] 品牌動畫生成完成")


if __name__ == "__main__":
    main()
