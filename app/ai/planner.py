"""
AI 規劃模組 (Planning)

負責根據初步新聞擷取主標題和需要深度搜索的主題。
利用 Pydantic 進行 Schema 驗證。
"""

from tenacity import retry, stop_after_attempt, wait_exponential
from pydantic import ValidationError

from app.clients import openai_client
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
        resp = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=500,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": '你是擁有10年經驗的華爾街投資策略師。根據新聞判斷市場情緒，輸出 JSON：\n{"title": "日報主標題（含關鍵股票或事件）", "topics": ["主題1(3-6字)", "主題2(3-6字)", "主題3(3-6字)"]}',
                },
                {"role": "user", "content": f"最新美股新聞：\n{news_text}"},
            ],
            timeout=30.0,
        )
        
        content = resp.choices[0].message.content
        if not content:
            raise AIGenerationError("Empty response from OpenAI")
            
        validated_plan = NewsletterPlan.model_validate_json(content)
        return validated_plan
        
    except ValidationError as e:
        log.error("AI returned invalid schema for plan: %s", e)
        raise AIGenerationError(f"Validation Error: {e}")
    except Exception as e:
        log.error("AI planning failed: %s", e)
        raise
