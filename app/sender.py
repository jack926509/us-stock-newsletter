"""
Telegram 傳送模組

智能合併區塊以減少訊息數量（6 則 → 2-3 則），
降低通知轟炸，提升閱讀連貫性。
"""

import asyncio
from datetime import datetime
import pytz

from telegram.error import NetworkError, RetryAfter, TimedOut

from app.config import settings, log, TELEGRAM_MAX_LEN
from app.clients import get_telegram_bot
from app.models import Newsletter
from app.formatter import (
    build_footer,
    build_header,
    build_market_card,
    build_section_block,
    build_verdicts_card,
)


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


_TELEGRAM_SEND_RETRIES = 4
_TELEGRAM_RETRY_BASE_DELAY = 2.0


async def _send_html_chunk(text: str) -> None:
    """傳送單一 HTML 文字。對網路類錯誤做指數退避重試，避免單次 TimedOut 就丟掉整份日報。"""
    last_exc: Exception | None = None
    for attempt in range(1, _TELEGRAM_SEND_RETRIES + 1):
        try:
            await get_telegram_bot().send_message(
                chat_id=settings.telegram_chat_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        except RetryAfter as e:
            wait = float(getattr(e, "retry_after", 5)) + 1.0
            log.warning(
                "Telegram RetryAfter (attempt %d/%d): 等待 %.1fs",
                attempt, _TELEGRAM_SEND_RETRIES, wait,
            )
            await asyncio.sleep(wait)
            last_exc = e
        except (TimedOut, NetworkError) as e:
            wait = _TELEGRAM_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            log.warning(
                "Telegram 網路錯誤 %s (attempt %d/%d): %.1fs 後重試",
                type(e).__name__, attempt, _TELEGRAM_SEND_RETRIES, wait,
            )
            await asyncio.sleep(wait)
            last_exc = e
        except Exception as e:
            log.error("Failed to send Telegram chunk: %s", e)
            raise

    log.error(
        "Telegram chunk 送出失敗，已用盡 %d 次重試: %s",
        _TELEGRAM_SEND_RETRIES, last_exc,
    )
    assert last_exc is not None
    raise last_exc


def _find_safe_cut(text: str, max_len: int) -> int:
    """選擇切割位置，依序偏好：段落 → 行 → 標籤閉合後 → 硬切。

    回傳要切到的索引。最後一層 fallback 仍可能切到標籤內部，但實務上
    會先命中前三層之一（newsletter 區塊都包含 \n\n 與 </tag>）。
    """
    cut = text.rfind("\n\n", 0, max_len)
    if cut != -1:
        return cut
    cut = text.rfind("\n", 0, max_len)
    if cut != -1:
        return cut
    # 找最右邊的 `> `（標籤閉合後接空白），確保不切在標籤中間
    cut = text.rfind("> ", 0, max_len)
    if cut != -1:
        return cut + 1  # 在 `>` 之後切
    # 退一步找任何 `>`（標籤閉合）
    cut = text.rfind(">", 0, max_len)
    if cut != -1:
        return cut + 1
    return max_len


async def _send_html_safe(text: str) -> None:
    """
    安全發送：若單則訊息仍超過上限，盡量在 HTML 安全邊界切割後依序發送。
    """
    if len(text) <= TELEGRAM_MAX_LEN:
        await _send_html_chunk(text)
        return

    chunks: list[str] = []
    while len(text) > TELEGRAM_MAX_LEN:
        cut = _find_safe_cut(text, TELEGRAM_MAX_LEN)
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

    # 在章節之後、footer 之前插入 AI 多分析師個股共識卡片（若有）
    verdicts_block = build_verdicts_card(newsletter.verdicts)
    if verdicts_block:
        blocks.append(verdicts_block)

    blocks.append(build_footer(newsletter.insights))

    # 智能合併：將相鄰區塊合併為盡可能少的訊息
    messages = _merge_blocks(blocks, TELEGRAM_MAX_LEN)
    log.info("區塊合併完成：%d 個區塊 → %d 則訊息", len(blocks), len(messages))

    for i, msg in enumerate(messages):
        await _send_html_safe(msg)
        if i < len(messages) - 1:
            await asyncio.sleep(0.5)

    log.info("日報已成功發送至 Telegram")
