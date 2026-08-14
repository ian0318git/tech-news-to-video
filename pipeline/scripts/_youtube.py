"""YouTube OAuth 共用元件: device flow 認證 + token 管理 + 上傳 helper。

純 httpx 實作,不依賴 google SDK。
Token 存在 output/youtube_token.json(0600),憑證在 output/client_secret.json。
"""

import json
import os
import sys
import time

import httpx
from _common import OUTPUT_DIR, fail

TOKEN_PATH = OUTPUT_DIR / "youtube_token.json"
CLIENT_SECRET_PATH = OUTPUT_DIR / "client_secret.json"
DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"  # 最小權限: 只上傳


def load_client_secret(logger) -> dict:
    """讀取 Google Cloud OAuth client JSON(Desktop app),回傳 client_id/secret。"""
    if not CLIENT_SECRET_PATH.exists():
        fail(
            logger,
            f"找不到 {CLIENT_SECRET_PATH}",
            "請依 DECISIONS.md 從 Google Cloud Console 建立 OAuth 用戶端 ID(桌面應用程式)"
            "並把下載的 JSON 存成 output/client_secret.json",
        )
    try:
        data = json.loads(CLIENT_SECRET_PATH.read_text(encoding="utf-8"))
        installed = data.get("installed", data.get("web", {}))
        # 憑證必須 0600(曾以 0644 躺在磁碟上)— 強制收緊,避免被其他本機使用者讀走
        os.chmod(CLIENT_SECRET_PATH, 0o600)
        return {
            "client_id": installed["client_id"],
            "client_secret": installed["client_secret"],
        }
    except (json.JSONDecodeError, KeyError) as exc:
        fail(logger, "client_secret.json 格式不正確", str(exc))


def device_auth(logger, client: dict) -> dict:
    """OAuth device flow — 印出網址與代碼,由使用者在任何裝置的瀏覽器完成。"""
    logger.info("[INFO] 取得裝置授權碼 ...")
    resp = httpx.post(
        DEVICE_CODE_URL,
        data={"client_id": client["client_id"], "scope": SCOPE},
        timeout=30.0,
    )
    if resp.status_code != 200:
        fail(
            logger,
            f"取得裝置授權碼失敗 (HTTP {resp.status_code})",
            resp.text[:800]
            + "\n注意: device flow 需要 OAuth client 類型為「TVs and Limited Input devices」(桌面應用程式類型會被拒絕)",
        )
    d = resp.json()
    logger.info("=" * 60)
    logger.info(f"  1. 在瀏覽器打開: {d['verification_url']}")
    logger.info(f"  2. 輸入代碼:     {d['user_code']}")
    logger.info("=" * 60)

    deadline = time.time() + d.get("expires_in", 1800)
    interval = d.get("interval", 5)
    while time.time() < deadline:
        time.sleep(interval)
        poll = httpx.post(
            TOKEN_URL,
            data={
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "device_code": d["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            timeout=30.0,
        )
        body = poll.json()
        if poll.status_code == 200 and "access_token" in body:
            if "refresh_token" not in body:
                fail(
                    logger,
                    "授權回應缺少 refresh_token(重複授權時 Google 可能省略)",
                    "請刪除 output/youtube_token.json 後重新執行 youtube_auth.py",
                )
            body["client_id"] = client["client_id"]
            body["client_secret"] = client["client_secret"]
            body["expires_at"] = time.time() + body.get("expires_in", 3600) - 60
            return body
        err = body.get("error", "")
        if err in ("authorization_pending", "slow_down"):
            if err == "slow_down":
                interval += 5
            continue
        fail(
            logger,
            f"裝置授權失敗: {err or 'unknown'}",
            json.dumps(body, ensure_ascii=False),
        )
    fail(logger, "裝置授權逾時(5 分鐘內未完成授權)")


def _save_token(token: dict) -> None:
    """原子寫入權杖: 先寫 temp + chmod,再 os.replace — 中間過程不會出現
    0644 權限或截斷內容的正式檔案(VM 暫停/斷電也不留殘骸)。"""
    tmp = TOKEN_PATH.with_name(TOKEN_PATH.name + ".tmp")
    tmp.write_text(json.dumps(token, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, TOKEN_PATH)


def ensure_access_token(logger) -> str:
    """回傳有效 access token(必要時自動 refresh / 首次授權)。"""
    if not TOKEN_PATH.exists():
        # 非互動環境(cron)沒有「人」能開瀏覽器 — 直接快速失敗,不啟動 device flow
        # 空等(2026-08-14 實測: cron 下啟動後卡 2 小時直到 expired_token,整日癱瘓)。
        if not sys.stdin.isatty():
            fail(
                logger,
                "沒有有效的 youtube_token.json,且目前不是互動式終端(cron 環境)",
                "請在互動式終端執行 python scripts/youtube_auth.py 完成授權後重跑"
                "(注意: 測試模式 OAuth app 的 refresh token 約 7 天到期,需定期重新授權)",
            )
        client = load_client_secret(logger)
        token = device_auth(logger, client)
        _save_token(token)
        logger.info(f"[OK] 授權完成,token 已存到 {TOKEN_PATH}")
        return token["access_token"]

    try:
        token = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        fail(
            logger,
            f"{TOKEN_PATH} 內容損壞(不是 JSON)",
            "刪除該檔後重跑即可重新授權(device flow)",
        )
    if token.get("expires_at", 0) > time.time():
        return token["access_token"]
    if not token.get("refresh_token"):
        fail(
            logger,
            "token 檔缺少 refresh_token(檔案可能損壞)",
            f"請刪除 {TOKEN_PATH} 後重跑 python scripts/youtube_auth.py",
        )

    # refresh: 暫時性錯誤(429/5xx)重試並保留 token;認證錯誤(400 級)才清除
    logger.info("[INFO] access token 過期,自動 refresh ...")
    resp = None
    for _attempt in range(2):
        resp = httpx.post(
            TOKEN_URL,
            data={
                "client_id": token["client_id"],
                "client_secret": token["client_secret"],
                "refresh_token": token["refresh_token"],
                "grant_type": "refresh_token",
            },
            timeout=30.0,
        )
        if resp.status_code == 200:
            break
        if resp.status_code in (429,) or resp.status_code >= 500:
            logger.warning(
                f"[WARN] refresh 暫時失敗 (HTTP {resp.status_code}),5s 後重試 ..."
            )
            time.sleep(5)
            continue
        TOKEN_PATH.unlink(missing_ok=True)
        fail(
            logger,
            f"refresh 失敗 (HTTP {resp.status_code}) — 已清除 token,請重跑 youtube_auth.py",
            resp.text[:500]
            + "\n(測試模式 OAuth app 的 refresh token 約 7 天到期,屬預期現象;重新授權即可)",
        )
    else:
        fail(
            logger,
            f"refresh 暫時失敗 (HTTP {resp.status_code}) — token 已保留,稍後重試",
            resp.text[:500],
        )
    new = resp.json()
    token.update(
        {
            "access_token": new["access_token"],
            "expires_at": time.time() + new.get("expires_in", 3600) - 60,
        }
    )
    _save_token(token)
    logger.info("[OK] token 已 refresh")
    return token["access_token"]
