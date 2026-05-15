"""
Slack Slash Command 處理

提供 `/newsletter help / status / watchlist / run` 四個子命令。
所有回應都是 ephemeral（只發起人看得到），實際日報結果仍由
pipeline 推到 SLACK_CHANNEL。

安全性：
- HMAC-SHA256 驗證 X-Slack-Signature + 5 分鐘 timestamp 防 replay
- 頻道白名單比對 SLACK_CHANNEL（ID 或 #name 都支援）
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from app.config import log, settings
from app.data.watchlist import load_watchlist

COMMAND_USAGE = (
    "*美股日報指令*\n"
    "• `/newsletter run` — 立即觸發今日日報（受 300 秒冷卻保護）\n"
    "• `/newsletter status` — 查看下次排程、運作狀態\n"
    "• `/newsletter watchlist` — 顯示目前自選股清單\n"
    "• `/newsletter help` — 顯示這份說明"
)

_MAX_TIMESTAMP_SKEW_SECONDS = 60 * 5


def verify_slack_signature(
    *,
    signing_secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
) -> bool:
    """Slack v0 signature 驗證 + 5 分鐘 timestamp 防 replay。

    參考：https://api.slack.com/authentication/verifying-requests-from-slack
    """
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
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, signature)


def channel_allowed(channel_id: str, channel_name: str) -> bool:
    """白名單比對：SLACK_CHANNEL 是 ID 就比 channel_id，是 #name 或 name 就比 channel_name。"""
    allowed = (settings.slack_channel or "").strip()
    if not allowed:
        return False
    if allowed.startswith(("C", "G")) and len(allowed) >= 9 and allowed[1:].isalnum():
        # 看起來像 channel ID（C... 公開、G... private）
        return channel_id == allowed
    # 否則視為名稱
    return channel_name == allowed.lstrip("#")


def _ephemeral(text: str, *, blocks: list[dict] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"response_type": "ephemeral", "text": text}
    if blocks:
        payload["blocks"] = blocks
    return payload


def cmd_help() -> dict[str, Any]:
    return _ephemeral(COMMAND_USAGE)


def cmd_watchlist() -> dict[str, Any]:
    try:
        tickers = load_watchlist()
    except Exception as e:  # noqa: BLE001
        log.warning("watchlist 讀取失敗: %s", e)
        return _ephemeral(f"⚠️ watchlist 讀取失敗：`{e}`")
    if not tickers:
        return _ephemeral("watchlist 是空的。")
    body = "  ".join(f"`{t}`" for t in tickers)
    return _ephemeral(f"*自選股（{len(tickers)} 檔）*\n{body}")


def cmd_status(
    *,
    next_run: str | None,
    last_trigger_ts: float,
    bg_task_count: int,
    cooldown_seconds: int,
) -> dict[str, Any]:
    lines = ["*美股日報狀態*"]
    lines.append(f"• 下次排程：`{next_run}`" if next_run else "• 下次排程：(無)")

    now = time.time()
    if last_trigger_ts > 0:
        elapsed = int(now - last_trigger_ts)
        if elapsed < cooldown_seconds:
            remaining = cooldown_seconds - elapsed
            lines.append(f"• 上次手動觸發：{elapsed} 秒前（冷卻中，剩 {remaining}s）")
        else:
            lines.append(f"• 上次手動觸發：{elapsed} 秒前")
    else:
        lines.append("• 上次手動觸發：未曾觸發")

    if bg_task_count:
        lines.append(f"• 背景任務：🟡 {bg_task_count} 個 pipeline 正在跑")
    else:
        lines.append("• 背景任務：🟢 閒置")

    return _ephemeral("\n".join(lines))


def cmd_unknown(sub: str) -> dict[str, Any]:
    return _ephemeral(f"未知指令 `{sub}`。\n\n{COMMAND_USAGE}")


def cmd_denied_channel() -> dict[str, Any]:
    return _ephemeral("⛔ 此頻道不允許使用此指令。請在指定頻道操作。")
