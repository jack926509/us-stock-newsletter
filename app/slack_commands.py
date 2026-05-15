"""
Slack Slash Command 處理（`/newsletter`）

支援的指令：
    help [<command>]               — 列總覽 / 看單一指令詳細
    status                         — 排程、今日進度、上次結果、暫停狀態
    ping                           — 並行探測 OpenAI / Finnhub / Tavily / Slack
    run                            — 觸發日報（共用 300s cooldown）
    pause [<duration>]             — 暫停排程；duration 例 30m / 2h / 1d
    resume                         — 恢復排程
    watchlist                      — 列出
    watchlist add <T...>           — 加（多檔以空格分隔，自動 uppercase / dedupe）
    watchlist remove <T...>        — 移除
    watchlist clear                — 互動式按鈕二次確認後清空

安全性：
- HMAC-SHA256 驗證 X-Slack-Signature + 5 分鐘 timestamp 防 replay
- 頻道白名單比對 SLACK_CHANNEL（ID 或 #name 都支援）
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Awaitable, Callable

from app.config import TRIGGER_COOLDOWN_SECONDS, log, settings
from app.data.watchlist import (
    add_tickers,
    clear_watchlist,
    read_raw_watchlist,
    remove_tickers,
)
from app.health_check import check_all
from app.scheduler_control import (
    is_paused,
    parse_duration,
    pause_for,
    pause_indefinite,
    pending_resume_at,
    resume,
)
from app.slack_blocks import confirm_clear_watchlist
from app.state import cooldown_state, pipeline_state, scheduler_handle

_MAX_TIMESTAMP_SKEW_SECONDS = 60 * 5

# ─── 訊息工具 ───────────────────────────────────────────────


def ephemeral(text: str, blocks: list[dict] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"response_type": "ephemeral", "text": text}
    if blocks:
        payload["blocks"] = blocks
    return payload


def _fmt_ago(ts: float) -> str:
    if ts <= 0:
        return "未曾發生"
    delta = int(time.time() - ts)
    if delta < 60:
        return f"{delta} 秒前"
    if delta < 3600:
        return f"{delta // 60} 分鐘前"
    if delta < 86400:
        return f"{delta // 3600} 小時前"
    return f"{delta // 86400} 天前"


def _fmt_tickers(tickers: list[str]) -> str:
    return "  ".join(f"`{t}`" for t in tickers) if tickers else "（無）"


# ─── 簽章與頻道白名單 ──────────────────────────────────────


def verify_slack_signature(
    *, signing_secret: str, timestamp: str, body: bytes, signature: str
) -> bool:
    """Slack v0 signature 驗證 + 5 分鐘 timestamp 防 replay。"""
    if not signing_secret or not timestamp or not signature:
        return False
    try:
        ts_int = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts_int) > _MAX_TIMESTAMP_SKEW_SECONDS:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    digest = hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"v0={digest}", signature)


def channel_allowed(channel_id: str, channel_name: str) -> bool:
    """SLACK_CHANNEL 是 ID 就比 channel_id；是 #name / name 就比 channel_name。"""
    allowed = (settings.slack_channel or "").strip()
    if not allowed:
        return False
    if (
        allowed.startswith(("C", "G"))
        and len(allowed) >= 9
        and allowed[1:].isalnum()
    ):
        return channel_id == allowed
    return channel_name == allowed.lstrip("#")


# ─── 指令說明（用於 help / help <sub>）────────────────────


COMMAND_HELP: dict[str, dict[str, str]] = {
    "help": {
        "usage": "/newsletter help [<command>]",
        "summary": "顯示指令說明",
        "detail": "不帶參數 = 全部指令總覽；帶參數 = 該指令的詳細用法。",
    },
    "status": {
        "usage": "/newsletter status",
        "summary": "排程、今日進度、上次結果、暫停狀態",
        "detail": (
            "顯示：\n"
            "• 下次排程時刻\n"
            "• 今日 pipeline 是否已跑、成功/失敗、耗時\n"
            "• 排程暫停狀態（含 auto-resume 時刻）\n"
            "• 手動觸發冷卻剩餘秒數\n"
            "• 背景任務數"
        ),
    },
    "ping": {
        "usage": "/newsletter ping",
        "summary": "探測 OpenAI / Finnhub / Tavily / Slack 連線",
        "detail": "並行對四家發輕量請求，5 秒超時，回各自 latency 與錯誤訊息。",
    },
    "run": {
        "usage": "/newsletter run",
        "summary": "立即觸發日報（受 300 秒 cooldown 保護）",
        "detail": (
            "把 pipeline 排入背景並立即回 ack。日報結果會推到 SLACK_CHANNEL。\n"
            "兩次觸發間至少 300 秒，過早觸發會回剩餘冷卻時間。"
        ),
    },
    "pause": {
        "usage": "/newsletter pause [<duration>]",
        "summary": "暫停排程；可選帶時長",
        "detail": (
            "不帶參數 = 暫停到手動 `/newsletter resume`。\n"
            "帶 duration（如 `30m` / `2h` / `1d`）= 暫停指定時間後自動恢復。\n"
            "暫停期間 `/newsletter run` 仍可手動觸發。"
        ),
    },
    "resume": {
        "usage": "/newsletter resume",
        "summary": "恢復排程",
        "detail": "立即恢復排程，並取消任何 pending 的 auto-resume 任務。",
    },
    "watchlist": {
        "usage": "/newsletter watchlist [add|remove|clear] [<TICKER...>]",
        "summary": "自選股管理",
        "detail": (
            "• `/newsletter watchlist` — 列出全部\n"
            "• `/newsletter watchlist add AAPL NVDA` — 加（多檔以空格分隔）\n"
            "• `/newsletter watchlist remove TSLA` — 移除\n"
            "• `/newsletter watchlist clear` — 互動按鈕二次確認後清空\n"
            "\n"
            "格式規則：1-10 字元，限 A-Z 與 `.` `-`；自動 uppercase / dedupe。"
        ),
    },
}


def _help_overview() -> dict[str, Any]:
    lines = ["*美股日報指令總覽*"]
    for name, meta in COMMAND_HELP.items():
        lines.append(f"• `{meta['usage']}` — {meta['summary']}")
    lines.append("\n輸入 `/newsletter help <command>` 看單一指令詳細。")
    return ephemeral("\n".join(lines))


def _help_for(name: str) -> dict[str, Any]:
    meta = COMMAND_HELP.get(name)
    if not meta:
        return ephemeral(f"沒有 `{name}` 這個指令。\n\n" + _help_overview()["text"])
    return ephemeral(
        f"*{name}* — {meta['summary']}\n\n"
        f"用法：`{meta['usage']}`\n\n{meta['detail']}"
    )


# ─── 各 sub-command handler ────────────────────────────────


def cmd_help(args: list[str]) -> dict[str, Any]:
    if not args:
        return _help_overview()
    return _help_for(args[0].lower())


def cmd_status(args: list[str]) -> dict[str, Any]:
    lines = ["*美股日報狀態*"]

    # 下次排程
    sched = scheduler_handle.scheduler
    if sched is None:
        lines.append("• 下次排程：(scheduler 未初始化)")
    else:
        job = sched.get_job(scheduler_handle.job_id)
        next_run = job.next_run_time if job else None
        lines.append(f"• 下次排程：`{next_run}`" if next_run else "• 下次排程：⏸️ 已暫停")

    # 暫停狀態
    if is_paused():
        resume_at = pending_resume_at()
        if resume_at:
            lines.append(f"• 排程狀態：⏸️ 暫停中（`{resume_at}` 自動恢復）")
        else:
            lines.append("• 排程狀態：⏸️ 暫停中（無自動恢復，需 `/newsletter resume`）")
    else:
        lines.append("• 排程狀態：▶️ 運作中")

    # 上次 pipeline 結果
    ps = pipeline_state
    if ps.is_running:
        lines.append(f"• 目前進度：🟡 pipeline 運行中（{int(time.time() - ps.started_at)} 秒）")
    elif ps.success is True:
        lines.append(
            f"• 上次結果：✅ 成功（{_fmt_ago(ps.finished_at)}，"
            f"{ps.ticker_count} 檔，耗時 {ps.duration_seconds:.0f}s）"
        )
    elif ps.success is False:
        err = (ps.error or "")[:120]
        lines.append(f"• 上次結果：❌ 失敗（{_fmt_ago(ps.finished_at)}）— `{err}`")
    else:
        lines.append("• 上次結果：（本次啟動後尚未跑過）")

    # 手動觸發冷卻
    last_trig = cooldown_state.last_manual_trigger
    if last_trig > 0:
        elapsed = int(time.time() - last_trig)
        if elapsed < TRIGGER_COOLDOWN_SECONDS:
            lines.append(
                f"• 手動觸發：{_fmt_ago(last_trig)}（冷卻剩 {TRIGGER_COOLDOWN_SECONDS - elapsed}s）"
            )
        else:
            lines.append(f"• 手動觸發：{_fmt_ago(last_trig)}（可再次觸發）")
    else:
        lines.append("• 手動觸發：未曾觸發")

    # 背景任務
    n = len(cooldown_state.background_tasks)
    lines.append(f"• 背景任務：{'🟡' if n else '🟢'} {n} 個" if n else "• 背景任務：🟢 閒置")

    return ephemeral("\n".join(lines))


async def cmd_ping(args: list[str]) -> dict[str, Any]:
    results = await check_all()
    lines = ["*連線健檢*"]
    for r in results:
        icon = "✅" if r.ok else "❌"
        if r.ok:
            lines.append(f"• {icon} *{r.name}*：{r.latency_ms} ms")
        else:
            lines.append(f"• {icon} *{r.name}*：{r.error or '未知錯誤'}（{r.latency_ms} ms）")
    return ephemeral("\n".join(lines))


def cmd_run(args: list[str], *, trigger: Callable[[], tuple[bool, str, int]]) -> dict[str, Any]:
    ok, msg, _ = trigger()
    icon = "✅" if ok else "⏳"
    return ephemeral(f"{icon} {msg}")


def cmd_pause(args: list[str]) -> dict[str, Any]:
    if not args:
        try:
            pause_indefinite()
        except RuntimeError as e:
            return ephemeral(f"⚠️ {e}")
        return ephemeral("⏸️ 排程已暫停（無限期）。`/newsletter resume` 可恢復。")

    seconds, label = parse_duration(args[0])
    if seconds is None:
        return ephemeral(
            f"❌ 無法解析時長 `{args[0]}`。格式：`30m` / `2h` / `1d`。"
        )
    try:
        resume_at = pause_for(seconds)
    except RuntimeError as e:
        return ephemeral(f"⚠️ {e}")
    return ephemeral(
        f"⏸️ 排程已暫停 *{label}*，將於 `{resume_at}` 自動恢復。"
    )


def cmd_resume(args: list[str]) -> dict[str, Any]:
    if not is_paused():
        return ephemeral("ℹ️ 排程目前並未暫停。")
    try:
        resume()
    except RuntimeError as e:
        return ephemeral(f"⚠️ {e}")
    return ephemeral("▶️ 排程已恢復。")


async def cmd_watchlist(args: list[str]) -> dict[str, Any]:
    sub = args[0].lower() if args else "list"
    rest = args[1:]

    if sub in ("list", ""):
        return await _watchlist_list()
    if sub == "add":
        if not rest:
            return ephemeral("用法：`/newsletter watchlist add <TICKER...>`")
        return await _watchlist_add(rest)
    if sub == "remove":
        if not rest:
            return ephemeral("用法：`/newsletter watchlist remove <TICKER...>`")
        return await _watchlist_remove(rest)
    if sub == "clear":
        current = await read_raw_watchlist()
        if not current:
            return ephemeral("ℹ️ watchlist 已經是空的。")
        return confirm_clear_watchlist(len(current))

    return ephemeral(
        f"未知 watchlist sub-command `{sub}`。\n\n" + _help_for("watchlist")["text"]
    )


async def _watchlist_list() -> dict[str, Any]:
    tickers = await read_raw_watchlist()
    if not tickers:
        return ephemeral("watchlist 是空的（pipeline 會 fallback 到預設清單）。")
    return ephemeral(f"*自選股（{len(tickers)} 檔）*\n{_fmt_tickers(tickers)}")


async def _watchlist_add(raw: list[str]) -> dict[str, Any]:
    r = await add_tickers(raw)
    parts = []
    if r.added:
        parts.append(f"✅ 已加入：{_fmt_tickers(r.added)}")
    if r.skipped_existing:
        parts.append(f"ℹ️ 已存在略過：{_fmt_tickers(r.skipped_existing)}")
    if r.over_cap:
        parts.append(f"🚫 超過上限略過：{_fmt_tickers(r.over_cap)}")
    if r.invalid:
        parts.append(f"⚠️ 格式不正確略過：{'  '.join(f'`{t}`' for t in r.invalid)}")
    if not parts:
        parts.append("（沒有任何變動）")
    parts.append(f"📋 watchlist 現有 {r.final_count} 檔")
    return ephemeral("\n".join(parts))


async def _watchlist_remove(raw: list[str]) -> dict[str, Any]:
    r = await remove_tickers(raw)
    parts = []
    if r.removed:
        parts.append(f"✅ 已移除：{_fmt_tickers(r.removed)}")
    if r.skipped_missing:
        parts.append(f"ℹ️ 不在清單中略過：{_fmt_tickers(r.skipped_missing)}")
    if r.invalid:
        parts.append(f"⚠️ 格式不正確略過：{'  '.join(f'`{t}`' for t in r.invalid)}")
    if not parts:
        parts.append("（沒有任何變動）")
    parts.append(f"📋 watchlist 現有 {r.final_count} 檔")
    return ephemeral("\n".join(parts))


# ─── 共用拒絕回應 ──────────────────────────────────────────


def cmd_denied_channel() -> dict[str, Any]:
    return ephemeral("⛔ 此頻道不允許使用此指令。請在指定頻道操作。")


# ─── Dispatcher ────────────────────────────────────────────


async def dispatch(
    text: str,
    *,
    trigger: Callable[[], tuple[bool, str, int]],
) -> dict[str, Any]:
    """把使用者輸入的 text 解析成 sub-command 並呼叫對應 handler。

    `trigger` 是注入的觸發回呼（避免 commands ↔ main 循環 import）。
    """
    parts = (text or "").strip().split()
    if not parts:
        return cmd_help([])

    name, args = parts[0].lower(), parts[1:]

    if name == "help":
        return cmd_help(args)
    if name == "status":
        return cmd_status(args)
    if name == "ping":
        return await cmd_ping(args)
    if name == "run":
        return cmd_run(args, trigger=trigger)
    if name == "pause":
        return cmd_pause(args)
    if name == "resume":
        return cmd_resume(args)
    if name == "watchlist":
        return await cmd_watchlist(args)

    return ephemeral(f"未知指令 `{name}`。\n\n" + _help_overview()["text"])
