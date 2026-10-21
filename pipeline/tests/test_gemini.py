"""_gemini 的每日配額偵測測試(修正 A)。

背景:2026-10-08 VPS 的 logs/rank_news.log 出現 274 筆同型 429 —
免費層每日 20 次配額耗盡,而舊版對每個 429 都重試 2 次(共 3 個請求 + 15 秒
等待),catch-up 每 15 分鐘再燒一次,整天打在同一道牆上。
daily_quota_hit 的責任就是把「今天沒了」和「等一下再來」分開:
前者立刻失敗,後者照舊重試。
"""

import json
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


def test_gemini_json_retries_5xx_only_once(monkeypatch):
    """5xx 每次嘗試都算進每日配額(2026-10-20 實測 21 次請求耗盡 20 次額度),
    所以最多再試一次;仍失敗交給 15 分鐘後的 catch-up,不在本輪連續重打。

    這裡刻意停用備援模型,單獨釘住主模型的重試政策;備援行為由下面幾支測試
    分別釘住,兩件事才不會互相掩蓋。
    """
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "")
    calls, sleeps = _capture(monkeypatch, text_resp("Service Unavailable", 503))
    with pytest.raises(SystemExit):
        gemini_json("hi", logging.getLogger("t"))
    assert len(calls) == 2
    assert sleeps == [5]


# --- 5xx 自動切換備援模型(2026-10-21)-----------------------------------------


def ok_json(payload=None) -> httpx.Response:
    """200 + Gemini 正常 JSON 外殼;payload 是候選人看到的結構化結果。"""
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                payload if payload is not None else {"ok": True}
                            )
                        }
                    ]
                }
            }
        ]
    }
    return httpx.Response(200, json=body, request=httpx.Request("POST", URL))


def _capture_by_model(monkeypatch, replies, *, fallback_env=None):
    """依 URL 的 model 名稱回傳對應 response,並記錄每次請求的 model。

    replies: {model: response | list[response]}。list 依序取用,最後一個會
    重複使用(讓「主模型連續兩次 503」不必寫兩份相同物件)。
    fallback_env=None → 刪除環境變數(測預設值);字串 → 設定它。
    """
    calls: list[str] = []
    sleeps: list[float] = []
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    if fallback_env is None:
        monkeypatch.delenv("GEMINI_FALLBACK_MODEL", raising=False)
    else:
        monkeypatch.setenv("GEMINI_FALLBACK_MODEL", fallback_env)
    queues = {
        model: (list(value) if isinstance(value, list) else [value])
        for model, value in replies.items()
    }

    def fake_post(url, **kwargs):
        model = url.split("/models/", 1)[1].split(":", 1)[0]
        calls.append(model)
        queue = queues[model]
        result = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("_gemini.time.sleep", lambda s: sleeps.append(s))
    return calls, sleeps


def test_gemini_json_returns_primary_result_without_fallback(monkeypatch):
    calls, _ = _capture_by_model(
        monkeypatch,
        {
            "gemini-3.5-flash": ok_json({"ok": True}),
            "gemini-3.1-flash-lite": AssertionError("主模型成功時不該用備援"),
        },
    )
    assert gemini_json("hi", logging.getLogger("t")) == {"ok": True}
    assert calls == ["gemini-3.5-flash"]


def test_gemini_json_switches_to_fallback_after_persistent_5xx(monkeypatch):
    """2026-10-21 事故的復原路徑:主模型重試仍 503 → 備援模型接手。

    主模型 2 次嘗試(含 5s 重試),備援只打 1 次;備援的 200 JSON 要能被
    正常解析回傳。
    """
    calls, sleeps = _capture_by_model(
        monkeypatch,
        {
            "gemini-3.5-flash": [text_resp("busy", 503), text_resp("busy", 503)],
            "gemini-3.1-flash-lite": ok_json({"top1": {"index": 0}}),
        },
    )
    result = gemini_json("hi", logging.getLogger("t"))
    assert result == {"top1": {"index": 0}}
    assert calls == [
        "gemini-3.5-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
    ]
    assert sleeps == [5]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_gemini_json_falls_back_on_any_retryable_5xx(monkeypatch, status):
    """備援的觸發條件與既有 5xx 重試集合一致,不只看 503。"""
    calls, _ = _capture_by_model(
        monkeypatch,
        {
            "gemini-3.5-flash": [
                text_resp("busy", status),
                text_resp("busy", status),
            ],
            "gemini-3.1-flash-lite": ok_json({"ok": True}),
        },
    )
    assert gemini_json("hi", logging.getLogger("t")) == {"ok": True}
    assert calls == [
        "gemini-3.5-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
    ]


def test_gemini_json_fallback_model_env_override(monkeypatch):
    """GEMINI_FALLBACK_MODEL 可換成任何模型名稱(不必改程式)。"""
    calls, _ = _capture_by_model(
        monkeypatch,
        {
            "gemini-3.5-flash": [text_resp("busy", 503), text_resp("busy", 503)],
            "custom-lite": ok_json({"ok": True}),
        },
        fallback_env="custom-lite",
    )
    assert gemini_json("hi", logging.getLogger("t")) == {"ok": True}
    assert calls == [
        "gemini-3.5-flash",
        "gemini-3.5-flash",
        "custom-lite",
    ]


def test_gemini_json_explicit_fallback_model_overrides_env(monkeypatch):
    calls, _ = _capture_by_model(
        monkeypatch,
        {
            "gemini-3.5-flash": [text_resp("busy", 503), text_resp("busy", 503)],
            "explicit-lite": ok_json({"ok": True}),
        },
        fallback_env="env-lite",
    )
    result = gemini_json(
        "hi", logging.getLogger("t"), fallback_model="explicit-lite"
    )
    assert result == {"ok": True}
    assert calls == [
        "gemini-3.5-flash",
        "gemini-3.5-flash",
        "explicit-lite",
    ]


def test_gemini_json_without_fallback_does_not_try_second_model(monkeypatch):
    """設成空字串即可完全停用備援(只影響 5xx,不影響其他錯誤政策)。"""
    calls, _ = _capture_by_model(
        monkeypatch,
        {
            "gemini-3.5-flash": [text_resp("busy", 503), text_resp("busy", 503)],
        },
        fallback_env="",
    )
    with pytest.raises(SystemExit):
        gemini_json("hi", logging.getLogger("t"))
    assert calls == ["gemini-3.5-flash", "gemini-3.5-flash"]


def test_gemini_json_same_fallback_model_is_not_retried_again(monkeypatch):
    """備援設成主模型本身時不可再打一次(會變成同一模型 3 次請求)。"""
    calls, _ = _capture_by_model(
        monkeypatch,
        {
            "gemini-3.5-flash": [text_resp("busy", 503), text_resp("busy", 503)],
        },
        fallback_env="gemini-3.5-flash",
    )
    with pytest.raises(SystemExit):
        gemini_json("hi", logging.getLogger("t"))
    assert calls == ["gemini-3.5-flash", "gemini-3.5-flash"]


def test_gemini_json_fallback_5xx_fails_without_retrying_fallback(monkeypatch):
    """兩個模型同時 503 時,備援只打一次 — 不要把第二份 20/天也燒光。"""
    calls, sleeps = _capture_by_model(
        monkeypatch,
        {
            "gemini-3.5-flash": [text_resp("busy", 503), text_resp("busy", 503)],
            "gemini-3.1-flash-lite": text_resp("still busy", 503),
        },
    )
    with pytest.raises(SystemExit):
        gemini_json("hi", logging.getLogger("t"))
    assert calls == [
        "gemini-3.5-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
    ]
    assert sleeps == [5]


def test_gemini_json_does_not_fallback_on_non_5xx(monkeypatch):
    """400/401/403 換模型也救不了,不要浪費備援配額。"""
    calls, _ = _capture_by_model(
        monkeypatch,
        {
            "gemini-3.5-flash": text_resp("bad request", 400),
            "gemini-3.1-flash-lite": AssertionError("非 5xx 不該切換模型"),
        },
    )
    with pytest.raises(SystemExit):
        gemini_json("hi", logging.getLogger("t"))
    assert calls == ["gemini-3.5-flash"]


def test_gemini_json_mixed_5xx_and_rate_limit_sequence(monkeypatch):
    """503 → 每分鐘 429 → 503:共用 3 次 attempt 上限,最後才切備援。

    純 5xx 序列主模型最多 2 個請求;429 會吃掉一次重試,所以混合序列
    最多 3 個 — 文件的「主模型最多 2 個」必須加上這個限定。
    """
    calls, sleeps = _capture_by_model(
        monkeypatch,
        {
            "gemini-3.5-flash": [
                text_resp("busy", 503),
                json_resp(per_minute_429()),
                text_resp("busy", 503),
            ],
            "gemini-3.1-flash-lite": ok_json({"ok": True}),
        },
    )
    assert gemini_json("hi", logging.getLogger("t")) == {"ok": True}
    assert calls == [
        "gemini-3.5-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
    ]
    assert sleeps == [5, 10]


@pytest.mark.parametrize("body_kind", ["not-json", "missing-candidates"])
def test_gemini_json_fallback_response_still_validates_contract(
    monkeypatch, body_kind
):
    """備援回 200 也要走完整 JSON 契約;壞格式必須 fail,不能回半成品。"""
    if body_kind == "not-json":
        bad = httpx.Response(
            200,
            text="<html>not json</html>",
            request=httpx.Request("POST", URL),
        )
    else:
        bad = httpx.Response(
            200,
            json={"candidates": []},
            request=httpx.Request("POST", URL),
        )
    calls, _ = _capture_by_model(
        monkeypatch,
        {
            "gemini-3.5-flash": [text_resp("busy", 503), text_resp("busy", 503)],
            "gemini-3.1-flash-lite": bad,
        },
    )
    with pytest.raises(SystemExit):
        gemini_json("hi", logging.getLogger("t"))
    assert calls == [
        "gemini-3.5-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
    ]


def test_gemini_json_fallback_retries_per_minute_429(monkeypatch):
    """備援的每分鐘 429 走既有重試;只有 5xx 才是「打一次、不重試」。"""
    calls, sleeps = _capture_by_model(
        monkeypatch,
        {
            "gemini-3.5-flash": [text_resp("busy", 503), text_resp("busy", 503)],
            "gemini-3.1-flash-lite": [
                json_resp(per_minute_429()),
                json_resp(per_minute_429()),
                json_resp(per_minute_429()),
            ],
        },
    )
    with pytest.raises(SystemExit):
        gemini_json("hi", logging.getLogger("t"))
    assert calls == [
        "gemini-3.5-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite",
    ]
    assert sleeps == [5, 5, 10]


def test_gemini_json_does_not_fallback_on_connection_error(monkeypatch):
    """連線錯誤不是模型可用性問題,不該浪費備援配額。"""
    calls, sleeps = _capture_by_model(
        monkeypatch,
        {
            "gemini-3.5-flash": [
                text_resp("busy", 503),
                httpx.ConnectError("connection refused"),
            ],
            "gemini-3.1-flash-lite": AssertionError("連線錯誤不該切換模型"),
        },
    )
    with pytest.raises(SystemExit):
        gemini_json("hi", logging.getLogger("t"))
    assert calls == ["gemini-3.5-flash", "gemini-3.5-flash"]
    assert sleeps == [5]


def test_gemini_json_reports_last_5xx_in_final_error(monkeypatch, caplog):
    """503 → 500 後最終錯誤要用最後一個 500,不是第一個 503。"""
    calls, sleeps = _capture_by_model(
        monkeypatch,
        {"gemini-3.5-flash": [text_resp("busy", 503), text_resp("busy", 500)]},
        fallback_env="",
    )
    caplog.set_level(logging.ERROR, logger="t")
    with pytest.raises(SystemExit):
        gemini_json("hi", logging.getLogger("t"))
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert calls == ["gemini-3.5-flash", "gemini-3.5-flash"]
    assert sleeps == [5]
    assert any("HTTP 500" in m for m in errors)
    assert not any("HTTP 503" in m for m in errors)
