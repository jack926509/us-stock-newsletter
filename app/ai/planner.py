"""
AI 規劃模組 (Planning)

根據初步新聞清單擷取主標題與需要深度搜索的焦點主題。
使用 Anthropic tool-use 強制結構化輸出（input_schema 來自 Pydantic），
避免手動解析 markdown code fence 的脆弱性。
"""

from tenacity import retry, stop_after_attempt, wait_exponential

from app.ai.errors import AIGenerationError
from app.clients import anthropic_client
from app.config import log
from app.models import NewsletterPlan

_PLAN_TOOL = {
    "name": "submit_newsletter_plan",
    "description": "輸出今日美股日報的主標題與焦點主題清單。",
    "input_schema": NewsletterPlan.model_json_schema(),
}


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
                "你是擁有 10 年經驗的華爾街投資策略師。"
                "閱讀提供的最新美股新聞，判斷市場情緒，"
                "並透過 submit_newsletter_plan 工具輸出日報主標題與最多 5 個焦點主題。"
            ),
            messages=[{"role": "user", "content": f"最新美股新聞：\n{news_text}"}],
            tools=[_PLAN_TOOL],
            tool_choice={"type": "tool", "name": "submit_newsletter_plan"},
        )

        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                return NewsletterPlan(**block.input)

        raise AIGenerationError("Planner 未回傳 tool_use 區塊")

    except AIGenerationError:
        raise
    except Exception as e:
        log.error("AI planning failed: %s", e)
        raise AIGenerationError(f"Planning error: {e}") from e
