"""
Slack Slash Command 處理（6 個 top-level 指令）

支援的指令：
    /status                         — 排程、今日進度、上次結果、暫停狀態
    /ping                           — 並行探測 OpenAI / Finnhub / Tavily / Slack（+ DB）
    /run                            — 觸發日報（共用 300s cooldown）
    /pause [<duration>]             — 暫停排程；duration 例 30m / 2h / 1d
    /resume                         — 恢復排程
    /watchlist                      — 列出自選股
    /watchlist add <T...>           — 加（多檔以空格分隔，自動 uppercase / dedupe）
    /watchlist remove <T...>        — 移除
    /watchlist clear                — 互動按鈕二次確認後清空

所有指令共用同一個 Request URL `/slack/command`，後端用 payload 的 `command`
欄位分派。Slack App 要為每個 top-level 指令各別註冊一條 slash command。

安全性：
- HMAC-SHA256 驗證 X-Slack-Signature + 5 分鐘 timestamp 防 replay
- 頻道白名單比對 SLACK_CHANNEL（ID 或 #name 都支援）
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Callable

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


WATCHLIST_USAGE = (
    "*watchlist 用法*\n"
    "• `/watchlist` — 列出全部\n"
    "• `/watchlist add <TICKER...>` — 加（多檔以空格分隔）\n"
    "• `/watchlist remove <TICKER...>` — 移除\n"
    "• `/watchlist clear` — 互動按鈕二次確認後清空\n"
    "\n格式規則：1-10 字元，限 A-Z 與 `.` `-`；自動 uppercase / dedupe。"
)


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


# ─── 各 sub-command handler ────────────────────────────────


def cmd_status(args: list[str]) -> dict[str, Any]:
    lines = ["*美股日報狀態*"]

    sched = scheduler_handle.scheduler
    if sched is None:
        lines.append("• 下次排程：(scheduler 未初始化)")
    else:
        job = sched.get_job(scheduler_handle.job_id)
        next_run = job.next_run_time if job else None
        lines.append(f"• 下次排程：`{next_run}`" if next_run else "• 下次排程：⏸️ 已暫停")

    if is_paused():
        resume_at = pending_resume_at()
        if resume_at:
            lines.append(f"• 排程狀態：⏸️ 暫停中（`{resume_at}` 自動恢復）")
        else:
            lines.append("• 排程狀態：⏸️ 暫停中（無自動恢復，需 `/resume`）")
    else:
        lines.append("• 排程狀態：▶️ 運作中")

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
        return ephemeral("⏸️ 排程已暫停（無限期）。`/resume` 可恢復。")

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
            return ephemeral("用法：`/watchlist add <TICKER...>`")
        return await _watchlist_add(rest)
    if sub == "remove":
        if not rest:
            return ephemeral("用法：`/watchlist remove <TICKER...>`")
        return await _watchlist_remove(rest)
    if sub == "clear":
        current = await read_raw_watchlist()
        if not current:
            return ephemeral("ℹ️ watchlist 已經是空的。")
        return confirm_clear_watchlist(len(current))

    return ephemeral(f"未知 watchlist sub-command `{sub}`。\n\n{WATCHLIST_USAGE}")


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


# 已註冊的 top-level command 集合（不含前置 /）
_KNOWN_COMMANDS = {"status", "ping", "run", "pause", "resume", "watchlist"}


async def dispatch(
    command: str,
    text: str,
    *,
    trigger: Callable[[], tuple[bool, str, int]],
) -> dict[str, Any]:
    """依 Slack payload 的 `command`（如 `/status`）與 `text` 分派到對應 handler。

    `trigger` 是注入的觸發回呼（避免 commands ↔ main 循環 import）。
    """
    name = (command or "").lstrip("/").lower()
    args = (text or "").strip().split()

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

    log.warning("Slack 收到未註冊指令 /%s", name)
    return ephemeral(
        f"未知指令 `/{name}`。已註冊：{', '.join(f'`/{c}`' for c in sorted(_KNOWN_COMMANDS))}"
    )
