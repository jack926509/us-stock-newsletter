"""
Slack interactive components 處理（按鈕點擊）。

Slack POST `application/x-www-form-urlencoded`，body 為單一欄位 `payload=<json>`。
這裡解析 payload，依 `actions[0].action_id` 分派。

回傳 `replace_original: true` 的 ephemeral，把原本的「確認對話」訊息替換成結果。
"""

from __future__ import annotations

import json
from typing import Any

from app.config import log
from app.data.watchlist import clear_watchlist, read_raw_watchlist
from app.slack_blocks import (
    ACTION_WL_CLEAR_CANCEL,
    ACTION_WL_CLEAR_CONFIRM,
    replaced_message,
)


def parse_payload(form_payload: str) -> dict[str, Any]:
    """解析 Slack 互動 payload。失敗時回 {}（呼叫方應該處理）。"""
    try:
        return json.loads(form_payload)
    except (TypeError, ValueError) as e:
        log.warning("interactivity payload 解析失敗: %s", e)
        return {}


async def dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    """根據 payload 執行對應動作。"""
    actions = payload.get("actions") or []
    if not actions:
        return replaced_message("⚠️ 收到空的 actions payload。")

    action_id = actions[0].get("action_id", "")
    user_id = (payload.get("user") or {}).get("id", "")
    log.info("Slack interactivity: action=%s user=%s", action_id, user_id)

    if action_id == ACTION_WL_CLEAR_CANCEL:
        return replaced_message("✅ 已取消，watchlist 沒有改動。")

    if action_id == ACTION_WL_CLEAR_CONFIRM:
        before = await read_raw_watchlist()
        if not before:
            return replaced_message("ℹ️ watchlist 已經是空的，未做任何改動。")
        result = await clear_watchlist()
        return replaced_message(
            f"🗑 watchlist 已清空（移除 {len(result.removed)} 檔）。"
            f"pipeline 會 fallback 到預設清單，直到再次 add。"
        )

    return replaced_message(f"⚠️ 未知 action_id：`{action_id}`")
