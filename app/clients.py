"""
單例客戶端初始化

集中管理 httpx / Anthropic / Telegram 客戶端的生命週期。
httpx.AsyncClient 在 FastAPI lifespan 中初始化和關閉。
"""

import httpx
import telegram
from anthropic import AsyncAnthropic

from app.config import settings

# ─── Singleton Clients ────────────────────────────────────────
anthropic_client = AsyncAnthropic(
    api_key=settings.anthropic_api_key,
    timeout=90.0,  # 涵蓋 Editor 最長呼叫時間
)
telegram_bot = telegram.Bot(token=settings.telegram_token)

# httpx client 需要在 async context 中初始化，由 lifespan 管理
http_client: httpx.AsyncClient | None = None


async def init_http_client() -> httpx.AsyncClient:
    """在 FastAPI lifespan 中呼叫以初始化 httpx client。"""
    global http_client
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    return http_client


async def close_http_client() -> None:
    """在 FastAPI lifespan 中呼叫以關閉 httpx client。"""
    global http_client
    if http_client:
        await http_client.aclose()
        http_client = None


def get_http_client() -> httpx.AsyncClient:
    """取得 httpx client，如果尚未初始化則拋出異常。"""
    if http_client is None:
        raise RuntimeError("httpx client 尚未初始化，請先呼叫 init_http_client()")
    return http_client
