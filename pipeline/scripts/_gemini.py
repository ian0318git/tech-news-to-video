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


def gemini_json(
    prompt: str,
    logger: logging.Logger,
    model: str = "gemini-2.5-flash",
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
    retries = 2  # 暫時性錯誤(429/5xx)重試,backoff 5s/10s
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
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                wait = 5 * (attempt + 1)
                logger.warning(
                    f"[WARN] Gemini HTTP {resp.status_code},{wait}s 後重試 ({attempt + 1}/{retries}) ..."
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
