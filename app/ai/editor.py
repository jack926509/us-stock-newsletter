"""
AI 編輯模組 (Editor)

將生成的章節與市場快照組合成一個 JSON 結構，
然後透過 Pydantic 強型別檢查，確保不會產生壞掉的 JSON 影響後送的 Telegram 格式。
"""

from datetime import datetime
import pytz
from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential

from app.clients import openai_client
from app.config import settings, log
from app.models import Newsletter
from app.ai.planner import AIGenerationError


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=3, max=15))
async def edit_newsletter(title: str, sections: list, market: dict) -> Newsletter:
    """整合章節內容並輸出符合 Newsletter Pydantic model 的 JSON"""
    today = datetime.now(pytz.timezone(settings.timezone)).strftime("%Y-%m-%d")
    sections_text = "\n\n\n".join(sections)
    
    market_snapshot = " | ".join([
        f"{v['name']}: {v['price']:.2f} ({v['change']:+.2f}%)"
        for v in market.values()
    ])
    
    try:
        resp = await openai_client.chat.completions.create(
            model="gpt-4o",
            max_tokens=2500,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"你是Bloomberg/WSJ風格的主編。整合分析報告為結構化 JSON（繁體中文）。\n"
                        f"今日日期: {today}\n"
                        f"大盤數據: {market_snapshot}\n\n"
                        "嚴格輸出以下 JSON 結構：\n"
                        '{"subject":"主旨(15字內)","market_summary":"大盤情緒一句話摘要",'
                        '"sections":[{"title":"章節標題","body":"正文（純文字，股票代碼用【TICKER】包住）",'
                        '"sources":[{"title":"來源標題","url":"URL"}]}],'
                        '"insights":"投資啟示與風險提醒(2-3句，股票代碼用【TICKER】包住)"}\n\n'
                        "規則：body/insights 只輸出純文字，不含任何 HTML；sections 恰好符合傳入的章節數量；每個 sources 最多 3 筆。"
                    ),
                },
                {"role": "user", "content": f"主標題: {title}\n\n所有章節內容:\n{sections_text}"},
            ],
            timeout=60.0,
        )
        
        content = resp.choices[0].message.content
        if not content:
            raise AIGenerationError("Editor returned empty content")
            
        validated_newsletter = Newsletter.model_validate_json(content)
        return validated_newsletter
        
    except ValidationError as e:
        log.error("AI Editor returned invalid JSON schema: %s", e)
        raise AIGenerationError(f"Editor JSON Schema error: {e}")
    except Exception as e:
        log.error("Failed to edit newsletter: %s", e)
        raise
