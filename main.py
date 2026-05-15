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
from app.db import close_pool as close_db_pool, init_pool as init_db_pool
from app.data.watchlist import seed_from_file_if_empty
from app.pipeline import run_newsletter_pipeline
from app.slack_commands import (
    channel_allowed,
    cmd_denied_channel,
    dispatch as dispatch_slash,
    verify_slack_signature,
)
from app.slack_interactivity import dispatch as dispatch_interactivity, parse_payload
from app.state import cooldown_state, scheduler_handle

# 支援 Windows 環境以避免 RuntimeError: Event loop is closed
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler(timezone=settings.timezone)


def _trigger_pipeline() -> tuple[bool, str, int]:
    """共用觸發邏輯：檢查 cooldown、排入背景任務。

    回傳 (success, message, remaining_cooldown_seconds)。
    """
    now = time.time()
    elapsed = now - cooldown_state.last_manual_trigger
    if elapsed < TRIGGER_COOLDOWN_SECONDS:
        remaining = int(TRIGGER_COOLDOWN_SECONDS - elapsed)
        return False, f"觸發太過頻繁，仍需冷卻 {remaining} 秒才能再次觸發。", remaining

    cooldown_state.last_manual_trigger = now
    task = asyncio.create_task(run_newsletter_pipeline())
    cooldown_state.background_tasks.add(task)
    task.add_done_callback(cooldown_state.background_tasks.discard)
    return True, "日報流程已排入背景。請盯著頻道結果。", 0


def _next_scheduled_run() -> str | None:
    jobs = scheduler.get_jobs()
    return str(jobs[0].next_run_time) if jobs else None


@asynccontextmanager
async def lifespan(fast_app: FastAPI):
    """應用程式生命週期：啟動 HTTP / Slack / DB pool，啟動排程，停止時銷毀。"""
    await init_http_client()
    await init_slack_client()
    await init_db_pool()
    await seed_from_file_if_empty()

    scheduler.add_job(
        run_newsletter_pipeline,
        CronTrigger(
            day_of_week="mon-fri",
            hour=settings.cron_hour,
            minute=settings.cron_minute,
            timezone=pytz.timezone(settings.timezone),
        ),
        id=scheduler_handle.job_id,
        name="Daily US Stock Newsletter",
    )
    scheduler.start()
    scheduler_handle.scheduler = scheduler
    log.info(
        "📅 定時排程已掛載：每日 %02d:%02d (%s)",
        settings.cron_hour, settings.cron_minute, settings.timezone,
    )

    yield

    log.info("🛑 關閉服務中...")
    scheduler_handle.scheduler = None
    scheduler.shutdown()
    await close_db_pool()
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
    if settings.admin_api_key:
        provided = authorization or ""
        if provided.startswith("Bearer "):
            provided = provided[len("Bearer "):]
        if not hmac.compare_digest(provided, settings.admin_api_key):
            raise HTTPException(status_code=401, detail="Unauthorized API Key")

    ok, msg, _ = _trigger_pipeline()
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


def _require_signing_secret() -> str:
    if not settings.slack_signing_secret:
        raise HTTPException(
            status_code=503,
            detail="Slack 整合已停用：未設定 SLACK_SIGNING_SECRET。",
        )
    return settings.slack_signing_secret


def _verify_or_403(body: bytes, headers) -> None:
    secret = _require_signing_secret()
    ts = headers.get("X-Slack-Request-Timestamp", "")
    sig = headers.get("X-Slack-Signature", "")
    if not verify_slack_signature(
        signing_secret=secret, timestamp=ts, body=body, signature=sig
    ):
        log.warning("Slack signature 驗證失敗（ts=%s）", ts)
        raise HTTPException(status_code=403, detail="Invalid Slack signature")


@app.post("/slack/command")
async def slack_command_endpoint(request: Request):
    """Slack Slash Command 共用入口；依 payload `command` 欄位分派到 cmd_*。

    Slack App 需為 /status /ping /run /pause /resume /watchlist 各別註冊一條
    slash command，全部都把 Request URL 指到這個端點。
    """
    body = await request.body()
    _verify_or_403(body, request.headers)

    form = {k: v[0] for k, v in parse_qs(body.decode("utf-8")).items()}
    channel_id = form.get("channel_id", "")
    channel_name = form.get("channel_name", "")
    user_id = form.get("user_id", "")
    command = form.get("command", "")
    text = (form.get("text", "") or "").strip()

    if not channel_allowed(channel_id, channel_name):
        log.info(
            "Slack 指令被拒（非允許頻道）: user=%s channel=%s/#%s cmd=%s",
            user_id, channel_id, channel_name, command,
        )
        return cmd_denied_channel()

    log.info("Slack 指令: user=%s cmd=%s text=%r", user_id, command, text)
    return await dispatch_slash(command, text, trigger=_trigger_pipeline)


@app.post("/slack/interactivity")
async def slack_interactivity_endpoint(request: Request):
    """Slack 互動元件（按鈕、modal）入口。"""
    body = await request.body()
    _verify_or_403(body, request.headers)

    form = {k: v[0] for k, v in parse_qs(body.decode("utf-8")).items()}
    raw_payload = form.get("payload", "")
    if not raw_payload:
        raise HTTPException(status_code=400, detail="Missing payload")

    payload = parse_payload(raw_payload)
    if not payload:
        raise HTTPException(status_code=400, detail="Invalid payload JSON")

    # interactivity 也走頻道白名單
    channel = (payload.get("channel") or {})
    channel_id = channel.get("id", "")
    channel_name = channel.get("name", "")
    if channel_id and not channel_allowed(channel_id, channel_name):
        return cmd_denied_channel()

    return await dispatch_interactivity(payload)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
