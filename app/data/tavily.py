"""
Tavily 深度搜尋模組

負責針對每個主題進行深度事實搜尋，提供 AI 撰寫報告的參考資料。
加入 tenacity 重試機制，避免偶發的 API 錯誤導致整段流程失敗。
"""

from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings, TAVILY_SEARCH_URL, log
from app.clients import get_http_client
from app.data.finnhub import DataFetchError


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=False)
async def tavily_search(query: str, max_results: int = 3, time_range: str = "week") -> list:
    """
    使用 Tavily API 搜尋新聞，帶有重試機制
    """
    http_client = get_http_client()
    try:
        resp = await http_client.post(
            TAVILY_SEARCH_URL,
            json={
                "api_key": settings.tavily_api_key,
                "query": query,
                "topic": "news",
                "max_results": max_results,
                "time_range": time_range,
                "include_raw_content": True,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        log.warning("Tavily search failed for query '%s': %s (will retry)", query, e)
        raise DataFetchError(f"Tavily search failed: {e}")
