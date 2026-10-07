#!/usr/bin/env python3
"""Daily pipeline(per-channel): fetch_news → rank_news → collect_sources。

用法:
  python scripts/run_daily.py                      # 跑 config 中所有頻道
  python scripts/run_daily.py --channel tech       # 只跑 tech 頻道
  python scripts/run_daily.py --skip-fetch         # 跳過抓取

子步驟以 import 呼叫(同程序執行,注入 argv)— 不 spawn 子程序,
路徑與 cwd / 啟動方式無關;腳本改名時錯誤在 import 當下顯現。

冪等(catch-up 補跑):cron 是 08:00 一次 + */15 8-14 補跑,一天最多 28 輪。
每一步都用它的產物判斷是否已完成,已完成就跳過:
  - top1.json 的 date == 今天 → fetch/rank 都不必再跑(選題已鎖定)
  - sources.json 存在、有來源、topic 等於今天的選題 → collect 不必再跑
沒了這層保護,rank 每次補跑都會再燒一次 Gemini 配額(免費層一天只有 20 次),
而且可能把早上的選題換掉 — 影片還沒生成時,影片檔閘門擋不住這種重選。
"""

import importlib
import json
import sys
from pathlib import Path

# 確保 scripts/ 在 import 路徑上 — 與 cwd、啟動方式無關
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _common import (
    channel_dir,
    flag_value,
    load_channels,
    load_env,
    resolve_channel,
    setup_logging,
    today_str,
)

STEPS = {"fetch": "fetch_news", "rank": "rank_news", "collect": "collect_sources"}


def run(module_name: str, args: list[str], logger) -> None:
    """同程序呼叫步驟腳本的 main()(注入 argv)。非零 exit code 向外傳播。"""
    logger.info(f"===== 執行 {module_name} {' '.join(args)} =====")
    module = importlib.import_module(module_name)
    old_argv = sys.argv
    sys.argv = [f"{module_name}.py", *args]
    try:
        module.main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code != 0:
            logger.error(
                f"[FAIL] {module_name} 失敗 (exit {code}) — 請查 logs/{module_name}.log"
            )
            sys.exit(code)
    finally:
        sys.argv = old_argv
    logger.info(f"===== {module_name} 完成 =====")


def load_top1(cdir: Path) -> dict:
    """讀今天的選題檔;不存在 / JSON 損壞 / 不是物件 → 空 dict(= 尚未鎖定)。

    壞掉的 top1.json 一律當成「沒跑過」重跑,而不是當成「已完成」跳過 —
    跳過會讓整個補跑流程安靜地什麼都不做(違反不靜默失敗原則)。
    """
    path = cdir / "top1.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def locked_topic(top1: dict, today: str) -> str:
    """top1.json 是否為**今天**的選題;是則回傳主題字串,否則空字串。

    只認 date 相符的 — 昨天的 top1.json 還在(rank 今天失敗過)不代表今天
    已經選好題,拿它去擋 fetch/rank 會讓今天完全選不出新聞。
    """
    if top1.get("date") != today:
        return ""
    news = top1.get("news")
    if not isinstance(news, dict):
        news = top1  # 與 collect_sources / build_metadata 相同的容忍寫法
    title = news.get("title")
    return title if isinstance(title, str) and title else ""


def sources_ready(cdir: Path, topic: str) -> bool:
    """sources.json 是否已是**這個主題**的完成品。

    collect_sources 只在 ok_sources 非空時才寫檔,所以「存在」通常已足夠;
    仍逐一驗證欄位,因為這份檔案是人看得懂、也可能被手改的 JSON。
    topic 比對是關鍵:選題若在早上之後換過(top1.json 被覆寫),舊的
    sources.json 就是別的題目的來源,必須重收。
    """
    path = cdir / "sources.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        return False
    return data.get("topic") == topic


def skip_reason(step: str, cdir: Path, today: str, top1: dict) -> str:
    """這個步驟是否該跳過:回傳原因字串;空字串 = 必須執行(純函式,可單元測試)。"""
    topic = locked_topic(top1, today)
    # 選題已鎖定就不再重跑 fetch/rank — 重跑會燒配額,還可能把題目換掉
    if step in ("fetch", "rank") and topic:
        return f"今天({today})的選題已鎖定:{topic}"
    if step == "collect" and topic and sources_ready(cdir, topic):
        return f"今天({today})的來源已收齊:{topic}"
    return ""


def main() -> None:
    load_env()
    logger = setup_logging("run_daily")
    args = sys.argv[1:]
    slug = flag_value(args, "--channel")
    skip = {arg.removeprefix("--skip-") for arg in args if arg.startswith("--skip-")}
    steps = [s for s in STEPS if s not in skip]
    if not steps:
        logger.error("所有步驟都被跳過,沒有事可做")
        sys.exit(1)

    today = today_str()
    # 未知 slug 會 fail,不會靜默 PASS
    targets = [resolve_channel(slug, logger)] if slug else load_channels(logger)
    for ch in targets:
        logger.info(f"########## 頻道: {ch['slug']} ({ch['keyword']}) ##########")
        cdir = channel_dir(ch)
        # 當天影片已存在 → 選題已鎖定,不重跑 fetch/rank/collect。
        # 否則 re-rank 會覆寫 top1.json(08-14 實測: 下午重選 ELBE 蓋掉早上
        # 的 OpenSTLinux),上傳時標題/描述與影片內容不符。
        if (cdir / f"video_{today}.mp4").exists():
            logger.info(
                f"[SKIP] 當天影片已存在 (video_{today}.mp4) — "
                "鎖定早上選題,不重跑新聞流程"
            )
            continue
        # 影片還沒好,但新聞流程可能已經跑完(補跑)→ 逐步用產物判斷,不重複燒配額
        top1 = load_top1(cdir)
        for step in steps:
            reason = skip_reason(step, cdir, today, top1)
            if reason:
                logger.info(f"[SKIP] {STEPS[step]}: {reason}")
                continue
            run(STEPS[step], ["--channel", ch["slug"]], logger)
    logger.info(
        "[PASS] Daily pipeline 完成 — 下一步: scripts/run_video_pipeline.py --channel <slug>"
    )


if __name__ == "__main__":
    main()
