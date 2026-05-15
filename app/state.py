"""
集中追蹤運行時狀態。

- `pipeline_state`：上次 pipeline 開始/結束時間、成功與否、ticker 數
- `cooldown_state`：手動觸發冷卻計時、背景任務集合
- `scheduler_handle`：APScheduler 實體（由 main.py lifespan 注入）

在主程式生命週期中只是普通的單例，重啟後重置——這對日報這種低頻服務
夠用。要持久化（例如 pause 狀態跨重啟）再升級到外部儲存。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler


@dataclass
class PipelineState:
    """上次 pipeline 執行的快照。0 = 未曾執行。"""

    started_at: float = 0.0
    finished_at: float = 0.0
    success: bool | None = None
    error: str | None = None
    ticker_count: int = 0

    @property
    def duration_seconds(self) -> float:
        if self.finished_at and self.started_at:
            return self.finished_at - self.started_at
        return 0.0

    @property
    def is_running(self) -> bool:
        """started_at 之後還沒 finished_at 就是運行中。"""
        return self.started_at > self.finished_at


@dataclass
class CooldownState:
    """手動觸發冷卻 + 背景 task 集合（防 GC）。"""

    last_manual_trigger: float = 0.0
    background_tasks: set[asyncio.Task] = field(default_factory=set)


@dataclass
class SchedulerHandle:
    """APScheduler 實體（由 main.py 在 lifespan 啟動時注入）+ 主排程 job id。"""

    scheduler: "AsyncIOScheduler | None" = None
    job_id: str = "daily_newsletter"
    auto_resume_job_id: str = "auto_resume_daily"


pipeline_state = PipelineState()
cooldown_state = CooldownState()
scheduler_handle = SchedulerHandle()
