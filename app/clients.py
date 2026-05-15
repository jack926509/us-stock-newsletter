"""
單例客戶端初始化

集中管理 httpx / OpenAI / Slack 客戶端的生命週期。
httpx.AsyncClient 與 Slack AsyncWebClient 都在 FastAPI lifespan 中初始化和關閉。
"""

import httpx
from openai import AsyncOpenAI
from slack_sdk.web.async_client import AsyncWebClient

from app.config import settings

# ─── Singleton Clients ────────────────────────────────────────
# OpenAI 官方端點（base_url 採 SDK 預設）。
llm_client = AsyncOpenAI(
    api_key=settings.openai_api_key,
    timeout=90.0,  # 涵蓋 Editor 最長呼叫時間
)

# httpx / slack 需要在 async context 中初始化，由 lifespan 管理
http_client: httpx.AsyncClient | None = None
slack_client: AsyncWebClient | None = None


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


async def init_slack_client() -> AsyncWebClient:
    """初始化 Slack AsyncWebClient，呼叫 auth.test 驗證 token 有效。"""
    global slack_client
    client = AsyncWebClient(token=settings.slack_bot_token, timeout=30)
    # 啟動時驗證 token，失敗在這裡早炸，比運行時送訊失敗易診斷
    await client.auth_test()
    slack_client = client
    return client


async def close_slack_client() -> None:
    """釋放 Slack client（slack_sdk 沒有顯式 close，這裡只清空 singleton）。"""
    global slack_client
    slack_client = None


def get_slack_client() -> AsyncWebClient:
    """取得 Slack client，尚未初始化則拋例外。"""
    if slack_client is None:
        raise RuntimeError("slack client 尚未初始化，請先呼叫 init_slack_client()")
    return slack_client
