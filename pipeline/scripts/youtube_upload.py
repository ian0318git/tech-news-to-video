#!/usr/bin/env python3
"""上傳影片到 YouTube(預設 private,per-channel)。

用法:
  python scripts/youtube_upload.py --channel tech                        # tech 頻道最新的影片
  python scripts/youtube_upload.py --channel embedded --file 指定檔.mp4  # 指定檔案
  python scripts/youtube_upload.py --privacy unlisted --title "自訂標題"

標題預設: top1.json 的爆款標題**原文照用,不加頻道前綴**(2026-10-08)——
前綴會占掉最前面的字元,而那些字元該放數字與結果。標題離譜到超過 95 字元時,
長片改用第三條 video_title_short(同一篇的較短寫法)整條替換,而不是截斷;
兩者都不可用就退回原始新聞標題。淨化與截斷一律由 build_title 收斂。
top1.json 與影片檔名不同天時退回檔名標題(該路徑仍帶前綴,見 filename_title)。
輸出: output/<slug>/youtube_uploads.json。

冪等設計: init_upload 後立即把 resumable session URI 寫進記錄(status=uploading);
若上傳中途被打斷(回應遺失/斷線/VM 暫停),重跑會先查 session 狀態 —
已完成的直接補記,未完成的從斷點續傳,不會重複上傳。
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
from _common import (
    SHORTS_TITLE_FIELD,
    TITLE_MAX,
    VIDEO_TITLE_FIELD,
    VIDEO_TITLE_SHORT_FIELD,
    build_title,
    channel_dir,
    clean_headline,
    fail,
    flag_value,
    load_env,
    program_marker,
    resolve_channel,
    save_json,
    setup_logging,
)
from _youtube import ensure_access_token

logger = setup_logging("youtube_upload")

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


UPLOADS_RECORD = "youtube_uploads.json"  # 每個頻道目錄內的上傳記錄

# 說明的長度上限(YouTube 官方 5000,留餘裕)。固定尾段保證不會被這條砍到,
# 見 build_description。
DESCRIPTION_MAX = 4900

DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")


def file_date(file: Path) -> str | None:
    """從影片檔名取日期(video_2026-10-08.branded.mp4 / shorts_2026-10-08.mp4)。"""
    m = DATE_IN_NAME.search(file.name)
    return m.group(1) if m else None


def dates_agree(top1: dict, file: Path) -> bool:
    """top1.json 的 date 與影片檔名日期是否一致。

    不一致 = top1.json 已被新的一天覆寫、而這支影片是舊的(當天影片生成成功
    但上傳失敗,隔天用 --file 補傳)→ 沿用它的標題/說明會把別篇新聞掛到這支
    影片上。任一邊取不到日期就回 True(無法證明不一致就照舊),避免誤擋手動
    補傳。2026-10-08 reviewer R3。
    """
    fdate = file_date(file)
    tdate = top1.get("date")
    if not fdate or not isinstance(tdate, str):
        return True
    return fdate == tdate


def latest_video(cdir: Path) -> Path:
    candidates = sorted(cdir.glob("video_*.mp4"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        fail(
            logger,
            f"找不到 {cdir}/video_*.mp4 — 請先執行 scripts/run_video_pipeline.py --channel <slug>",
        )
    return candidates[-1]


def load_upload_records(cdir: Path) -> dict:
    path = cdir / UPLOADS_RECORD
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning(f"[WARN] {UPLOADS_RECORD} 損壞,重新開始記錄")
    return {}


def save_upload_records(records: dict, cdir: Path) -> None:
    """寫入上傳記錄;內含 session_uri(resumable 上傳憑證)→ 收緊 0600。"""
    path = cdir / UPLOADS_RECORD
    save_json(records, path)
    os.chmod(path, 0o600)


def record_done(
    records: dict, cdir: Path, file: Path, video_id: str, title: str, privacy: str
) -> None:
    """上傳完成的記錄(status=done,清掉 session_uri)。"""
    records[file.name] = {
        "video_id": video_id,
        "url": f"https://youtu.be/{video_id}",
        "title": title,
        "privacy": privacy,
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "done",
    }
    save_upload_records(records, cdir)


def stem_label(file: Path) -> str:
    """檔名的人類可讀版本(video_2026-10-08.branded → video 2026-10-08.branded)。"""
    return file.stem.replace("_", " ")


def filename_title(prefix: str, file: Path) -> str:
    """沒有可用內容時的保守標題:帶頻道前綴 + 檔名,不對影片內容做任何描述。

    三條沒有內容的路徑(沒有 top1.json / top1.json 與影片不同天 / top1.json
    有 news 但標題全部取不到)共用這條,否則同一件事會有多種輸出
    ("video_2026-10-08" 對 "video 2026-10-08"),就像先前 TITLE_MAX 95 與 100
    並存那樣。

    **內容標題不加前綴,這裡加**。兩者不矛盾:前綴吃掉的正是「最前面那幾個
    字元」,而內容標題要用那些字元放數字與結果;這條路徑只剩日期,前綴在那裡
    是「辨識」而不是搶版面 —— 拔掉只會得到裸的 "video 2026-10-08.branded",
    連是哪個節目都看不出來。
    """
    return f"{prefix} - {stem_label(file)}"[:TITLE_MAX]


def build_description(head_text: str, slug: object) -> str:
    """影片說明 = 可變前段(摘要/來源)+ 固定的出處與**節目標記**。

    截斷只砍可變的前段:固定尾段先組好、長度從前段扣。反過來寫(整串
    `[:4900]`)的話,摘要一長就會把尾端的節目標記裁掉 —— 而那是
    `backfill_history` 歸屬節目的唯一線索,被裁掉不會有任何錯誤訊息,
    只會在幾個月後「補 0 筆」安靜地漏掉影片(同 D27 的教訓:靜默的資料遺失)。
    """
    tail_lines = [
        "",
        "由 tech-news-to-video pipeline 自動生成(Gemini 選題 → NotebookLM Video Overview)。",
    ]
    marker = program_marker(slug)
    if marker:
        tail_lines.append(marker)
    elif isinstance(slug, str) and slug.strip():
        # slug 有值卻產生不出標記(含非法字元)→ 這支影片將無法被歸屬。
        # 不是靜默失敗:沒這行警告,要幾個月後重建歷史時才會發現「補 0 筆」。
        # 空白/非字串代表**沒有 slug**,不算異常(降級路徑與測試會傳空值)。
        logger.warning(
            f"[WARN] 頻道 slug {slug!r} 產生不出節目標記(字元不合法)"
            " — 這支影片的說明不會有 program: 標記,backfill_history 將無法歸屬"
        )
    tail = "\n".join(tail_lines)
    return (head_text[: DESCRIPTION_MAX - len(tail)] + tail)[:DESCRIPTION_MAX]


def apply_custom_title(custom_title: object, fallback: str, logger) -> str:
    """套用 `--title`:淨化後非空才採用,否則沿用自動標題並發警告。

    `--title "   "` 或 `"  <  >  "` 都是 truthy 的字串,但淨化後成空 —— 直接
    截上傳等於送出一條空白標題(YouTube 拒收,或更糟:送出沒有標題的影片)。
    判斷必須在淨化**之後**,與 build_title 的 R1 同一道原則。
    """
    if not custom_title:
        return fallback
    cleaned = clean_headline(custom_title)
    if not cleaned:
        logger.warning(
            f"[WARN] --title {custom_title!r} 淨化後為空 — 改用自動產生的標題"
        )
        return fallback
    return cleaned[:TITLE_MAX]


def build_metadata(cdir: Path, channel: dict, file: Path) -> tuple[str, str]:
    """產生上傳到 YouTube 的 (標題, 說明)。

    這裡才是**真正決定 YouTube 標題**的地方 — 影片生成階段的 title 只用在
    NotebookLM 的專案名稱(該專案下載後即被自動刪除),不會出現在成品上。
    (2026-10-08:先前把爆款標題接到影片階段,等於白做 — reviewer 查出。)

    標題**不加頻道前綴**(2026-10-08 使用者指示):前綴會吃掉最前面的二十幾個
    字元,而那些字元該放數字與結果。沒有內容可放的降級路徑仍走
    filename_title() 帶前綴(見該函式)。

    說明**必須帶 PROGRAM_MARKER**:`backfill_history.parse_video` 靠它歸屬
    節目。兩個節目共用同一個 YouTube 頻道的上傳清單(實測 187 支影片裡
    tech 122 / embedded 63),所以影片標題的前綴是**唯一**的歸屬線索 ——
    而標題從 2026-10-08 起不再帶前綴,少了這個標記,去重歷史的重建工具
    會把所有新影片當成「無法歸屬」跳過(靜默地少補資料)。
    """
    top1_path = cdir / "top1.json"
    prefix = channel.get("title_prefix", "Daily News")
    top1 = None
    if top1_path.exists():
        top1 = json.loads(top1_path.read_text(encoding="utf-8"))
        if not dates_agree(top1, file):
            # 寧可用沒有資訊的檔名標題,也不要把別篇新聞的爆款標題掛上去
            logger.warning(
                f"[WARN] top1.json 是 {top1.get('date')} 的選題,影片檔名日期是"
                f" {file_date(file)} — 兩者不同天,標題與說明改用檔名"
            )
            top1 = None
    if top1 is None:
        return (
            filename_title(prefix, file),
            build_description(
                "Generated by tech-news-to-video pipeline.", channel.get("slug", "")
            ),
        )

    news = top1.get("news")
    if not isinstance(news, dict):
        # JSON 的 "news" 可能是 null/字串 → 退回用 top1 本身,不讓 .get 拋
        # AttributeError(與同輪 rank_news 的畸形輸入處理同一原則)
        news = top1
    # 爆款標題由 rank_news 隨選題一起產生(零額外 API 呼叫)。Shorts 用專屬
    # 欄位 — 直式 60 秒需要更短更鉤人的標題。檔名前綴決定它是不是 Shorts
    # (run_shorts_pipeline 的 filename_pattern 是 "shorts_{date}.mp4")。
    is_shorts = file.name.startswith("shorts_")
    field = SHORTS_TITLE_FIELD if is_shorts else VIDEO_TITLE_FIELD
    # fallback 必須是「保證非空」的字串:news.title 可能不存在、是 null,或整串
    # 只有 < >/空白。用 .get(k, default) 只擋得住第一種 —— null 會回 None、
    # 淨化後成空,兩者都會讓 build_title 在兩條尾巴都取不到時送出**空標題**
    # (前綴時代是尾端懸空的 "prefix - " 直接上傳,2026-10-08 reviewer F1)。
    # 先淨化,空了就退回檔名。
    # 標題與說明都取不到內容時,連標題也走 filename_title —— 否則會產出裸的
    # "video 2026-10-08":filename_title 的 docstring 說「沒有內容時帶前綴」,
    # 這裡若自己拼裸檔名就當場推翻那句話(2026-10-08 reviewer 指出)。
    fallback = clean_headline(news.get("title")) or filename_title(prefix, file)
    # 備援短標題只在長片用:Shorts 的欄位本來就 ≤15,再截也沒東西,而 prompt
    # 沒有給 Shorts 第三條標題(前三名各三條 = 長片主/長片備援/Shorts)。
    # 傳空字串 = build_title 照舊截斷。
    viral_short = "" if is_shorts else top1.get(VIDEO_TITLE_SHORT_FIELD)
    # head 傳空字串 = 不加頻道前綴(2026-10-08 使用者指示)。空 head 若照舊拼
    # f"{head} - {tail}" 會留下前導 " - ",所以 build_title 內部會跳過分隔符。
    title = build_title("", top1.get(field), fallback, logger, viral_short)
    head_text = "\n".join(
        [
            str(news.get("headline", "") or news.get("summary", "")),
            f"來源: {news.get('source', '')}  {news.get('url', '')}",
        ]
    )
    return title, build_description(head_text, channel.get("slug", ""))


def init_upload(
    access_token: str, size: int, title: str, description: str, privacy: str
) -> str:
    resp = httpx.post(
        f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(size),
        },
        json={
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": "28",  # Science & Technology
                "tags": ["tech news", "notebooklm"],
            },
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
        },
        timeout=60.0,
    )
    if resp.status_code not in (200, 201):
        fail(
            logger, f"建立上傳 session 失敗 (HTTP {resp.status_code})", resp.text[:1000]
        )
    location = resp.headers.get("Location")
    if not location:
        fail(logger, "回應缺少 Location header", resp.text[:500])
    return location


def query_upload_status(session_uri: str) -> httpx.Response:
    """PUT Content-Range: bytes */0 → 問 YouTube 上次 session 傳到哪。

    200/201 = 已完成(回應附影片資源);308 = 未完成(附 Range header);
    404/410 = session 失效,要重新建立。
    """
    return httpx.put(
        session_uri,
        content=b"",
        headers={"Content-Range": "bytes */0"},
        timeout=60.0,
    )


def parse_range_end(range_header: str | None) -> int | None:
    """解析 308 的 Range header(bytes=0-<end>)→ 已接收位元組數(end+1)。"""
    if not range_header:
        return None
    m = re.match(r"bytes=0-(\d+)", range_header)
    if not m:
        return None
    return int(m.group(1)) + 1


def decide_resume(
    status_code: int, range_header: str | None, size: int
) -> tuple[str, int | None]:
    """查詢 resumable session 狀態後的決定(純函式,可單元測試)。

    回傳 (動作, 參數):
      ("done", None)     — 上次上傳已完成(回應遺失)→ 補記記錄,不用重傳
      ("resume", offset) — 續傳,從 offset 送剩餘 bytes(offset==size 表示全收到,只差收尾)
      ("restart", None)  — session 失效(404/410)→ 重新 init_upload
      ("fail", None)     — 無法判定的回應 → 停止,不冒重複上傳的險
    """
    if status_code in (200, 201):
        return "done", None
    if status_code == 308:
        offset = parse_range_end(range_header) or 0
        return "resume", min(offset, size)
    if status_code in (404, 410):
        return "restart", None
    return "fail", None


def finalize_upload(session_uri: str, size: int) -> httpx.Response:
    """所有 bytes 已收到但未收尾 → 空 PUT Content-Range: bytes */<size> 完成。"""
    return httpx.put(
        session_uri,
        content=b"",
        headers={"Content-Range": f"bytes */{size}"},
        timeout=60.0,
    )


def _video_id_from(resp: httpx.Response) -> str:
    """從完成回應中取 video id;缺 id / 壞 JSON 都 fail(不默默放行)。"""
    try:
        result = resp.json()
    except json.JSONDecodeError:
        fail(logger, "上傳完成但回應不是 JSON", resp.text[:1000])
    video_id = result.get("id")
    if not video_id:
        fail(logger, "上傳完成但回應缺少 video id", resp.text[:1000])
    return video_id


def upload_body(session_uri: str, file: Path, offset: int = 0) -> dict:
    """PUT 影片內容到 session URI(bytes + 明確 Content-Length)。

    不能用 generator(data=): httpx 0.28 會轉成 Transfer-Encoding: chunked
    且無 Content-Length,Google resumable endpoint 不保證接受(chunked)。
    offset>0 表示續傳,需要 Content-Range 指定起點與總長。
    """
    size = file.stat().st_size
    with open(file, "rb") as fh:
        fh.seek(offset)
        payload = fh.read()
    headers = {"Content-Type": "video/mp4", "Content-Length": str(len(payload))}
    if offset:
        headers["Content-Range"] = f"bytes {offset}-{size - 1}/{size}"
    resp = httpx.put(
        session_uri,
        content=payload,
        headers=headers,
        timeout=300.0,
    )
    if resp.status_code not in (200, 201):
        fail(
            logger,
            f"上傳失敗 (HTTP {resp.status_code})",
            resp.text[:1000],
        )
    return resp.json()


def main() -> None:
    load_env()
    args = sys.argv[1:]
    slug = flag_value(args, "--channel")
    channel = resolve_channel(slug, logger)
    cdir = channel_dir(channel)

    file_arg = flag_value(args, "--file")
    file = Path(file_arg) if file_arg else latest_video(cdir)
    if not file.exists():
        fail(logger, f"影片檔不存在: {file}")
    # 隱私: --privacy 旗標 > .env 的 UPLOAD_PRIVACY > 預設 private
    privacy = flag_value(args, "--privacy", os.environ.get("UPLOAD_PRIVACY", "private"))
    custom_title = flag_value(args, "--title")
    force = "--force" in args

    # 防重複上傳: 同檔名已完成(status=done 或舊版無 status)→ 跳過(--force 可覆蓋);
    # status=uploading → 上次被打斷,走下面的續傳/查狀態流程
    records = load_upload_records(cdir)
    rec = records.get(file.name)
    if rec and rec.get("status") != "uploading" and not force:
        logger.info(
            f"[INFO] {file.name} 已上傳過,跳過(用 --force 強制重傳): {rec.get('url', rec.get('video_id'))}"
        )
        return

    size = file.stat().st_size
    logger.info(
        f"[INFO] 頻道 {channel['slug']}: 影片 {file} ({size / 1024 / 1024:.1f} MB),privacy={privacy}"
    )

    access_token = ensure_access_token(logger)
    title, description = build_metadata(cdir, channel, file)
    title = apply_custom_title(custom_title, title, logger)
    logger.info(f"[INFO] 標題: {title}")

    # ── 上次上傳被打斷 → 查 session 狀態: 已完成的補記,未完成的續傳,失效的重啟
    offset = 0
    session_uri = None
    if rec and rec.get("status") == "uploading" and rec.get("session_uri"):
        logger.info("[INFO] 偵測到未完成的上傳記錄,查詢 session 狀態...")
        status_resp = query_upload_status(rec["session_uri"])
        action, offset = decide_resume(
            status_resp.status_code, status_resp.headers.get("Range"), size
        )
        if action == "done":
            video_id = _video_id_from(status_resp)
            record_done(records, cdir, file, video_id, title, privacy)
            logger.info(
                f"[PASS] 上次其實已上傳完成(回應遺失),補記: https://youtu.be/{video_id}"
            )
            return
        if action == "resume":
            session_uri = rec["session_uri"]
            if offset >= size:
                # 所有 bytes 已收到,只差收尾
                final = finalize_upload(session_uri, size)
                if final.status_code not in (200, 201):
                    fail(
                        logger,
                        f"上傳收尾失敗 (HTTP {final.status_code})",
                        final.text[:1000],
                    )
                video_id = _video_id_from(final)
                record_done(records, cdir, file, video_id, title, privacy)
                logger.info(
                    f"[PASS] 上傳完成(續傳收尾): https://youtu.be/{video_id} (privacy={privacy})"
                )
                return
            logger.info(f"[INFO] 續傳: 已上傳 {offset}/{size} bytes")
        elif action == "restart":
            logger.warning("[WARN] 上傳 session 已失效,重新建立")
        else:
            fail(
                logger,
                f"無法判定上次上傳狀態 (HTTP {status_resp.status_code}),"
                "停止以避免重複上傳",
                status_resp.text[:1000],
            )

    if not session_uri:
        session_uri = init_upload(access_token, size, title, description, privacy)
        # 立即持久化 session — 之後任何崩潰/斷線,重跑都能續傳或查狀態,不會重複上傳
        records[file.name] = {"status": "uploading", "session_uri": session_uri}
        save_upload_records(records, cdir)

    logger.info("[INFO] 上傳中(36 MB 約 1-3 分鐘)...")
    result = upload_body(session_uri, file, offset=offset)
    video_id = result.get("id")
    if not video_id:
        fail(
            logger,
            "上傳回應缺少 video id",
            json.dumps(result, ensure_ascii=False)[:1000],
        )
    record_done(records, cdir, file, video_id, title, privacy)
    logger.info(f"[PASS] 上傳完成: https://youtu.be/{video_id} (privacy={privacy})")


if __name__ == "__main__":
    main()
