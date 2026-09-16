#!/usr/bin/env python3
"""頻道數據快照(唯讀) — 訂閱數、總觀看數、最近影片表現。

用法:
  python scripts/channel_stats.py            # 印摘要 + 存快照
  python scripts/channel_stats.py --json     # 只輸出 JSON(供程式化使用)

前置: python scripts/youtube_read_auth.py 已完成授權。
輸出: output/stats/channel_<日期>.json 與 output/stats/history.jsonl(逐日一行)。

配額: 一次執行消耗約 3 units(每日免費額度 10,000),每日排程無虞。
"""

import json
import os
import statistics
import sys
from datetime import datetime, timezone

import httpx
from _common import OUTPUT_DIR, fail, setup_logging, today_str
from _youtube_read import ensure_read_token

API = "https://www.googleapis.com/youtube/v3"
STATS_DIR = OUTPUT_DIR / "stats"

# 頻道掛在品牌帳號(Brand Account)時 channels.list(mine=true) 會回空,
# 此時改用 forHandle 查詢。可用環境變數覆寫。
CHANNEL_HANDLE_ENV = "YOUTUBE_CHANNEL_HANDLE"
DEFAULT_CHANNEL_HANDLE = "@techsnack-daily"
CHANNEL_PARTS = "statistics,contentDetails,snippet"


def api_get(logger, path: str, token: str, **params) -> dict:
    """呼叫 YouTube Data API(唯讀)。非 200 一律快速失敗,不靜默回空值。"""
    resp = httpx.get(
        f"{API}/{path}",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    if resp.status_code != 200:
        fail(
            logger,
            f"YouTube API {path} 失敗 (HTTP {resp.status_code})",
            resp.text[:500],
        )
    return resp.json()


def channel_from_item(item: dict) -> dict:
    """純函式: 把 API 回傳的 channel resource 正規化成我們要的欄位。"""
    stats = item.get("statistics", {})
    return {
        "id": item["id"],
        "title": item["snippet"]["title"],
        "published_at": item["snippet"]["publishedAt"],
        "subscribers": int(stats.get("subscriberCount", 0)),
        "total_views": int(stats.get("viewCount", 0)),
        "video_count": int(stats.get("videoCount", 0)),
        "uploads_playlist": item["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def resolve_handle() -> str:
    """頻道 handle(品牌帳號 fallback 用);可用 YOUTUBE_CHANNEL_HANDLE 覆寫。"""
    return os.environ.get(CHANNEL_HANDLE_ENV) or DEFAULT_CHANNEL_HANDLE


def fetch_channel(logger, token: str, handle: str | None = None) -> dict:
    """取得頻道統計與上傳清單 ID。

    先試 mine=true(一般帳號直屬頻道);若回空表示頻道掛在品牌帳號下
    (實測 2026-09-16:此情況下 mine=true 回 items=0,但 forHandle 查得到),
    改用 forHandle 重查。
    """
    data = api_get(logger, "channels", token, part=CHANNEL_PARTS, mine="true")
    items = data.get("items", [])
    if not items:
        handle = handle or resolve_handle()
        logger.info(f"[INFO] mine=true 查無頻道(品牌帳號),改用 forHandle={handle}")
        data = api_get(logger, "channels", token, part=CHANNEL_PARTS, forHandle=handle)
        items = data.get("items", [])
    if not items:
        fail(
            logger,
            f"查不到頻道(已試 mine=true 與 forHandle={handle or resolve_handle()})",
            "請確認頻道存在,或以 YOUTUBE_CHANNEL_HANDLE 指定正確的 @handle",
        )
    return channel_from_item(items[0])


def fetch_recent_videos(logger, token: str, uploads_playlist: str, limit: int = 50) -> list:
    """取得最近上傳影片的公開統計(觀看/喜歡/留言)。"""
    listing = api_get(
        logger,
        "playlistItems",
        token,
        part="contentDetails",
        playlistId=uploads_playlist,
        maxResults=min(limit, 50),
    )
    ids = [it["contentDetails"]["videoId"] for it in listing.get("items", [])]
    if not ids:
        return []
    stats = api_get(
        logger, "videos", token, part="statistics,snippet", id=",".join(ids)
    )
    videos = []
    for item in stats.get("items", []):
        st = item.get("statistics", {})
        videos.append(
            {
                "video_id": item["id"],
                "title": item["snippet"]["title"],
                "published_at": item["snippet"]["publishedAt"],
                "views": int(st.get("viewCount", 0)),
                "likes": int(st.get("likeCount", 0)),
                "comments": int(st.get("commentCount", 0)),
            }
        )
    return videos


def compute_summary(videos: list) -> dict:
    """純函式: 從影片清單算出觀看數分布(供測試;不連網)。"""
    views = [v["views"] for v in videos]
    if not views:
        return {"count": 0, "total": 0, "median": 0, "mean": 0, "max": 0, "min": 0}
    return {
        "count": len(views),
        "total": sum(views),
        "median": int(statistics.median(views)),
        "mean": int(statistics.mean(views)),
        "max": max(views),
        "min": min(views),
    }


def print_summary(channel: dict, videos: list, summary: dict) -> None:
    """人類可讀摘要。"""
    print(f"頻道: {channel['title']}")
    print(f"  訂閱數:   {channel['subscribers']:,}")
    print(f"  總觀看數: {channel['total_views']:,}")
    print(f"  影片數:   {channel['video_count']:,}")
    if not videos:
        print("\n(沒有可列出的影片)")
        return
    print(
        f"\n最近 {summary['count']} 支: "
        f"中位數 {summary['median']:,} / 平均 {summary['mean']:,} "
        f"(最高 {summary['max']:,}, 最低 {summary['min']:,})"
    )
    print("\n最舊 → 最新:")
    for v in reversed(videos):
        print(
            f"  {v['views']:>6,} views  {v['likes']:>4,} likes  "
            f"{v['published_at'][:10]}  {v['title'][:70]}"
        )


def main() -> None:
    logger = setup_logging("channel_stats")
    token = ensure_read_token(logger)

    channel = fetch_channel(logger, token)
    videos = fetch_recent_videos(logger, token, channel["uploads_playlist"])
    summary = compute_summary(videos)

    snapshot = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "date": today_str(),
        "channel": channel,
        "summary": summary,
        "recent_videos": videos,
    }

    if "--json" in sys.argv:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        print_summary(channel, videos, summary)

    # 快照: 每日一檔(覆寫同日) + 逐日一行 JSONL(時間序列)
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    (STATS_DIR / f"channel_{today_str()}.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (STATS_DIR / "history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "date": snapshot["date"],
                    "captured_at": snapshot["captured_at"],
                    "subscribers": channel["subscribers"],
                    "total_views": channel["total_views"],
                    "video_count": channel["video_count"],
                    "recent_median_views": summary["median"],
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    logger.info(f"[OK] 快照已存到 {STATS_DIR}")


if __name__ == "__main__":
    main()
