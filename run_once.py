"""
單次執行入口（給 Claude Code routines / cron / 手動執行用）。

不啟動 FastAPI / APScheduler，直接跑一次 run_newsletter_pipeline()
然後退出。退出碼：0 成功，1 失敗（pipeline 內部例外已自行推 Telegram 告警）。
"""

import asyncio
import sys

from app.clients import close_http_client, init_http_client
from app.config import log
from app.pipeline import run_newsletter_pipeline


async def _main() -> int:
    await init_http_client()
    try:
        await run_newsletter_pipeline()
        return 0
    except Exception as e:  # noqa: BLE001
        log.exception("run_once 執行失敗：%s", e)
        return 1
    finally:
        await close_http_client()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
