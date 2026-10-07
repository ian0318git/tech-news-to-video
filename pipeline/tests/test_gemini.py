"""_gemini 的每日配額偵測測試(修正 A)。

背景:2026-10-08 VPS 的 logs/rank_news.log 出現 274 筆同型 429 —
免費層每日 20 次配額耗盡,而舊版對每個 429 都重試 2 次(共 3 個請求 + 15 秒
等待),catch-up 每 15 分鐘再燒一次,整天打在同一道牆上。
daily_quota_hit 的責任就是把「今天沒了」和「等一下再來」分開:
前者立刻失敗,後者照舊重試。
"""

import logging

import httpx
import pytest
from _gemini import daily_quota_hit, gemini_json

URL = "https://generativelanguage.googleapis.com/v1beta/models/x:generateContent"

# 2026-10-08 從 VPS log 撈出的真實 payload(僅截短 message)
DAILY_QUOTA_429 = {
    "error": {
        "code": 429,
        "message": "You exceeded your current quota, please check your plan and billing details.",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [
                    {
                        "quotaMetric": "generativelanguage.googleapis.com/"
                        "generate_content_free_tier_requests",
                        "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                        "quotaValue": "20",
                    }
                ],
            },
            {
                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                "retryDelay": "85477s",
            },
        ],
    }
}


def per_minute_429(quota_id: str = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"):
    """每分鐘速率限制 — quotaMetric 與每日版本**完全相同**,只有 quotaId 不同。

    這正是判準必須看 PerDay 而不能看 metric 的原因;若誤判成每日,該重試的
    尖峰壅塞會直接放棄(免費層每分鐘 15 次,Gemini 自己的重試間隔是 5/10 秒)。
    """
    return {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {
                            "quotaMetric": "generativelanguage.googleapis.com/"
                            "generate_content_free_tier_requests",
                            "quotaId": quota_id,
                            "quotaValue": "15",
                        }
                    ],
                }
            ],
        }
    }


def json_resp(payload, status=429) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("POST", URL))


def text_resp(text, status=429) -> httpx.Response:
    return httpx.Response(status, text=text, request=httpx.Request("POST", URL))


def test_daily_quota_hit_reports_real_payload():
    msg = daily_quota_hit(json_resp(DAILY_QUOTA_429))
    assert msg, "真實的每日配額 429 必須被認出"
    assert "PerDay" in msg
    assert "20" in msg  # 額度值要出現在訊息裡,人才知道牆有多高


def test_daily_quota_hit_ignores_per_minute_limit():
    assert daily_quota_hit(json_resp(per_minute_429())) == ""


@pytest.mark.parametrize(
    "quota_id",
    [
        "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
        "GenerateRequestsPerDayPerProjectPerModel",
        "generate_requests_per_day_free_tier",
        "GenerateRequestsPerDay-Foo",
    ],
)
def test_daily_quota_hit_marker_variants(quota_id):
    """PerDay / per_day / per-day 都算 — Google 的 quotaId 命名改過不只一次。"""
    assert daily_quota_hit(json_resp(per_minute_429(quota_id)))


@pytest.mark.parametrize(
    "body",
    [
        "<html>502 Bad Gateway</html>",  # 非 JSON(閘道錯誤頁)
        "[]",  # JSON 但不是物件
        "null",
        "{}",  # 沒有 error
        '{"error": "boom"}',  # error 是字串
        '{"error": null}',
        '{"error": {"details": null}}',
        '{"error": {"details": []}}',
    ],
)
def test_daily_quota_hit_tolerates_unparseable_body(body):
    """判不出來一律回空字串(→ 照常重試),絕不能因解析失敗而拋例外。"""
    assert daily_quota_hit(text_resp(body)) == ""


@pytest.mark.parametrize(
    "details",
    [
        "not-a-list",
        [None],
        ["string-detail"],
        [{"violations": "not-a-list"}],
        [{"violations": [None, "x"]}],
        [{}],
    ],
)
def test_daily_quota_hit_tolerates_malformed_details(details):
    assert daily_quota_hit(json_resp({"error": {"details": details}})) == ""


def _capture(monkeypatch, response: httpx.Response):
    """把 httpx.post / time.sleep 換成記錄器,回傳 (呼叫次數, sleep 秒數清單)。"""
    calls: list[int] = []
    sleeps: list[float] = []
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append(1) or response)
    monkeypatch.setattr("_gemini.time.sleep", lambda s: sleeps.append(s))
    return calls, sleeps


def test_gemini_json_fails_fast_on_daily_quota(monkeypatch):
    """每日配額 → 只打一次、不 sleep,立刻 exit(修正 A 的重點)。"""
    calls, sleeps = _capture(monkeypatch, json_resp(DAILY_QUOTA_429))
    with pytest.raises(SystemExit):
        gemini_json("hi", logging.getLogger("t"))
    assert len(calls) == 1, "每日配額不該重試 — 重試只是白等 15 秒再失敗"
    assert sleeps == []


def test_gemini_json_still_retries_per_minute_429(monkeypatch):
    """對照組:每分鐘速率限制照舊重試 2 次(backoff 5s/10s),A 沒有誤傷這條路。"""
    calls, sleeps = _capture(monkeypatch, json_resp(per_minute_429()))
    with pytest.raises(SystemExit):
        gemini_json("hi", logging.getLogger("t"))
    assert len(calls) == 3
    assert sleeps == [5, 10]
