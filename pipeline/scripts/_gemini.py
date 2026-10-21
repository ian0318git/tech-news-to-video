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

# 5xx(503 等)**每一次 HTTP 嘗試都算進每日配額**。2026-10-20 實測:當天
# 09:45 前 rank_news 8 次呼叫 + 10 次重試、collect_sources 1 次呼叫 +
# 2 次重試 = 21 次請求 → 20 次/天的額度在上午就耗盡,之後 catch-up 只能
# fast-fail,整天再也救不回來。5xx 因此最多再試一次(純 5xx 序列共 2 個
# 請求;若中間夾雜每分鐘 429,共用 429 的 3 次 attempt 上限);仍失敗就
# 交給 15 分鐘後的 catch-up,不在這一輪連續重打。
SERVER_ERROR_RETRIES = 1

# 429(每分鐘速率限制)重試,backoff 5s/10s(共 3 個請求)。每日配額的 429
# 不走這條路 — daily_quota_hit() 會先攔下來直接 fail。
RATE_LIMIT_RETRIES = 2

# 2026-10-21: gemini-3.5-flash 從 08:00 連續 503 到 09:15,每次 catch-up 的
# 兩次重試把 20/20 配額燒光,兩支長片與 Shorts 全缺。免費層配額是「專案 ×
# 模型」各自計算 → 主模型 5xx 重試耗盡時,換一個模型還有自己的一份 20 次/天。
# 實測 gemini-3.1-flash-lite 在同一時段可正常回應。
DEFAULT_FALLBACK_MODEL = "gemini-3.1-flash-lite"


class GeminiServerError(Exception):
    """5xx 重試耗盡;由上層決定要不要切換備援模型。

    `fail()` 會直接 SystemExit,無法在中途換模型,所以這裡只把最後一個
    HTTP 回應往上傳。由 `gemini_json` 決定是否切換備援;備援停用或備援
    也 5xx 時才呼叫 `fail()`。
    """

    def __init__(self, response: httpx.Response):
        self.response = response
        super().__init__(f"HTTP {response.status_code}")


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


def _call_model(
    prompt: str,
    logger: logging.Logger,
    model: str,
    temperature: float,
    api_key: str,
    *,
    server_error_retries: int,
    role: str = "主模型",
) -> httpx.Response:
    """用同一個模型打一次邏輯呼叫(含 429/5xx 重試)。

    成功回傳 200 的 response;5xx 重試耗盡時拋 `GeminiServerError`,讓上層
    決定是否切換備援模型。其餘錯誤(每日配額、4xx、連線失敗)一律 fail,
    不做模型切換 — 換模型救不了 key 無效或 prompt 格式錯誤。
    """
    logger.info(f"[INFO] 呼叫 Gemini ({model}) ...")
    for attempt in range(RATE_LIMIT_RETRIES + 1):
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
            return resp
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
                        f"Gemini 每日配額已耗盡 ({quota};{role} {model})",
                        "今天的額度用完了,重試也不會成功 — 請等太平洋時間午夜配額重置,"
                        "或改用付費方案的 key。catch-up 排程會持續嘗試到今天結束為止"
                        "(每次都會立刻失敗,不會再浪費時間等待)。",
                    )
            if resp.status_code in (429, 500, 502, 503, 504):
                max_retries = (
                    RATE_LIMIT_RETRIES
                    if resp.status_code == 429
                    else server_error_retries
                )
                if attempt < max_retries:
                    wait = 5 * (attempt + 1)
                    logger.warning(
                        f"[WARN] Gemini HTTP {resp.status_code},{wait}s 後重試 "
                        f"({attempt + 1}/{max_retries}) ..."
                    )
                    time.sleep(wait)
                    continue
                if resp.status_code in (500, 502, 503, 504):
                    raise GeminiServerError(resp)
            fail(logger, f"Gemini API 錯誤 (HTTP {resp.status_code})", resp.text[:2000])
        except httpx.HTTPError as exc:
            fail(logger, "Gemini API 連線失敗", str(exc))
    raise AssertionError("unreachable")  # pragma: no cover


def gemini_json(
    prompt: str,
    logger: logging.Logger,
    model: str = "gemini-3.5-flash",
    temperature: float = 0.2,
    fallback_model: str | None = None,
) -> dict:
    """Call Gemini with a JSON-response prompt; return the parsed JSON object.

    Requires GEMINI_API_KEY in the environment (loaded from .env via load_env).

    主模型 5xx 重試耗盡時,改用備援模型再打一次:
      1. `fallback_model` 參數(測試用)
      2. 環境變數 `GEMINI_FALLBACK_MODEL`
      3. `DEFAULT_FALLBACK_MODEL`
    主模型純 5xx 序列最多 2 個請求;若中間夾雜每分鐘 429,則共用 429 的
    3 次 attempt 上限(最多 3 個請求)。

    備援模型設成空字串(或與主模型相同)即停用。備援遇到 5xx 只打一次、
    不重試(429 每分鐘限速仍照常重試)— 兩個模型同時 5xx 時不要再把
    第二份配額也燒光,交給 15 分鐘後的 catch-up。
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        fail(
            logger,
            "GEMINI_API_KEY 未設定",
            "請在 .env 加入一行 GEMINI_API_KEY=<key>(取得: https://aistudio.google.com/apikey)",
        )

    if fallback_model is None:
        fallback_model = os.environ.get(
            "GEMINI_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL
        )
    fallback_model = (fallback_model or "").strip()

    try:
        resp = _call_model(
            prompt,
            logger,
            model,
            temperature,
            api_key,
            server_error_retries=SERVER_ERROR_RETRIES,
        )
    except GeminiServerError as primary_exc:
        primary_status = primary_exc.response.status_code
        if not fallback_model or fallback_model == model:
            fallback_state = (
                "備援已停用"
                if not fallback_model
                else f"備援與主模型相同 ({fallback_model})"
            )
            fail(
                logger,
                f"Gemini API 錯誤 (HTTP {primary_status};5xx 重試耗盡,"
                f"{fallback_state})",
                primary_exc.response.text[:2000],
            )
        logger.warning(
            f"[WARN] Gemini {model} 5xx 重試耗盡 (HTTP {primary_status}),"
            f"改用備援模型 {fallback_model} ..."
        )
        try:
            resp = _call_model(
                prompt,
                logger,
                fallback_model,
                temperature,
                api_key,
                server_error_retries=0,
                role="備援模型",
            )
        except GeminiServerError as fallback_exc:
            fail(
                logger,
                f"Gemini API 錯誤 (HTTP {fallback_exc.response.status_code};"
                f"備援模型 {fallback_model} 也失敗)",
                fallback_exc.response.text[:2000],
            )

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
