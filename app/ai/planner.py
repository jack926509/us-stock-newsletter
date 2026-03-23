"""
AI 規劃模組 (Planning)

負責根據初步新聞擷取主標題和需要深度搜索的主題。
使用 Anthropic messages.parse() 直接輸出 Pydantic 驗證物件，
省去手動 JSON 解析步驟。
"""

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
        resp = await anthropic_client.messages.parse(
            model="claude-haiku-4-5",
            max_tokens=500,
            system="你是擁有10年經驗的華爾街投資策略師。根據新聞判斷市場情緒，輸出日報規劃結果。",
            messages=[{"role": "user", "content": f"最新美股新聞：\n{news_text}"}],
            output_format=NewsletterPlan,
        )
        return resp.parsed_output

    except Exception as e:
        log.error("AI planning failed: %s", e)
        raise AIGenerationError(f"Planning error: {e}") from e
