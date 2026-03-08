"""
核心流程模組

串接所有的模組（抓數據 -> 規劃 -> 搜尋 -> 寫作 -> 編輯 -> 推送），
提供單一對外接口 `run_newsletter_pipeline` 讓主程式呼叫。
"""

import asyncio

from app.config import log, settings
from app.clients import telegram_bot
from app.data.finnhub import get_market_data, get_finnhub_news
from app.data.tavily import tavily_search
from app.ai.planner import plan_newsletter
from app.ai.writer import write_section
from app.ai.editor import edit_newsletter
from app.sender import send_newsletter_to_telegram


async def run_newsletter_pipeline() -> None:
    """執行美股日報自動化完整流程"""
    log.info("🚀 開始生成美股日報流程...")
    
    try:
        # 1. 取得市場大盤與突發新聞
        market_data, finnhub_news = await asyncio.gather(
            get_market_data(),
            get_finnhub_news(category="general", count=5),
        )
        
        # 2. AI 規劃主題
        log.info("初步市場數據與新聞就緒，讓 AI 進行規劃...")
        plan = await plan_newsletter(finnhub_news)
        log.info("AI 規劃主旨: %s (焦點: %s)", plan.title, plan.topics)
        
        # 3. 對每個主題進行 Tavily 深度搜索
        log.info("正針對 %d 個焦點使用 Tavily 深層搜索資料...", len(plan.topics))
        research_tasks = [
            tavily_search(topic, max_results=3, time_range="month")
            for topic in plan.topics
        ]
        research_results = await asyncio.gather(*research_tasks, return_exceptions=True)
        
        valid_topics = []
        valid_researches = []
        for i, res in enumerate(research_results):
            topic = plan.topics[i]
            if isinstance(res, Exception):
                log.warning("主題 %s 搜尋失敗，將被略過: %s", topic, res)
                continue
            valid_topics.append(topic)
            valid_researches.append(res)
            
        if not valid_topics:
            raise RuntimeError("所有的主題搜尋都失敗，無法進行下一步。")
            
        # 4. 對每個有效主題撰寫分析章節 (設定並發限制避免 OpenAI 429 Error)
        log.info("Tavily 搜索完成，開始並行撰寫各主題分析章節...")
        sem = asyncio.Semaphore(3)  # 最多同時發 3 個 OpenAI 請求

        async def _bounded_write(*args):
            async with sem:
                return await write_section(*args)

        writer_tasks = [
            _bounded_write(valid_topics[i], valid_researches[i])
            for i in range(len(valid_topics))
        ]
        sections = list(await asyncio.gather(*writer_tasks))
        
        # 5. 最終 AI 編輯合成 JSON 電子報格式
        log.info("所有章節草稿已就緒，交由 AI 主編排版合成最終報表...")
        newsletter = await edit_newsletter(plan.title, sections, market_data)
        
        # 6. 透過 Telegram 推播
        log.info("排版完成，開始推送 Telegram 頻道...")
        await send_newsletter_to_telegram(newsletter, market_data)
        log.info("✅ 流程結束，美股日報發送成功")
        
    except Exception as e:
        log.exception("❌ 美股日報生成流程發生未預期嚴重失敗: %s", e)
        try:
            # 報錯推送到群組
            await telegram_bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=f"⚠️ <b>美股新聞編輯室發生異常</b>\n\n系統生成日報過程中遭遇失敗，請檢查 Zeabur Log。\n<code>{str(e)}</code>",
                parse_mode="HTML"
            )
        except Exception as notify_e:
            log.error("Telegram 錯誤通知發送失敗，無法推播告警: %s", notify_e)
