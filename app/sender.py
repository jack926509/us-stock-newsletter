"""
Slack 傳送模組

策略：
1. 主訊息（broadcast）：header + market snapshot → 出現在頻道列表
2. Thread 回覆：每個焦點章節、AI 個股共識、footer 各自一則 → 不再轟炸頻道

如此每天只會在頻道顯示一則「日報主貼文」，細節都收在 thread 內，符合 Slack 慣例。
"""

import asyncio
from datetime import datetime

import pytz
from slack_sdk.errors import SlackApiError

from app.clients import get_slack_client
from app.config import SLACK_BLOCKS_PER_MSG_MAX, log, settings
from app.formatter import (
    build_footer_blocks,
    build_header_blocks,
    build_market_blocks,
    build_section_blocks,
    build_verdicts_blocks,
)
from app.models import Newsletter

_SEND_RETRIES = 4
_RETRY_BASE_DELAY = 2.0


def _chunk_blocks(blocks: list[dict], limit: int = SLACK_BLOCKS_PER_MSG_MAX) -> list[list[dict]]:
    """單則 Slack 訊息最多 50 個 block，超過就切。"""
    if len(blocks) <= limit:
        return [blocks]
    return [blocks[i : i + limit] for i in range(0, len(blocks), limit)]


async def _post_message(
    blocks: list[dict],
    *,
    fallback_text: str,
    thread_ts: str | None = None,
) -> str | None:
    """呼叫 chat.postMessage，對網路/限流錯誤做指數退避重試。

    回傳訊息 ts（用來把後續區塊以 thread 連起來）；失敗時拋例外。
    """
    if not blocks:
        return None

    client = get_slack_client()
    last_exc: Exception | None = None

    for attempt in range(1, _SEND_RETRIES + 1):
        try:
            resp = await client.chat_postMessage(
                channel=settings.slack_channel,
                text=fallback_text,  # 通知 / 螢幕閱讀器後援文字
                blocks=blocks,
                thread_ts=thread_ts,
                unfurl_links=False,
                unfurl_media=False,
            )
            return resp.get("ts")
        except SlackApiError as e:
            # Slack rate limit：response 帶 Retry-After header
            resp = getattr(e, "response", None)
            status = getattr(resp, "status_code", None) if resp else None
            if status == 429:
                retry_after = float(resp.headers.get("Retry-After", "5")) if resp else 5.0
                log.warning(
                    "Slack 429 rate limited (attempt %d/%d): 等待 %.1fs",
                    attempt, _SEND_RETRIES, retry_after,
                )
                await asyncio.sleep(retry_after + 0.5)
                last_exc = e
                continue
            log.error("Slack API 錯誤: %s", e)
            raise
        except Exception as e:  # noqa: BLE001 — 統一網路類錯誤的退避
            wait = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
            log.warning(
                "Slack 傳送失敗 %s (attempt %d/%d): %.1fs 後重試 — %s",
                type(e).__name__, attempt, _SEND_RETRIES, wait, e,
            )
            await asyncio.sleep(wait)
            last_exc = e

    log.error("Slack 訊息送出失敗，已用盡 %d 次重試: %s", _SEND_RETRIES, last_exc)
    assert last_exc is not None
    raise last_exc


async def _post_in_thread(
    blocks: list[dict],
    *,
    fallback_text: str,
    thread_ts: str,
) -> None:
    """把一組 blocks 以 thread reply 形式發出，必要時依 50-block 上限切多則。"""
    if not blocks:
        return
    for chunk in _chunk_blocks(blocks):
        await _post_message(chunk, fallback_text=fallback_text, thread_ts=thread_ts)
        # Slack tier 3 限制約 1 msg/sec/channel，保守 sleep
        await asyncio.sleep(0.4)


async def send_newsletter_to_slack(newsletter: Newsletter, market_data: dict) -> None:
    """把完整 Newsletter 推到 Slack：主貼文 + thread 細節。"""
    log.info("開始發送到 Slack 頻道: %s", settings.slack_channel)

    now = datetime.now(pytz.timezone(settings.timezone))
    weekday = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]
    date_str = now.strftime(f"%Y/%m/%d（週{weekday}）")
    fallback = f"📰 美股日報 {date_str} — {newsletter.subject}"

    # 主訊息：日期主旨 + 大盤快照
    main_blocks = build_header_blocks(newsletter.subject, now) + build_market_blocks(
        market_data, newsletter.market_summary
    )
    thread_ts = await _post_message(main_blocks, fallback_text=fallback)
    if not thread_ts:
        raise RuntimeError("Slack 主訊息送出後沒有拿到 ts，無法繼續 thread。")

    # Thread：每個焦點章節獨立一則
    total = len(newsletter.sections)
    for i, section in enumerate(newsletter.sections):
        section_blocks = build_section_blocks(
            i, total, section.title, section.body, section.sources
        )
        await _post_in_thread(
            section_blocks,
            fallback_text=f"[{i + 1}/{total}] {section.title}",
            thread_ts=thread_ts,
        )

    # Thread：AI 多分析師個股共識（可能為空）
    verdicts_blocks = build_verdicts_blocks(newsletter.verdicts)
    if verdicts_blocks:
        await _post_in_thread(
            verdicts_blocks,
            fallback_text="AI 分析師個股共識",
            thread_ts=thread_ts,
        )

    # Thread：投資啟示 + 免責
    await _post_in_thread(
        build_footer_blocks(newsletter.insights),
        fallback_text="投資啟示與風險提醒",
        thread_ts=thread_ts,
    )

    log.info("日報已成功發送至 Slack（thread_ts=%s）", thread_ts)
