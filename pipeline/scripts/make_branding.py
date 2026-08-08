#!/usr/bin/env python3
"""生成品牌片頭/片尾動畫(一次性資產,FFmpeg 程序化生成)。

用法: python scripts/make_branding.py
輸出: output/branding/intro.mp4、outro.mp4(1280x720@24fps,含合成音樂與音效)

視覺(對齊 NotebookLM embedded 的深色電路 HUD 風格):
- 整個畫面以 2x(2560x1440)生成再縮小 → 字形真正超採樣,銳利
- 深炭藍→靛藍漸層 + 中央光暈 + 電路板走線(ASS 向量繪製)+ 四角 HUD 框架 + 掃描線
- 音效全部 FFmpeg 合成: whoosh / 上升掃頻 sweep / chime / tick
"""

import subprocess
from pathlib import Path

from _common import OUTPUT_DIR, fail, setup_logging

logger = setup_logging("make_branding")

BRAND_DIR = OUTPUT_DIR / "branding"
FONT = "Liberation Sans"  # libass 用字體名稱
OUT_W, OUT_H, FPS = 1280, 720, 24      # 最終輸出
SCALE = 2
W, H = OUT_W * SCALE, OUT_H * SCALE    # 內部渲染 2x → 真超採樣
FFMPEG = Path.home() / "bin" / "ffmpeg"
if not FFMPEG.exists():
    FFMPEG = Path("/usr/bin/ffmpeg")

BG_C0 = "0x0b1024"   # 深炭藍
BG_C1 = "0x232a4d"   # 靛藍
ACCENT = "0x2dd4bf"  # 青綠(與 embedded HUD 同色系)

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{FONT},176,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1
Style: Sub,{FONT},112,&H00F5E0CF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1
Style: Accent,{FONT},192,&H00BFD42D,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1
Style: Handle,{FONT},104,&H00F5E0CF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1
Style: Trace,{FONT},1,&H00BFD42D,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

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


def _trace(start: float, end: float, pts: list[tuple[int, int]], alpha: str = "&H60&") -> str:
    """電路走線(ASS 向量繪製): pts 為折線頂點,線寬 8px(2x 畫布)。"""
    body = " ".join(f"l {x} {y}" for x, y in pts[1:])
    back = " ".join(f"l {x} {y + 8}" for x, y in reversed(pts[:-1]))
    return (
        f"Dialogue: 0,{_ts(start)},{_ts(end)},Trace,,0,0,0,,"
        f"{{\\alpha{alpha}\\fad(300,200)\\p1}}m {pts[0][0]} {pts[0][1]} {body} {back}{{\\p0}}"
    )


def _brackets(start: float, end: float) -> str:
    """四角 HUD 框架(ASS 向量,青色,24px 粗)。"""
    shapes = []
    for x, y in ((60, 60), (W - 220, 60), (60, H - 220), (W - 220, H - 220)):
        shapes.append(
            f"m {x} {y} l {x + 160} {y} l {x + 160} {y + 24} l {x + 24} {y + 24} "
            f"l {x + 24} {y + 160} l {x} {y + 160} l {x} {y}"
        )
    return (
        f"Dialogue: 0,{_ts(start)},{_ts(end)},Trace,,0,0,0,,"
        f"{{\\alpha&H50&\\fad(400,200)\\p1}}" + " ".join(shapes) + "{\\p0}"
    )


def write_ass(path: Path, lines: list[tuple[float, float, str, str, int, int]], traces: list) -> None:
    """lines: [(開始秒, 結束秒, Style 名, 文字, x, y)];traces: 額外繪製事件字串。"""
    events = []
    for start, end, style, text, x, y in lines:
        sp = "\\fsp4" if style == "Main" else ("\\fsp2" if style in ("Sub", "Handle") else "")
        events.append(
            f"Dialogue: 0,{_ts(start)},{_ts(end)},{style},,0,0,0,,"
            f"{{\\fad(600,400){sp}\\pos({x},{y})}}{text}"
        )
    events.extend(traces)
    path.write_text(ASS_HEADER.format(W=W, H=H, FONT=FONT) + "\n".join(events) + "\n", encoding="utf-8")


def audio_filters(sfx_events: list[tuple[float, str]], duration: float) -> tuple[list[str], str]:
    """音效事件 → (額外的 lavfi 輸入列表, 濾鏡片段)。"""
    inputs, chains = [], []
    for i, (t, kind) in enumerate(sfx_events):
        idx = i + 3  # 0=漸層(v), 1=音樂(a), 2=光暈(v), 3+=音效(a)
        inputs.append(SFX_SOURCES[kind])
        delay = int(t * 1000)
        fx = {
            "whoosh": "lowpass=f=2500,afade=t=in:d=0.05,afade=t=out:st=1.0:d=0.2,volume=0.4",
            "sweep": "afade=t=out:st=0.5:d=0.4,volume=0.5",
            "chime": "volume=0.5",
            "tick": "volume=0.4",
        }[kind]
        chains.append(f"[{idx}:a]{fx},adelay={delay}|{delay}[s{i}]")
    mix_in = "".join(f"[s{i}]" for i in range(len(sfx_events)))
    pad = f"[1:a]afade=t=in:d=0.8,afade=t=out:st={duration - 1}:d=1,volume=0.55[pad]"
    amix = f"[pad]{mix_in}amix=inputs={len(sfx_events) + 1}:normalize=0,alimiter[aout]"
    return inputs, f"{pad};{';'.join(chains)};{amix}"


def build(name: str, duration: float, ass_lines: list, traces: list, sfx_events: list, scanline: bool = True) -> None:
    ass_file = BRAND_DIR / f"{name}.ass"
    write_ass(ass_file, ass_lines, traces)
    out = BRAND_DIR / f"{name}.mp4"

    # 視覺鏈: 漸層 → 中央光暈 → 字幕+HUD 繪製 → 掃描線 → 縮到 720p(超採樣)
    glow_size = 1200
    vf = f"[0:v][glow]overlay=x={ (W - glow_size) // 2 }:y={ (H - glow_size) // 2 }[bg]"
    vf += f";[bg]subtitles={ass_file}"
    if scanline:
        vf += f",drawbox=x=0:y='mod(t*300,{H})':w={W}:h=6:color={ACCENT}@0.07:t=fill"
    vf += f"[vt];[vt]scale={OUT_W}:{OUT_H}:flags=lanczos[vout]"

    music = (
        f"aevalsrc=0.11*sin(2*PI*220*t)+0.08*sin(2*PI*261.63*t)"
        f"+0.06*sin(2*PI*329.63*t)+0.04*sin(2*PI*440*t):s=44100:d={duration}"
    )
    sfx_inputs, audio_fc = audio_filters(sfx_events, duration)

    cmd = [
        "-f", "lavfi", "-i",
        f"gradients=size={W}x{H}:rate={FPS}:speed=0.02:c0={BG_C0}:c1={BG_C1}:nb_colors=2",
        "-f", "lavfi", "-i", music,
        "-f", "lavfi", "-i", f"color=c={ACCENT}@0.05:s={glow_size}x{glow_size}:r={FPS}",
    ]
    for src in sfx_inputs:
        cmd += ["-f", "lavfi", "-i", src]
    cmd += [
        "-filter_complex",
        (
            f"[2:v]format=rgba,gblur=sigma=300[glow];"
            f"{vf};{audio_fc}"
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
    intro_traces = [
        _trace(0.3, 2.0, [(120, 200), (420, 200), (420, 700), (760, 700), (760, 300)]),
        _trace(0.6, 2.4, [(W - 120, H - 160), (W - 460, H - 160), (W - 460, 620), (W - 820, 620)]),
        _brackets(0.2, 5.0),
    ]
    build(
        "intro",
        5.0,
        [
            (0.7, 5.0, "Main", "TechSnack Daily", W // 2, 480),
            (1.6, 5.0, "Sub", "DAILY TECH NEWS BRIEFING", W // 2, 700),
        ],
        intro_traces,
        sfx_events=[
            (0.0, "whoosh"),
            (0.7, "sweep"),
            (1.6, "chime"),
            (3.2, "tick"),
        ],
    )
    outro_traces = [
        _trace(0.3, 3.6, [(120, H - 160), (420, H - 160), (420, 240), (760, 240)]),
        _brackets(0.2, 4.0),
    ]
    build(
        "outro",
        4.0,
        [
            (0.6, 4.0, "Sub", "See you tomorrow", W // 2, 440),
            (1.4, 4.0, "Accent", "SUBSCRIBE", W // 2, 680),
            (2.0, 4.0, "Handle", "youtube.com/@techsnack-daily", W // 2, 900),
        ],
        outro_traces,
        sfx_events=[
            (0.6, "chime"),
            (1.4, "sweep"),
            (2.0, "tick"),
        ],
    )
    logger.info("[PASS] 品牌動畫生成完成")


if __name__ == "__main__":
    main()
