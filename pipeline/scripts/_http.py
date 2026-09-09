"""護欄式 HTTP GET — DNS 解析卡死時快速失敗(共用)。

httpx/socket 的 timeout 管不到 libc 的 DNS 解析(getaddrinfo 阻塞不遵守
socket timeout)— 2026-09-08 整天卡在 "Temporary failure in name
resolution"、fetch 空轉 8 小時的實測教訓。bounded_get 在 daemon thread
執行請求,總期限 deadline 到即拋 GuardTimeoutError(卡住的 daemon thread
隨 process 結束回收,不阻塞 exit)。

逾時語意由呼叫方決定(護欄只負責「保證在期限內返回」):
- fetch_news.fetch_rss: 逾時 = 整支快速失敗(捕獲後 fail,交 catch-up 重試)
- collect_sources.check_url: 逾時 = 該 URL 視同不可達,繼續檢查其他來源

httpx 例外(含 DNS ConnectError)原樣拋給呼叫方,錯誤處理不變。
"""

import queue
import threading

import httpx


class GuardTimeoutError(httpx.TimeoutException):
    """執行緒護欄期限屆滿 — 請求(可能卡在 DNS 解析)超過 deadline 仍未返回。

    繼承 httpx.TimeoutException(→ TransportError → HTTPError),呼叫方用
    except httpx.HTTPError 即可一併捕捉。
    """


def bounded_get(
    url: str,
    *,
    headers: dict | None = None,
    timeout: float = 30.0,
    deadline: float = 45.0,
    follow_redirects: bool = True,
) -> httpx.Response:
    """執行 httpx.get 但保證 deadline 內返回。deadline 必須 > timeout。"""
    box: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            resp = httpx.get(
                url,
                headers=headers or {},
                timeout=timeout,
                follow_redirects=follow_redirects,
            )
            box.put(("ok", resp))
        except Exception as exc:  # httpx.HTTPError 等
            box.put(("err", exc))

    t = threading.Thread(target=worker, daemon=True, name="guarded-http")
    t.start()
    try:
        status, payload = box.get(timeout=deadline)
    except queue.Empty:
        raise GuardTimeoutError(
            f"HTTP 請求超過護欄期限 {deadline:.0f}s(DNS/連線可能卡死): {url[:120]}"
        ) from None
    if status == "err":
        assert isinstance(payload, BaseException)
        raise payload
    resp = payload
    assert isinstance(resp, httpx.Response)
    return resp
