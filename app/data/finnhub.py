"""
Finnhub 資料獲取模組

實作了並行獲取市場報價的優化，並加入 tenacity 重試機制，
確保在發生暫時性網路錯誤時能自動重試，而非直接失敗。
"""

import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings, FINNHUB_QUOTE_URL, FINNHUB_NEWS_URL, MARKET_SYMBOLS, log
from app.clients import get_http_client


class DataFetchError(Exception):
    """資料獲取失敗"""
    pass


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=False)
async def _fetch_single_quote(sym: str, name: str) -> tuple[str, dict]:
    """獲取單個股票的報價，帶有重試機制"""
    http_client = get_http_client()
    try:
        r = await http_client.get(
            FINNHUB_QUOTE_URL,
            params={"symbol": sym, "token": settings.finnhub_api_key},
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
        return sym, {
            "name": name,
            "price": data.get("c", 0),
            "change": data.get("dp", 0),
        }
    except Exception as e:
        log.warning("Finnhub quote fetch failed for %s: %s (will retry)", sym, e)
        raise DataFetchError(f"Failed to fetch quote for {sym}: {e}")


async def get_market_data() -> dict:
    """並行獲取所有大盤指數的報價"""
    log.info("Fetching market data concurrently...")
    
    # 使用 asyncio.gather 並行獲取所有大盤數據
    tasks = [
        _fetch_single_quote(sym, name)
        for sym, name in MARKET_SYMBOLS.items()
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    market_data = {}
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            # 即使重試了還是失敗，提供預設值
            sym = list(MARKET_SYMBOLS.keys())[i]
            name = MARKET_SYMBOLS[sym]
            log.error("Finnhub quote completely failed for %s: %s", sym, res)
            market_data[sym] = {"name": name, "price": 0, "change": 0}
        else:
            sym, data = res
            market_data[sym] = data
            
    return market_data


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=False)
async def get_finnhub_news(category: str = "general", count: int = 5) -> list:
    """獲取最新的 Finnhub 新聞，帶有重試機制"""
    http_client = get_http_client()
    try:
        r = await http_client.get(
            FINNHUB_NEWS_URL,
            params={"category": category, "token": settings.finnhub_api_key},
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()[:count]
    except Exception as e:
        log.warning("Finnhub news fetch failed: %s (will retry)", e)
        raise DataFetchError(f"Failed to fetch news: {e}")
