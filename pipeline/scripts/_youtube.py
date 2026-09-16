"""YouTube OAuth 共用元件: device flow 認證 + token 管理 + 上傳 helper。

純 httpx 實作,不依賴 google SDK。
Token 存在 output/youtube_token.json(0600),憑證在 output/client_secret.json。
"""

import json
import os
import sys
import time
from pathlib import Path

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


def device_auth(logger, client: dict, scope: str = SCOPE) -> dict:
    """OAuth device flow — 印出網址與代碼,由使用者在任何裝置的瀏覽器完成。

    scope 預設為上傳權限;唯讀流程傳入 READ_SCOPE 以取得不同用途的權杖。
    注意 device flow 只接受特定 scope 清單 — `yt-analytics.readonly` 實測會被拒
    (invalid_scope),`youtube.readonly` 與 `youtube.upload` 皆可。
    """
    logger.info("[INFO] 取得裝置授權碼 ...")
    resp = httpx.post(
        DEVICE_CODE_URL,
        data={"client_id": client["client_id"], "scope": scope},
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


def _save_token(token: dict, path: Path = TOKEN_PATH) -> None:
    """原子寫入權杖: 先寫 temp + chmod,再 os.replace — 中間過程不會出現
    0644 權限或截斷內容的正式檔案(VM 暫停/斷電也不留殘骸)。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(token, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def ensure_token(logger, token_path: Path, scope: str, auth_script: str) -> str:
    """通用權杖管理: 讀取快取 → 過期則 refresh → 不存在則走 device flow。

    抽成通用形式是為了讓唯讀權杖(見 _youtube_read.py)共用同一套
    refresh 重試 / 快速失敗 / 原子寫入行為,不必複製一份邏輯。
    """
    if not token_path.exists():
        # 非互動環境(cron)沒有「人」能開瀏覽器 — 直接快速失敗,不啟動 device flow
        # 空等(2026-08-14 實測: cron 下啟動後卡 2 小時直到 expired_token,整日癱瘓)。
        if not sys.stdin.isatty():
            fail(
                logger,
                f"沒有有效的 {token_path.name},且目前不是互動式終端(cron 環境)",
                f"請在互動式終端執行 python scripts/{auth_script} 完成授權後重跑",
            )
        client = load_client_secret(logger)
        token = device_auth(logger, client, scope=scope)
        _save_token(token, token_path)
        logger.info(f"[OK] 授權完成,token 已存到 {token_path}")
        return token["access_token"]

    try:
        token = json.loads(token_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        fail(
            logger,
            f"{token_path} 內容損壞(不是 JSON)",
            f"刪除該檔後重跑 python scripts/{auth_script} 即可重新授權(device flow)",
        )
    if token.get("expires_at", 0) > time.time():
        return token["access_token"]
    if not token.get("refresh_token"):
        fail(
            logger,
            "token 檔缺少 refresh_token(檔案可能損壞)",
            f"請刪除 {token_path} 後重跑 python scripts/{auth_script}",
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
        token_path.unlink(missing_ok=True)
        fail(
            logger,
            f"refresh 失敗 (HTTP {resp.status_code}) — 已清除 token,請重跑 {auth_script}",
            resp.text[:500]
            + "\n(若為測試模式 OAuth app,refresh token 約 7 天到期,屬預期現象;重新授權即可)",
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
    _save_token(token, token_path)
    logger.info("[OK] token 已 refresh")
    return token["access_token"]


def ensure_access_token(logger) -> str:
    """回傳有效的上傳用 access token(必要時自動 refresh / 首次授權)。"""
    return ensure_token(logger, TOKEN_PATH, SCOPE, "youtube_auth.py")
