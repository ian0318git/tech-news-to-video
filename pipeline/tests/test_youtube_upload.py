"""youtube_upload 的 resumable 續傳決策邏輯測試(純函式,不連網)。

情境對應: 上傳完成但回應遺失 → 重跑查狀態補記;斷線 → 續傳;session 過期 → 重啟。
"""

from youtube_upload import decide_resume, parse_range_end


def test_parse_range_end():
    assert parse_range_end("bytes=0-1234") == 1235
    assert parse_range_end("bytes=0-0") == 1
    assert parse_range_end("bytes=0-999999") == 1000000


def test_parse_range_end_missing_or_malformed():
    assert parse_range_end(None) is None
    assert parse_range_end("bytes=0-abc") is None
    assert parse_range_end("bytes=100-200") is None  # 起點不是 0 → 當作無 Range


def test_decide_done_when_response_lost():
    """最後 PUT 的 200/201 回應遺失 → 狀態查詢回 200/201 → 判定已完成。"""
    assert decide_resume(200, None, 100) == ("done", None)
    assert decide_resume(201, "bytes=0-50", 100) == ("done", None)


def test_decide_resume_offset():
    assert decide_resume(308, "bytes=0-1234", 10000) == ("resume", 1235)
    assert decide_resume(308, None, 10000) == ("resume", 0)


def test_decide_resume_clamped_to_size():
    """Range 顯示全收到 → offset==size,交由收尾流程處理。"""
    assert decide_resume(308, "bytes=0-999999", 1000) == ("resume", 1000)
    assert decide_resume(308, "bytes=0-1000000", 1000) == ("resume", 1000)


def test_decide_restart_when_session_expired():
    assert decide_resume(404, None, 100) == ("restart", None)
    assert decide_resume(410, None, 100) == ("restart", None)


def test_decide_fail_on_unknown_status():
    """無法判定的狀態 → fail,不冒重複上傳的險。"""
    assert decide_resume(500, None, 100) == ("fail", None)
    assert decide_resume(403, "bytes=0-10", 100) == ("fail", None)
