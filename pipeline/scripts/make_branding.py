#!/usr/bin/env python3
"""生成品牌片頭/片尾動畫(一次性資產,FFmpeg 程序化生成)。

用法: python scripts/make_branding.py
輸出: output/branding/intro.mp4、outro.mp4(1280x720@24fps,含合成音樂與音效)

文字用 ASS 字幕渲染(靜態 ffmpeg 無 drawtext,但支援 subtitles/libass)。
音效(全部 FFmpeg 合成,零版權問題):
- whoosh: 白噪聲掃過(開場)
- sweep: 上升掃頻 blip(雷達/科技感)
- chime: 高頻指數衰減鈴聲(提示音)
- tick: 短促高頻 tick
"""

import subprocess
from pathlib import Path

from _common import OUTPUT_DIR, fail, setup_logging

logger = setup_logging("make_branding")

BRAND_DIR = OUTPUT_DIR / "branding"
FONT = "Liberation Sans"  # libass 用字體名稱
W, H, FPS = 1280, 720, 24  # 對齊 NotebookLM 輸出(1280x720@24fps)
FFMPEG = Path.home() / "bin" / "ffmpeg"
if not FFMPEG.exists():
    FFMPEG = Path("/usr/bin/ffmpeg")

BG_C0 = "0x0b1024"   # 深炭藍
BG_C1 = "0x232a4d"   # 靛藍
ACCENT = "0x2dd4bf"  # 青綠(與 embedded HUD 同色系)

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 2560
PlayResY: 1440

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{FONT},88,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1
Style: Sub,{FONT},56,&H00F5E0CF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1
Style: Accent,{FONT},96,&H00BFD42D,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1
Style: Handle,{FONT},52,&H00F5E0CF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

SFX_SOURCES = {
    "whoosh": "anoisesrc=color=white:amplitude=0.35:duration=1.2:s=44100",
    "sweep": "aevalsrc=0.28*sin(2*PI*(250+550*t)*t):d=0.9:s=44100",
    "chime": "aevalsrc=0.32*sin(2*PI*1318.5*t)*exp(-3*t)+0.15*sin(2*PI*2637*t)*exp(-4*t):d=1.5:s=44100",
    "tick": "aevalsrc=0.25*sin(2*PI*1800*t)*exp(-18*t):d=0.2:s=44100",
}


def _ts(sec: float) -> str:
    """秒 → ASS 時間戳 (H:MM:SS.cc)。"""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int((sec - int(sec)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def write_ass(path: Path, lines: list[tuple[float, float, str, str, int, int]]) -> None:
    """lines: [(開始秒, 結束秒, Style 名, 文字, x, y)] — (x,y) 為文字中心座標"""
    events = []
    for start, end, style, text, x, y in lines:
        events.append(
            f"Dialogue: 0,{_ts(start)},{_ts(end)},{style},,0,0,0,,"
            f"{{\\fad(600,400)\\pos({x},{y})}}{text}"
        )
    path.write_text(ASS_HEADER.format(W=W, H=H, FONT=FONT) + "\n".join(events) + "\n", encoding="utf-8")


def audio_filters(sfx_events: list[tuple[float, str]], duration: float) -> tuple[list[str], str]:
    """音效事件 → (額外的 lavfi 輸入列表, 濾鏡片段)。

    sfx_events: [(時間秒, 音效型別)];輸入 0=影片, 1=音樂墊, 2+=音效。
    """
    inputs, chains = [], []
    for i, (t, kind) in enumerate(sfx_events):
        idx = i + 2
        inputs.append(SFX_SOURCES[kind])
        delay = int(t * 1000)
        if kind == "whoosh":
            fx = "lowpass=f=2500,afade=t=in:d=0.05,afade=t=out:st=1.0:d=0.2,volume=0.4"
        elif kind == "sweep":
            fx = "afade=t=out:st=0.5:d=0.4,volume=0.5"
        elif kind == "chime":
            fx = "volume=0.5"
        else:  # tick
            fx = "volume=0.4"
        chains.append(f"[{idx}:a]{fx},adelay={delay}|{delay}[s{i}]")
    mix_in = "".join(f"[s{i}]" for i in range(len(sfx_events)))
    pad = f"[1:a]afade=t=in:d=0.8,afade=t=out:st={duration - 1}:d=1,volume=0.55[pad]"
    amix = f"[pad]{mix_in}amix=inputs={len(sfx_events) + 1}:normalize=0,alimiter[aout]"
    return inputs, f"{pad};{';'.join(chains)};{amix}"


def build(name: str, duration: float, ass_lines: list, sfx_events: list, accent_bar: bool = False) -> None:
    ass_file = BRAND_DIR / f"{name}.ass"
    write_ass(ass_file, ass_lines)
    out = BRAND_DIR / f"{name}.mp4"

    filters = [f"subtitles={ass_file}"]
    if accent_bar:
        filters.append(
            f"drawbox=x=(w-600)/2:y=390:w='min(420,max(0,(t-1.2)*330))':h=4"
            f":color={ACCENT}@0.9:t=fill"
        )
    music = (
        f"aevalsrc=0.11*sin(2*PI*220*t)+0.08*sin(2*PI*261.63*t)"
        f"+0.06*sin(2*PI*329.63*t)+0.04*sin(2*PI*440*t):s=44100:d={duration}"
    )
    sfx_inputs, audio_fc = audio_filters(sfx_events, duration)

    cmd = [
        "-f", "lavfi", "-i",
        f"gradients=size={W}x{H}:rate={FPS}:speed=0.02:c0={BG_C0}:c1={BG_C1}:nb_colors=2",
        "-f", "lavfi", "-i", music,
    ]
    for src in sfx_inputs:
        cmd += ["-f", "lavfi", "-i", src]
    cmd += [
        "-filter_complex",
        (
            f"[0:v]{','.join(filters)}[vout];"
            f"{audio_fc}"
        ),
        "-map", "[vout]", "-map", "[aout]",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
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
            (0.7, 5.0, "Main", "TechSnack Daily", 1280, 480),
            (1.6, 5.0, "Sub", "DAILY TECH NEWS BRIEFING", 1280, 700),
        ],
        sfx_events=[
            (0.0, "whoosh"),   # 開場掃過
            (0.7, "sweep"),    # 主標題出現(雷達 blip)
            (1.6, "chime"),    # 副標出現
            (3.2, "tick"),     # 節奏點綴
        ],
        accent_bar=True,
    )
    build(
        "outro",
        4.0,
        [
            (0.6, 4.0, "Sub", "See you tomorrow", 1280, 440),
            (1.4, 4.0, "Accent", "SUBSCRIBE", 1280, 680),
            (2.0, 4.0, "Handle", "youtube.com/@techsnack-daily", 1280, 900),
        ],
        sfx_events=[
            (0.6, "chime"),    # See you tomorrow
            (1.4, "sweep"),    # SUBSCRIBE
            (2.0, "tick"),     # handle
        ],
    )
    logger.info("[PASS] 品牌動畫生成完成")


if __name__ == "__main__":
    main()
