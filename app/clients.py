"""
單例客戶端初始化

集中管理 httpx / OpenAI (OpenRouter) / Telegram 客戶端的生命週期。
httpx.AsyncClient 與 telegram.Bot 都在 FastAPI lifespan 中初始化和關閉。
"""

import httpx
import telegram
from openai import AsyncOpenAI

from app.config import OPENROUTER_BASE_URL, settings

# ─── Singleton Clients ────────────────────────────────────────
# OpenAI SDK 走 OpenRouter，回傳格式 / tool calling 完全相容。
llm_client = AsyncOpenAI(
    base_url=OPENROUTER_BASE_URL,
    api_key=settings.openrouter_api_key,
    timeout=90.0,  # 涵蓋 Editor 最長呼叫時間
    default_headers={
        "HTTP-Referer": settings.openrouter_referer,
        "X-Title": settings.openrouter_app_title,
    },
)

# httpx / telegram 需要在 async context 中初始化，由 lifespan 管理
http_client: httpx.AsyncClient | None = None
telegram_bot: telegram.Bot | None = None


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


async def init_telegram_bot() -> telegram.Bot:
    """初始化 Telegram Bot 並進入 async context（讓內部 httpx pool 啟動）。"""
    global telegram_bot
    bot = telegram.Bot(token=settings.telegram_token)
    await bot.initialize()
    telegram_bot = bot
    return bot


async def close_telegram_bot() -> None:
    """關閉 Telegram Bot（釋放 httpx pool）。"""
    global telegram_bot
    if telegram_bot is not None:
        try:
            await telegram_bot.shutdown()
        finally:
            telegram_bot = None


def get_telegram_bot() -> telegram.Bot:
    """取得 Telegram Bot，尚未初始化則拋例外。"""
    if telegram_bot is None:
        raise RuntimeError("telegram bot 尚未初始化，請先呼叫 init_telegram_bot()")
    return telegram_bot
