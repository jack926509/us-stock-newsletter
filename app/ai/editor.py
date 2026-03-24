"""
AI 編輯模組 (Editor)

將生成的章節與市場快照組合成結構化輸出。
使用 Anthropic messages.create() + JSON 解析 + Pydantic 強型別驗證。
"""

import json
from datetime import datetime
import pytz
from tenacity import retry, stop_after_attempt, wait_exponential

from app.clients import anthropic_client
from app.config import settings, log
from app.models import Newsletter
from app.ai.planner import AIGenerationError


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=3, max=15))
async def edit_newsletter(title: str, sections: list, market: dict) -> Newsletter:
    """整合章節內容並輸出符合 Newsletter Pydantic model 的結構"""
    today = datetime.now(pytz.timezone(settings.timezone)).strftime("%Y-%m-%d")
    sections_text = "\n\n\n".join(sections)

    market_snapshot = " | ".join([
        f"{v['name']}: {v['price']:.2f} ({v['change']:+.2f}%)"
        for v in market.values()
    ])

    try:
        resp = await anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2500,
            system=(
                f"你是Bloomberg/WSJ風格的主編。整合分析報告為結構化輸出（繁體中文）。\n"
                f"今日日期: {today}\n"
                f"大盤數據: {market_snapshot}\n\n"
                "規則：body/insights 只輸出純文字，不含任何 HTML；"
                "sections 數量恰好符合傳入的章節數；每個 sources 最多 3 筆；"
                "股票代碼用【TICKER】包住；subject 限 15 字內。\n\n"
                "必須以 JSON 格式回覆，格式如下：\n"
                '{"subject": "主旨", "market_summary": "大盤摘要", '
                '"sections": [{"title": "...", "body": "...", "sources": [{"title": "...", "url": "..."}]}], '
                '"insights": "投資啟示"}\n'
                "不輸出任何其他文字。"
            ),
            messages=[{"role": "user", "content": f"主標題: {title}\n\n所有章節內容:\n{sections_text}"}],
        )
        raw = resp.content[0].text.strip()
        # 移除可能的 markdown code block
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        return Newsletter(**data)

    except Exception as e:
        log.error("Failed to edit newsletter: %s", e)
        raise AIGenerationError(f"Editor error: {e}") from e
