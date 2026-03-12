"""
Telegram 傳送模組

智能合併區塊以減少訊息數量（6 則 → 2-3 則），
降低通知轟炸，提升閱讀連貫性。
"""

import asyncio
from datetime import datetime
import pytz

from app.config import settings, log, TELEGRAM_MAX_LEN
from app.clients import telegram_bot
from app.models import Newsletter
from app.formatter import build_header, build_market_card, build_section_block, build_footer


def _merge_blocks(blocks: list[str], max_len: int) -> list[str]:
    """
    將相鄰區塊盡可能合併為單則訊息，減少 Telegram 推播數量。

    策略：依序嘗試把下一個 block 接到當前訊息，
    超過 max_len 才切到新訊息。
    """
    messages: list[str] = []
    current = ""

    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= max_len:
            current = candidate
        else:
            if current:
                messages.append(current)
            current = block

    if current:
        messages.append(current)

    return messages


async def _send_html_chunk(text: str) -> None:
    """傳送單一 HTML 文字，不分段。"""
    try:
        await telegram_bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.error("Failed to send Telegram chunk: %s", e)
        raise


async def _send_html_safe(text: str) -> None:
    """
    安全發送：若單則訊息仍超過上限，在段落邊界切割後依序發送。
    """
    if len(text) <= TELEGRAM_MAX_LEN:
        await _send_html_chunk(text)
        return

    # 超長訊息：在 \n\n 邊界迭代切割
    chunks: list[str] = []
    while len(text) > TELEGRAM_MAX_LEN:
        cut = text.rfind("\n\n", 0, TELEGRAM_MAX_LEN)
        if cut == -1:
            cut = TELEGRAM_MAX_LEN
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)

    for i, chunk in enumerate(chunks):
        if chunk.strip():
            await _send_html_chunk(chunk)
            if i < len(chunks) - 1:
                await asyncio.sleep(1.0)


async def send_newsletter_to_telegram(newsletter: Newsletter, market_data: dict) -> None:
    """
    將完整的 Newsletter 組裝成區塊，智能合併後推播到 Telegram。
    """
    log.info("開始發送到 Telegram 聊天室: %s...", settings.telegram_chat_id)

    now = datetime.now(pytz.timezone(settings.timezone))
    total = len(newsletter.sections)

    # 組裝所有區塊
    blocks = [
        build_header(newsletter.subject, now),
        build_market_card(market_data, newsletter.market_summary),
    ]

    for i, section in enumerate(newsletter.sections):
        blocks.append(
            build_section_block(i, total, section.title, section.body, section.sources)
        )

    blocks.append(build_footer(newsletter.insights))

    # 智能合併：將相鄰區塊合併為盡可能少的訊息
    messages = _merge_blocks(blocks, TELEGRAM_MAX_LEN)
    log.info("區塊合併完成：%d 個區塊 → %d 則訊息", len(blocks), len(messages))

    for i, msg in enumerate(messages):
        await _send_html_safe(msg)
        if i < len(messages) - 1:
            await asyncio.sleep(0.5)

    log.info("日報已成功發送至 Telegram")
