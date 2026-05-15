"""
FastAPI Server 與排程設定器

取代舊的巨大 main.py，這裡只有啟動伺服器與 API 端點設定，
以及 APScheduler 定時器。
"""

import sys
import asyncio
import hmac
import time
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import parse_qs

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel
import pytz

from app.config import settings, log, TRIGGER_COOLDOWN_SECONDS
from app.clients import (
    close_http_client,
    close_slack_client,
    init_http_client,
    init_slack_client,
)
from app.pipeline import run_newsletter_pipeline
from app.slack_commands import (
    channel_allowed,
    cmd_denied_channel,
    cmd_help,
    cmd_status,
    cmd_unknown,
    cmd_watchlist,
    verify_slack_signature,
)

# 支援 Windows 環境以避免 RuntimeError: Event loop is closed
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler(timezone=settings.timezone)
_last_manual_trigger = 0.0
_background_tasks: set = set()  # 防止背景 task 被 GC 回收


def _trigger_pipeline() -> tuple[bool, str, int]:
    """共用觸發邏輯：檢查 cooldown、排入背景任務。

    回傳 (success, message, remaining_cooldown_seconds)。
    """
    global _last_manual_trigger
    now = time.time()
    elapsed = now - _last_manual_trigger
    if elapsed < TRIGGER_COOLDOWN_SECONDS:
        remaining = int(TRIGGER_COOLDOWN_SECONDS - elapsed)
        return False, f"觸發太過頻繁，仍需冷卻 {remaining} 秒才能再次觸發。", remaining

    _last_manual_trigger = now
    task = asyncio.create_task(run_newsletter_pipeline())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return True, "日報流程已排入背景。請盯著頻道結果。", 0


def _next_scheduled_run() -> str | None:
    jobs = scheduler.get_jobs()
    return str(jobs[0].next_run_time) if jobs else None


@asynccontextmanager
async def lifespan(fast_app: FastAPI):
    """應用程式生命週期：啟動 HTTP client / Slack client，啟動排程，停止時銷毀。"""
    await init_http_client()
    await init_slack_client()

    # 加入每日任務（限定週一至週五）
    scheduler.add_job(
        run_newsletter_pipeline,
        CronTrigger(
            day_of_week="mon-fri",
            hour=settings.cron_hour,
            minute=settings.cron_minute,
            timezone=pytz.timezone(settings.timezone),
        ),
        id="daily_newsletter",
        name="Daily US Stock Newsletter",
    )
    scheduler.start()
    log.info(
        "📅 定時排程已掛載：每日 %02d:%02d (%s)",
        settings.cron_hour, settings.cron_minute, settings.timezone,
    )

    yield

    # 關閉服務
    log.info("🛑 關閉服務中...")
    scheduler.shutdown()
    await close_slack_client()
    await close_http_client()


app = FastAPI(title="美股新聞編輯室 - 後端 API", lifespan=lifespan)


class StatusResponse(BaseModel):
    status: str
    service: str
    next_run: Optional[str]


@app.get("/", response_model=StatusResponse)
def health_check():
    return StatusResponse(
        status="ok",
        service="美股新聞編輯室",
        next_run=_next_scheduled_run(),
    )


@app.post("/run")
async def manual_trigger_endpoint(authorization: str = Header(None)):
    """手動觸發日報流程，加入基本的鑑權防禦機制與過度戳擊保護。"""
    # 若配置了 ADMIN_API_KEY，需要經過基本的 authorization
    # 接受 "Bearer <token>" 或裸 token；用 hmac.compare_digest 避免 timing attack
    if settings.admin_api_key:
        provided = authorization or ""
        if provided.startswith("Bearer "):
            provided = provided[len("Bearer "):]
        if not hmac.compare_digest(provided, settings.admin_api_key):
            raise HTTPException(status_code=401, detail="Unauthorized API Key")

    ok, msg, remaining = _trigger_pipeline()
    if not ok:
        raise HTTPException(status_code=429, detail=msg)

    log.info("收到 `/run` 手動觸發，排入背景任務。")
    return {
        "status": "triggered",
        "message": (
            f"日報流程已經被丟往背景運作，請盯著頻道結果"
            f"（限制觸發：每次至少間隔 {TRIGGER_COOLDOWN_SECONDS} 秒）"
        ),
    }


@app.post("/slack/command")
async def slack_command_endpoint(request: Request):
    """Slack Slash Command 入口 `/newsletter [run|status|watchlist|help]`。"""
    if not settings.slack_signing_secret:
        raise HTTPException(
            status_code=503,
            detail="Slash command 已停用：未設定 SLACK_SIGNING_SECRET。",
        )

    body = await request.body()
    ts = request.headers.get("X-Slack-Request-Timestamp", "")
    sig = request.headers.get("X-Slack-Signature", "")
    if not verify_slack_signature(
        signing_secret=settings.slack_signing_secret,
        timestamp=ts,
        body=body,
        signature=sig,
    ):
        log.warning("Slack signature 驗證失敗（ts=%s）", ts)
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    form = {k: v[0] for k, v in parse_qs(body.decode("utf-8")).items()}
    channel_id = form.get("channel_id", "")
    channel_name = form.get("channel_name", "")
    user_id = form.get("user_id", "")
    text = (form.get("text", "") or "").strip()

    if not channel_allowed(channel_id, channel_name):
        log.info(
            "Slack 指令被拒（非允許頻道）: user=%s channel=%s/#%s",
            user_id, channel_id, channel_name,
        )
        return cmd_denied_channel()

    sub = (text.split() or [""])[0].lower()
    log.info("Slack 指令 sub=%r user=%s channel=%s", sub, user_id, channel_id)

    if sub in ("", "help"):
        return cmd_help()
    if sub == "watchlist":
        return cmd_watchlist()
    if sub == "status":
        return cmd_status(
            next_run=_next_scheduled_run(),
            last_trigger_ts=_last_manual_trigger,
            bg_task_count=len(_background_tasks),
            cooldown_seconds=TRIGGER_COOLDOWN_SECONDS,
        )
    if sub == "run":
        ok, msg, _ = _trigger_pipeline()
        prefix = "✅" if ok else "⏳"
        return {"response_type": "ephemeral", "text": f"{prefix} {msg}"}

    return cmd_unknown(sub)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
