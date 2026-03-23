"""
AI 寫手模組 (Writer)

負責利用 Tavily 的深層搜索資料撰寫相應章節。
"""

from tenacity import retry, stop_after_attempt, wait_exponential

from app.clients import anthropic_client
from app.config import log
from app.ai.planner import AIGenerationError


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def write_section(topic: str, research: list) -> str:
    """根據搜索結果撰寫一個單獨的分析段落"""
    research_text = "\n\n".join([
        f"標題: {r.get('title')}\nURL: {r.get('url')}\n內容: {r.get('content', '')[:600]}"
        for r in research
    ])

    try:
        resp = await anthropic_client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=800,
            system=(
                "你是專業美股證券分析師。針對主題撰寫分析報告章節（繁體中文）：\n"
                "1. 標題（含公司名+股票代碼如NVDA）\n"
                "2. 核心數據（如有）\n"
                "3. 分析內容（為什麼重要、對投資者影響）\n"
                "語氣：客觀、數據驅動。必須引用來源URL。嚴禁捏造數據。"
            ),
            messages=[{"role": "user", "content": f"主題: {topic}\n\n研究資料:\n{research_text}"}],
        )
        content = resp.content[0].text
        if not content:
            raise AIGenerationError(f"Missing content for section writer on topic: {topic}")
        return content

    except AIGenerationError:
        raise
    except Exception as e:
        log.error("Failed to write section for topic %s: %s", topic, e)
        raise AIGenerationError(f"Writer error for topic {topic}: {e}") from e
