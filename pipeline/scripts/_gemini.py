"""Gemini API 客戶端: JSON 結構化輸出 + 暫時性錯誤重試。

依賴規則: 只匯入 _base(兄弟模組);不得匯入 _common facade。
"""

import json
import logging
import os
import time

import httpx
from _base import fail

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

# 判定「每日配額」用的字樣(比對 quotaId + quotaMetric,不分大小寫)
DAILY_QUOTA_MARKERS = ("perday", "per_day", "per-day")


def daily_quota_hit(resp: httpx.Response) -> str:
    """解析 429 的 body,判斷是否為**每日配額耗盡**;是則回傳可讀說明。

    回傳空字串 = 不是每日配額(例如每分鐘速率限制),呼叫端照常重試。

    **真實 payload**(2026-10-08 從 VPS `logs/rank_news.log` 撈出,274 次同型):
      {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                         "quotaValue": "20",
                         "quotaMetric": "generativelanguage.googleapis.com/"
                                        "generate_content_free_tier_requests"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo",
         "retryDelay": "85477s"}]}}

    判準用 quotaId/quotaMetric 的 "PerDay" 字樣,**不用 retryDelay**:同一種 429
    的 retryDelay 實測從 32s 到 85477s(≈23.7 小時 — 正是「明天再來」)都出現過,
    拿它當判準會把每分鐘限制誤判成每日,反而該重試的不重試。
    """
    try:
        error = resp.json().get("error")
    except (json.JSONDecodeError, AttributeError):
        return ""  # 非 JSON body(閘道錯誤頁)或 JSON 不是物件 → 無法判定
    if not isinstance(error, dict):
        return ""
    for detail in error.get("details") or []:
        if not isinstance(detail, dict):
            continue
        for violation in detail.get("violations") or []:
            if not isinstance(violation, dict):
                continue
            marker = (
                f"{violation.get('quotaId', '')} {violation.get('quotaMetric', '')}"
            ).lower()
            if any(m in marker for m in DAILY_QUOTA_MARKERS):
                quota_id = violation.get("quotaId", "?")
                value = violation.get("quotaValue", "?")
                return f"{quota_id}, 額度={value}"
    return ""


def gemini_json(
    prompt: str,
    logger: logging.Logger,
    model: str = "gemini-3.5-flash",
    temperature: float = 0.2,
) -> dict:
    """Call Gemini with a JSON-response prompt; return the parsed JSON object.

    Requires GEMINI_API_KEY in the environment (loaded from .env via load_env).
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        fail(
            logger,
            "GEMINI_API_KEY 未設定",
            "請在 .env 加入一行 GEMINI_API_KEY=<key>(取得: https://aistudio.google.com/apikey)",
        )
    logger.info(f"[INFO] 呼叫 Gemini ({model}) ...")
    retries = 2  # 429(每分鐘)重試,backoff 5s/10s
    # 2026-10-20: 5xx(503 等)**每一次 HTTP 嘗試都算進每日配額**。實測當天
    # 09:45 前 rank_news 8 次呼叫 + 10 次重試、collect_sources 1 次呼叫 +
    # 2 次重試 = 21 次請求 → 20 次/天的額度在上午就耗盡,之後 catch-up
    # 只能 fast-fail,整天再也救不回來。5xx 因此最多再試一次(共 2 個請求);
    # 仍失敗就交給 15 分鐘後的 catch-up,不要在這一輪連續重打。
    SERVER_ERROR_RETRIES = 1
    for attempt in range(retries + 1):
        try:
            resp = httpx.post(
                GEMINI_API_URL.format(model=model),
                params={"key": api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": temperature,
                    },
                },
                timeout=90.0,
            )
            resp.raise_for_status()
            break
        except httpx.HTTPStatusError:
            # 每日配額耗盡 → 重試只是白等(實測 retryDelay 可達 85477s ≈ 23.7h)。
            # cron 的 catch-up(*/15 8-14)每 15 分鐘再燒一次,一天 28 次全打在同一道
            # 牆上,log 被 274 筆同型 429 灌滿。直接失敗,訊息講清楚是「今天沒了」。
            # 2026-10-08:A+B+C 修正的 A。
            if resp.status_code == 429:
                quota = daily_quota_hit(resp)
                if quota:
                    fail(
                        logger,
                        f"Gemini 每日配額已耗盡 ({quota})",
                        "今天的額度用完了,重試也不會成功 — 請等太平洋時間午夜配額重置,"
                        "或改用付費方案的 key。catch-up 排程會持續嘗試到今天結束為止"
                        "(每次都會立刻失敗,不會再浪費時間等待)。",
                    )
            if resp.status_code in (429, 500, 502, 503, 504):
                max_retries = (
                    retries if resp.status_code == 429 else SERVER_ERROR_RETRIES
                )
                if attempt < max_retries:
                    wait = 5 * (attempt + 1)
                    logger.warning(
                        f"[WARN] Gemini HTTP {resp.status_code},{wait}s 後重試 "
                        f"({attempt + 1}/{max_retries}) ..."
                    )
                    time.sleep(wait)
                    continue
            fail(logger, f"Gemini API 錯誤 (HTTP {resp.status_code})", resp.text[:2000])
        except httpx.HTTPError as exc:
            fail(logger, "Gemini API 連線失敗", str(exc))

    try:
        data = resp.json()
    except json.JSONDecodeError:
        fail(
            logger,
            "Gemini 回傳 200 但 body 不是 JSON(可能是閘道錯誤頁)",
            resp.text[:2000],
        )
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        fail(
            logger,
            "Gemini 回應缺少 candidates/content",
            json.dumps(data, ensure_ascii=False)[:2000],
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        fail(logger, "Gemini 回傳內容不是 JSON", text[:2000])
