"""讓測試可以直接 import scripts/ 下的模組,並把 log 輸出隔離到暫存目錄。"""

import atexit
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# 步驟腳本在 **import 時**就呼叫 setup_logging(),而 setup_logging 會建一個指向
# logs/<step>.log 的 FileHandler(建構時就 open)→ 光是匯入就把正式 log 開起來寫。
#
# 2026-10-08 在 VPS 上實測到:deploy.sh 的遠端 pytest 會把測試的 WARN 寫進正式
# logs/youtube_upload.log,例如「top1.json 是 2026-10-08 的選題,影片檔名日期是
# 2026-10-07 —— 兩者不同天」— 那筆**看起來就像真的跨日補傳事件**,事故時 grep
# 正式 log 會被帶去查不存在的問題。同一天 01:05 的部署也留了 6 筆同型雜訊,
# 是每次都發生、不是偶發。
#
# 修在 conftest 而非各測試:conftest 在**任何 test_* 模組被匯入之前**載入,
# 而副作用發生在那些模組 import 的當下 —— 用 fixture(即使 autouse/session)
# 都來不及,檔案早就被打開了。改在這裡也一併涵蓋手動 `pytest`(CLAUDE.md 有寫)。
import _base  # noqa: E402  (需先設好 sys.path)

TEST_LOGS_DIR = Path(tempfile.mkdtemp(prefix="pipeline-test-logs-"))
atexit.register(shutil.rmtree, TEST_LOGS_DIR, ignore_errors=True)
_base.LOGS_DIR = TEST_LOGS_DIR
