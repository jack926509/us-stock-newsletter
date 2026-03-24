"""
AI 規劃模組 (Planning)

負責根據初步新聞擷取主標題和需要深度搜索的主題。
使用 Anthropic messages.create() + JSON 解析輸出 Pydantic 驗證物件。
"""

import json

from tenacity import retry, stop_after_attempt, wait_exponential

from app.clients import anthropic_client
from app.config import log
from app.models import NewsletterPlan


class AIGenerationError(Exception):
    pass


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def plan_newsletter(news_items: list) -> NewsletterPlan:
    """根據新聞清單，產生日報規劃 (主標題 + 搜尋主題)"""
    news_text = "\n\n".join([
        f"標題: {n.get('headline', n.get('title', ''))}\n摘要: {n.get('summary', n.get('content', ''))[:300]}"
        for n in news_items
    ])

    try:
        resp = await anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=(
                "你是擁有10年經驗的華爾街投資策略師。根據新聞判斷市場情緒，輸出日報規劃結果。\n"
                "必須以 JSON 格式回覆，格式如下：\n"
                '{"title": "日報主標題", "topics": ["主題1", "主題2", "主題3"]}\n'
                "topics 最多 5 個，不輸出任何其他文字。"
            ),
            messages=[{"role": "user", "content": f"最新美股新聞：\n{news_text}"}],
        )
        raw = resp.content[0].text.strip()
        # 移除可能的 markdown code block
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        return NewsletterPlan(**data)

    except Exception as e:
        log.error("AI planning failed: %s", e)
        raise AIGenerationError(f"Planning error: {e}") from e
