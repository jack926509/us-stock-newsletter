"""
Telegram 傳送模組

將過長的 HTML 內容切割（解決原本有潛在無限遞迴崩潰的問題），
並限制速率避免 Telegram 阻擋 API。
"""

import asyncio
from datetime import datetime
import pytz

from app.config import settings, log, TELEGRAM_MAX_LEN
from app.clients import telegram_bot
from app.models import Newsletter
from app.formatter import build_header, build_market_card, build_section_block, build_footer


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


async def _send_html_iterative(text: str) -> None:
    """
    迭代式分段發送發送，消除原本使用遞迴的崩潰風險。
    遇到超長內容，在最接近 TELEGRAM_MAX_LEN 的段落邊界 \n\n 切開。
    """
    chunks = []
    
    while len(text) > TELEGRAM_MAX_LEN:
        cut = text.rfind("\n\n", 0, TELEGRAM_MAX_LEN)
        if cut == -1:
            # 找不到換行，硬切
            cut = TELEGRAM_MAX_LEN
            
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
        
    if text:
        chunks.append(text)

    # 依序發送，並加入 Sleep 避免 Hit Rate Limit (429)
    for i, chunk in enumerate(chunks):
        if chunk.strip():
            await _send_html_chunk(chunk)
            if i < len(chunks) - 1:
                await asyncio.sleep(1.0)  # Delay between messages


async def send_newsletter_to_telegram(newsletter: Newsletter, market_data: dict) -> None:
    """
    將完整的 Newsletter 與市場數據組裝成區塊推播給 Telegram。
    """
    log.info("開始發送到 Telegram 聊天室: %s...", settings.telegram_chat_id)
    
    now = datetime.now(pytz.timezone(settings.timezone))
    
    blocks = [
        build_header(newsletter.subject, now),
        build_market_card(market_data, newsletter.market_summary),
    ]
    
    # 加入各章節
    for i, section in enumerate(newsletter.sections):
        blocks.append(
            build_section_block(i, section.title, section.body, section.sources)
        )
        
    blocks.append(build_footer(newsletter.insights))
    
    for block in blocks:
        await _send_html_iterative(block)
        await asyncio.sleep(0.5)  # 每個大區塊間隔避開 Rate limit
        
    log.info("日報已成功發送至 Telegram")
