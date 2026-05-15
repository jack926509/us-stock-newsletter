"""
Scheduler 控制：暫停 / 恢復主排程。

`pause_for(seconds)` 會額外註冊一個一次性的 auto-resume job，
時間到自動把主排程喚醒。狀態存活週期 = 程式運行週期；重啟後排程恢復運作。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytz
from apscheduler.triggers.date import DateTrigger

from app.config import log, settings
from app.state import scheduler_handle

_DURATION_RE = re.compile(r"^(\d+)\s*([mhd])$", re.IGNORECASE)
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}
_UNIT_LABEL = {"m": "分鐘", "h": "小時", "d": "天"}


def parse_duration(text: str) -> tuple[int | None, str | None]:
    """`"30m"` → (1800, "30 分鐘")；不合法回 (None, None)。

    支援單位：`m` 分鐘、`h` 小時、`d` 天。
    """
    if not text:
        return None, None
    m = _DURATION_RE.match(text.strip())
    if not m:
        return None, None
    n = int(m.group(1))
    unit = m.group(2).lower()
    if n <= 0:
        return None, None
    return n * _UNIT_SECONDS[unit], f"{n} {_UNIT_LABEL[unit]}"


def _require_scheduler():
    sched = scheduler_handle.scheduler
    if sched is None:
        raise RuntimeError("Scheduler 尚未初始化（main lifespan 未啟動）")
    return sched


def is_paused() -> bool:
    """主排程 job 處於 paused（next_run_time 為 None）即視為暫停。"""
    sched = scheduler_handle.scheduler
    if sched is None:
        return False
    job = sched.get_job(scheduler_handle.job_id)
    return bool(job and job.next_run_time is None)


def pending_resume_at() -> datetime | None:
    """有掛 auto-resume job 的話回傳排程時間，否則 None。"""
    sched = scheduler_handle.scheduler
    if sched is None:
        return None
    job = sched.get_job(scheduler_handle.auto_resume_job_id)
    return job.next_run_time if job else None


def pause_indefinite() -> None:
    sched = _require_scheduler()
    sched.pause_job(scheduler_handle.job_id)
    # 取消任何先前的 auto-resume
    if sched.get_job(scheduler_handle.auto_resume_job_id):
        sched.remove_job(scheduler_handle.auto_resume_job_id)
    log.info("⏸️ 主排程已暫停（無限期）")


def pause_for(seconds: int) -> datetime:
    """暫停 + 排定 seconds 後自動恢復。回傳恢復時刻。"""
    sched = _require_scheduler()
    sched.pause_job(scheduler_handle.job_id)
    tz = pytz.timezone(settings.timezone)
    resume_at = datetime.now(tz=tz) + timedelta(seconds=seconds)
    sched.add_job(
        _resume_callable,
        DateTrigger(run_date=resume_at),
        id=scheduler_handle.auto_resume_job_id,
        replace_existing=True,
        name="Auto-resume daily newsletter",
    )
    log.info("⏸️ 主排程已暫停 → 將於 %s 自動恢復", resume_at.isoformat())
    return resume_at


def resume() -> None:
    sched = _require_scheduler()
    sched.resume_job(scheduler_handle.job_id)
    if sched.get_job(scheduler_handle.auto_resume_job_id):
        sched.remove_job(scheduler_handle.auto_resume_job_id)
    log.info("▶️ 主排程已恢復")


def _resume_callable() -> None:
    """auto-resume job 的目標函式（必須是 module-level，APScheduler 才能 pickle）。"""
    sched = scheduler_handle.scheduler
    if sched is None:
        return
    sched.resume_job(scheduler_handle.job_id)
    log.info("⏰ Auto-resume 觸發，主排程已恢復")
