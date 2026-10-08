"""設定與路徑: 目錄常數、頻道定義、旗標解析、pipeline 日期。

依賴規則: 只匯入 _base(兄弟模組);不得匯入 _common facade。
"""

import json
import os
import re
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


# --- YouTube 標題 -----------------------------------------------------------
# 爆款標題由 rank_news 隨選題在同一次 Gemini 呼叫一起產生(見
# RANK_PROMPT_TEMPLATE 的標題規則),寫進 top1.json。欄位名在此單一定義 —
# 產生端(rank_news)與消費端(兩支影片腳本)都從這裡取,避免字串漂移。
# 不另開 Gemini 呼叫:免費層每日配額僅 20 次,多一次呼叫就是少一天的產能。
VIDEO_TITLE_FIELD = "video_title"
SHORTS_TITLE_FIELD = "shorts_title"
# 備援長片標題:同一個故事的第二個、更短的寫法。prompt 已把長片壓到 25 字元,
# 但那是**指示**不是保證 —— 模型仍可能寫長。主標題一旦離譜到超過 TITLE_MAX,
# 有一條短備援就能整條換掉而不是硬切腰斷(硬切會砍掉最有力的字尾)。
# Shorts 沒有對應欄位:它本來就 ≤15,再截也沒東西。
VIDEO_TITLE_SHORT_FIELD = "video_title_short"

# YouTube 標題上限(官方硬限 100,留 5 字元餘裕)。
# 內容標題的截斷統一由 build_title 收斂;沒有內容可放的兩條路徑
# (filename_title / --title)各自截,因為它們根本不經過 build_title。
# 注意這是**平台硬限**,不是風格目標:風格目標(長片 25 / Shorts 15)只在
# prompt 裡,寫在這裡會變成靜默腰斷一條本來鉤人的標題。
TITLE_MAX = 95


# --- 影片說明的節目標記 ------------------------------------------------
# 單獨一行 "program: embedded"。**為什麼說明裡需要它**:`backfill_history.py`
# 要從 YouTube 反推 `output/<slug>/topic_history.json`,而兩個節目共用同一個
# 上傳清單(實測 187 支影片裡 tech 122 / embedded 63)→ 影片本身必須帶得出
# 節目身分。2026-10-08 之前這個身分藏在標題的 "<prefix> - " 裡,而標題從
# 2026-10-08 起不再帶前綴(CTR 規則)→ 少了這行,重建工具會把所有新影片
# 當成「無法歸屬」跳過,安靜地只補到舊資料(同 D27 的教訓:靜默的資料遺失)。
# 產生端(youtube_upload)與消費端(backfill_history)共用這裡的兩個函式,
# 不各自寫字串 — 字串漂移正是這類 bug 的來源(欄位名契約同理)。
PROGRAM_MARKER_KEY = "program"
# 整行比對(^…$)而非搜尋:新聞摘要本身可能出現 "program:" 字樣,
# 沒有行錨就會把摘要內容誤判成節目標記。
_PROGRAM_MARKER_RE = re.compile(
    rf"^{PROGRAM_MARKER_KEY}:\s*([A-Za-z0-9_-]+)\s*$", re.MULTILINE
)


def program_marker(slug: object) -> str:
    """產生說明用的節目標記行;slug 不是非空字串時回空字串(不寫半截標記)。"""
    if not isinstance(slug, str) or not slug.strip():
        return ""
    return f"{PROGRAM_MARKER_KEY}: {slug.strip()}"


def parse_program_marker(description: object) -> str:
    """從影片說明取出節目 slug;沒有標記則回空字串。

    回空字串的呼叫端(backfill_history)要據此退回舊的標題前綴判斷 ——
    2026-10-08 之前上傳的影片沒有這行,而那批影片仍需要被歸屬。
    """
    if not isinstance(description, str):
        return ""
    match = _PROGRAM_MARKER_RE.search(description)
    return match.group(1) if match else ""


def clean_headline(text: object) -> str:
    """標題候選的淨化:型別檢查 → 清 < > 與換行 → 收斂空白。

    換行與 < > 必須清掉:舊標題直接來自 RSS,不會有這些字元;現在是 LLM
    自由生成(「強烈懸念」公式特別容易生成問句與符號),而 YouTube Data API
    對標題含 < > 會回 400 invalidTitle,換行則破壞版面。
    非字串(JSON null / 數字 / 物件)一律視為空 — 呼叫端不該為了淨化而
    先做一次型別檢查,那會出現兩份判斷不一致的空窗。
    """
    if not isinstance(text, str):
        return ""
    return " ".join(text.replace("<", "").replace(">", "").split())


def build_title(
    head: str, viral: object, fallback: object, logger, viral_short: object = ""
) -> str:
    """組合 YouTube 標題為 "<head> - <爆款標題>"(head 為空時只留尾段)。

    viral 是 rank_news 產生的爆款標題(top1.json 的 VIDEO/SHORTS_TITLE_FIELD)。
    取不到時退回 fallback(原始新聞標題)並發警告 — 四種取不到的情況:
      1. Gemini 沒回這個欄位
      2. Gemini 回的不是字串(或只有空白)
      3. 淨化後成空字串(整串只有 < > 或空白)
      4. 去重改選了別篇 — 沿用會張冠李戴,rank_news 已改帶被選中那篇的標題
    fallback 同樣做型別檢查:JSON 的 "title": null 會讓 .get 回 None。
    永不靜默失敗(CLAUDE.md)。

    head 是選用的前置段,**目前唯一的正式呼叫端一律傳空字串**
    (youtube_upload.build_metadata):2026-10-08 起爆款標題不加頻道前綴
    (「把最重要的內容放最前面」),所以這條組合路徑在正式流程裡已經用不到。
    參數與 "<head> - tail" 的行為留著,是因為它仍是通用組合器(單元測試守著);
    而**降級標題的前綴不在這裡** —— top1.json 不可用、沒有內容可放時走
    youtube_upload.filename_title,那個函式自己帶前綴,不經過 build_title。
    空 head 不能拼成 " - tail":那會產生前導分隔符。

    viral_short(選用)是**同一篇的較短寫法**(VIDEO_TITLE_SHORT_FIELD)。只在
    「用了爆款標題、但整條超長」時才拿出來比,硬切會砍掉最有力的字尾。
    不從 fallback 分支取用:fallback 是**別篇**的新聞標題,換成 short 版沒有意義。

    2026-10-08 reviewer R1:可用性判斷必須在**淨化之後**。舊版先判斷 viral
    非空才淨化,於是 viral="<>" 會被判定「有值」→ 淨化後成空字串 → fallback
    被跳過,產出尾端懸空的 "prefix - " 直接上傳。先淨化再判斷就沒有這個洞。
    """

    def compose(part: str) -> str:
        return f"{head} - {part}" if head else part

    tail = clean_headline(viral)
    from_viral = bool(tail)
    if not from_viral:
        logger.warning(
            "[WARN] 沒有可用的爆款標題(Gemini 未回 / 型別錯誤 / 淨化後為空 /"
            " 去重改選已清空),退回原始新聞標題"
        )
        tail = clean_headline(fallback)
    if not tail:
        logger.warning("[WARN] 標題尾段為空 — 爆款標題與原始新聞標題都取不到")
    title = compose(tail)
    if len(title) > TITLE_MAX and from_viral:
        short = clean_headline(viral_short)
        if short:
            candidate = compose(short)
            # 條件只有一個:candidate ≤ 上限。它已隱含「比原標題短」—— 原標題
            # 此刻必然 > TITLE_MAX。塞不進去就維持原標題走截斷,不做二次替換。
            if len(candidate) <= TITLE_MAX:
                logger.warning(
                    f"[WARN] 標題 {len(title)} 字元超過上限 {TITLE_MAX},"
                    "已改用較短的備援標題"
                )
                return candidate
    if len(title) > TITLE_MAX:
        logger.warning(
            f"[WARN] 標題 {len(title)} 字元超過上限 {TITLE_MAX},已截斷:"
            f" {title[:70]}…"
        )
        title = title[:TITLE_MAX]
    return title


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

    PIPELINE_DATE 環境變數可覆寫(2026-08-18 停電預製 08-19 影片時使用):
    整個 pipeline(檔名、去重、新鮮度)必須用同一個「今天」才一致。
    """
    override = os.environ.get("PIPELINE_DATE")
    if override:
        try:
            datetime.strptime(override, "%Y-%m-%d")
        except ValueError:
            # 壞格式直接報錯 — 不允許靜默失敗(CLAUDE.md)
            raise ValueError(f"PIPELINE_DATE 格式錯誤: {override!r}(需 YYYY-MM-DD)")
        return override
    return datetime.now(PIPELINE_TZ).strftime("%Y-%m-%d")


AUTO_DELETE_VALUES = {"1", "true", "yes", "on"}


def auto_delete_enabled() -> bool:
    """AUTO_DELETE_NOTEBOOKS 開關(load_env 之後讀)。預設 true — 主要訴求就是別讓專案累積。

    任何值 ∈ {1, true, yes, on}(不分大小寫)啟用;其餘停用。
    """
    return os.environ.get("AUTO_DELETE_NOTEBOOKS", "true").strip().lower() in AUTO_DELETE_VALUES


def channel_dir(channel: dict) -> Path:
    """每個頻道有自己的 output/<slug>/ 目錄,避免產出互相覆蓋。"""
    d = OUTPUT_DIR / channel["slug"]
    d.mkdir(parents=True, exist_ok=True)
    return d
