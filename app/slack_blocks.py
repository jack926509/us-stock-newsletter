"""
Slack Block Kit 訊息片段組裝（互動按鈕專用）。

各 builder 回傳 ephemeral 訊息 dict（含 `blocks` 與 fallback `text`）。
互動按鈕的 `action_id` 與 `value` 由 `app.slack_interactivity` dispatch 處理。
"""

from __future__ import annotations

from typing import Any

# 每個 destructive 操作各自一組 action_id 命名空間
ACTION_WL_CLEAR_CONFIRM = "wl_clear_confirm"
ACTION_WL_CLEAR_CANCEL = "wl_clear_cancel"


def _ephemeral(text: str, blocks: list[dict] | None = None, *, replace_original: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"response_type": "ephemeral", "text": text}
    if blocks:
        payload["blocks"] = blocks
    if replace_original:
        payload["replace_original"] = True
    return payload


def confirm_clear_watchlist(current_count: int) -> dict[str, Any]:
    """`/newsletter watchlist clear` 互動式二次確認。"""
    text = f"⚠️ 確定要清空 watchlist（目前 {current_count} 檔）嗎？此操作不可復原。"
    return _ephemeral(
        text=text,
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"⚠️ *確定要清空 watchlist 嗎？*\n"
                        f"目前共有 *{current_count}* 檔，清空後 pipeline 會 fallback 到預設清單。\n"
                        f"此操作不可復原。"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "取消", "emoji": True},
                        "action_id": ACTION_WL_CLEAR_CANCEL,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "🗑 確定清空", "emoji": True},
                        "style": "danger",
                        "action_id": ACTION_WL_CLEAR_CONFIRM,
                        "confirm": {
                            "title": {"type": "plain_text", "text": "再次確認"},
                            "text": {"type": "mrkdwn", "text": "真的要清空整個 watchlist 嗎？"},
                            "confirm": {"type": "plain_text", "text": "是，清空"},
                            "deny": {"type": "plain_text", "text": "不要"},
                        },
                    },
                ],
            },
        ],
    )


def replaced_message(text: str) -> dict[str, Any]:
    """互動按鈕回應：替換掉原本的確認訊息。"""
    return _ephemeral(text=text, replace_original=True)
