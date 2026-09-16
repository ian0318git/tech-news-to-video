"""YouTube 唯讀權杖: 讀取頻道/影片統計用(與上傳權杖分離)。

為什麼獨立一份 token,而不是把 readonly 併進 youtube_token.json:
- **零風險**: 重新授權生產中的上傳權杖會有失效窗口 — 2026-08-14 實測過
  refresh token 失效導致整日 pipeline 癱瘓。唯讀權杖獨立,壞了也不影響上傳。
- **最小權限**: 上傳流程不需要讀取權限,反之亦然。

device flow 的 scope 限制(2026-09-16 實測):
- `youtube.readonly` ✅ 接受
- `youtube.upload` ✅ 接受(文件稱不在允許清單,實測可用 — 以實測為準)
- `yt-analytics.readonly` ❌ invalid_scope → Analytics API(留存率/流量來源/CTR)
  無法透過 device flow 取得,需要另一種 OAuth client 類型。
"""

from _common import OUTPUT_DIR
from _youtube import ensure_token

READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
READ_TOKEN_PATH = OUTPUT_DIR / "youtube_read_token.json"


def ensure_read_token(logger) -> str:
    """回傳有效的唯讀 access token(不存在時啟動 device flow 首次授權)。"""
    return ensure_token(logger, READ_TOKEN_PATH, READ_SCOPE, "youtube_read_auth.py")
