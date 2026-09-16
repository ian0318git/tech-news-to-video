#!/usr/bin/env python3
"""從 YouTube 實際上傳紀錄重建 topic_history.json 的缺漏(選題去重的補救工具)。

## 為什麼需要這支

選題去重靠 `output/<slug>/topic_history.json`。但那份檔案只記得「現在還留著的」
條目 —— 2026-09-16 查證發現,舊版的 14 天窗口早已把 09-03 以前的記錄修剪光,
於是 08-06 ~ 09-02 做過的文章全部可以重新選中。當天 embedded 新闻池裡就有 4 則
已經做成影片的文章(#0 還是那篇被做了 14 次的 FIT)完全沒被擋下。

**把窗口從 14 天拉長到 90 天救不回已經被刪掉的歷史。** 唯一可靠的來源是
YouTube 本身:每支影片的描述都帶著當初的 Google News 來源 URL
(實證與新闻池的 URL 逐字元相同),而 URL 是最穩定的去重鍵。

## 用法

    python scripts/backfill_history.py                 # 乾跑,只印計畫
    python scripts/backfill_history.py --write         # 實際寫入 output/<slug>/topic_history.json

在營運目錄執行(需要 output/ 下的 state 與唯讀 token);路徑由 `_base.ROOT`
決定,與 cwd、啟動方式無關。讀 `output/youtube_read_token.json`
(先跑 `scripts/youtube_read_auth.py` 授權)。

## 設計要點

- **分節目各自重建**:頻道上的 embedded / tech 是兩個節目,`topic_history.json`
  也是各一份。查證過兩節目從未做過同一篇文章(交集 0),所以把 A 節目的文章
  塞進 B 節目的歷史會造成過度封鎖。
- **只補不外插**:既有條目原樣保留,只補「history 完全認不出來」的文章。
- **日期用雪梨時區**:影片的 `publishedAt` 是 UTC,而 pipeline 的「今天」是
  AEST —— 直接取 UTC 日期會讓 08:00 AEST 上傳的影片落到前一天。
- **每篇文章取最後一次使用的日期**,代表「這個主題最近被消耗的時間」。
- 重跑安全:已經補過的條目會被 `topic_keys` 認出來,不會重複寫入。
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# 確保 scripts/ 在 import 路徑上 — 與 cwd、啟動方式無關
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _common import OUTPUT_DIR, fail, save_json, setup_logging
from _youtube_read import ensure_read_token
from rank_news import topic_keys, url_key

SOURCE_URL_RE = re.compile(r"https://news\.google\.com/rss/articles/[^\s)]+")
# 描述第 1 行是 "<文章標題><來源>"。分隔用的是**不斷行空格**(\xa0)不是普通空格,
# 用 "  " 去切會切不開、把來源名留在標題裡 —— 那樣 title_key 就永遠對不上
# feed 的標題(\s 在 Python 3 的 str 模式會 match \xa0)。
SOURCE_SEP_RE = re.compile(r"\s{2,}")

TZ = ZoneInfo("Australia/Sydney")

# 影片標題前綴 → 節目 slug。前兩支是 2026-08-06 的首次上傳實測,
# 當時還沒有正式的 title_prefix,故用影片 ID 指定。
PREFIX_TO_SLUG = {
    "Embedded Linux Daily": "embedded",
    "TechSnack Daily": "tech",
    "Tech & AI Daily": "tech",  # tech 的舊前綴
}
VID_TO_SLUG = {"kFttyvxMZFw": "embedded", "2oN6Z-4Oi2U": "tech"}

logger = setup_logging("backfill_history")


def fetch_uploads(token: str) -> list[dict]:
    """取得頻道上所有影片的標題、發布時間與描述。"""
    import channel_stats as cs

    channel = cs.fetch_channel(logger, token)
    videos, page = [], None
    while True:
        params = {
            "part": "snippet,contentDetails",
            "playlistId": channel["uploads_playlist"],
            "maxResults": 50,
        }
        if page:
            params["pageToken"] = page
        data = cs.api_get(logger, "playlistItems", token, **params)
        for item in data["items"]:
            sn = item["snippet"]
            videos.append(
                {
                    "vid": item["contentDetails"]["videoId"],
                    "title": sn["title"],
                    "published": sn["publishedAt"],
                    "desc": sn.get("description", ""),
                }
            )
        page = data.get("nextPageToken")
        if not page:
            return videos


def parse_video(video: dict) -> dict | None:
    """影片 → {slug, date, title, url};無法歸屬或沒有來源 URL 則回 None。"""
    prefix = video["title"].split(" - ")[0]
    slug = PREFIX_TO_SLUG.get(prefix) or VID_TO_SLUG.get(video["vid"])
    if slug is None:
        logger.warning(f"[WARN] 無法歸屬的影片 {video['vid']}: {video['title'][:60]}")
    # 描述格式: 第 1 行 "<文章標題>  <來源>",第 2 行 "來源: <來源>  <URL>"
    match = SOURCE_URL_RE.search(video["desc"])
    if slug is None or not match:
        return None
    line = video["desc"].splitlines()[0].strip()
    # 取第一個「連續 2 個以上空白」之前的部分 = 文章標題。
    # 標題本身含單一空白(如 "Fjall 3.0: Rust Storage Engine"),不會被切到。
    title = SOURCE_SEP_RE.split(line, maxsplit=1)[0].strip()
    return {
        "slug": slug,
        "date": datetime.fromisoformat(video["published"].replace("Z", "+00:00"))
        .astimezone(TZ)
        .date()
        .isoformat(),
        "title": title,
        "url": match.group(0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="實際寫入(預設只乾跑)")
    args = parser.parse_args()

    token = ensure_read_token(logger)
    articles: dict[str, dict[str, dict]] = {}
    for video in fetch_uploads(token):
        rec = parse_video(video)
        if rec is None:
            continue
        bucket = articles.setdefault(rec["slug"], {})
        key = url_key(rec["url"])
        # 同一篇文章可能被做過多次 → 留最後一次
        if key not in bucket or rec["date"] > bucket[key]["date"]:
            bucket[key] = {"date": rec["date"], "title": rec["title"], "url": rec["url"]}

    if not articles:
        fail(logger, "沒有從任何影片解析出來源 URL — 影片描述格式可能變了")

    print(f"從 YouTube 解析出 {sum(len(v) for v in articles.values())} 篇文章")
    print()

    for slug, arts in sorted(articles.items()):
        path = OUTPUT_DIR / slug / "topic_history.json"
        history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

        known: set[str] = set()
        for entry in history:
            known |= topic_keys(entry.get("title", ""), entry.get("url", ""))

        added = [
            e for e in arts.values() if not (topic_keys(e["title"], e["url"]) & known)
        ]
        print(f"[{slug}] history {len(history)} 筆 → 補 {len(added)} 筆 "
              f"→ {len(history) + len(added)} 筆")

        if not added:
            continue
        merged = sorted(history + added, key=lambda h: h["date"])
        for entry in added[:5]:
            print(f"    + {entry['date']}  {entry['title'][:58]}")
        if len(added) > 5:
            print(f"    ... 另有 {len(added) - 5} 筆")

        if args.write:
            save_json(merged, path)
            print(f"    → 已寫入 {path}")
        else:
            print("    (乾跑,未寫入 — 加 --write 才會生效)")

    if not args.write:
        print("\n[DRY-RUN] 以上是預計補入的條目。確認無誤後加 --write。")


if __name__ == "__main__":
    main()
