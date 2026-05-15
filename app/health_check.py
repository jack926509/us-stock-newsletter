"""
外部依賴連線健檢：OpenAI / Finnhub / Tavily / Slack 並行 ping。

每家給 5 秒超時、各自 try/except，回 `PingResult` 列表。Slack `ping` 指令把
這個結果格式化呈現。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from app.clients import get_http_client, get_slack_client, llm_client
from app.config import FINNHUB_QUOTE_URL, TAVILY_SEARCH_URL, log, settings

_PING_TIMEOUT_SECONDS = 5.0


@dataclass
class PingResult:
    name: str
    ok: bool
    latency_ms: int
    error: str | None = None


async def _timed(name: str, coro) -> PingResult:
    start = time.monotonic()
    try:
        await asyncio.wait_for(coro, timeout=_PING_TIMEOUT_SECONDS)
        return PingResult(name=name, ok=True, latency_ms=int((time.monotonic() - start) * 1000))
    except asyncio.TimeoutError:
        return PingResult(
            name=name, ok=False,
            latency_ms=int((time.monotonic() - start) * 1000),
            error=f"逾時（>{_PING_TIMEOUT_SECONDS:.0f}s）",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("ping %s 失敗: %s", name, e)
        return PingResult(
            name=name, ok=False,
            latency_ms=int((time.monotonic() - start) * 1000),
            error=str(e)[:120],
        )


async def _openai_call() -> None:
    # models.list() 是輕量請求，不消耗 LLM tokens
    await llm_client.models.list()


async def _finnhub_call() -> None:
    r = await get_http_client().get(
        FINNHUB_QUOTE_URL,
        params={"symbol": "AAPL", "token": settings.finnhub_api_key},
        timeout=_PING_TIMEOUT_SECONDS,
    )
    r.raise_for_status()


async def _tavily_call() -> None:
    r = await get_http_client().post(
        TAVILY_SEARCH_URL,
        json={
            "api_key": settings.tavily_api_key,
            "query": "ping",
            "max_results": 1,
            "search_depth": "basic",
        },
        timeout=_PING_TIMEOUT_SECONDS,
    )
    r.raise_for_status()


async def _slack_call() -> None:
    await get_slack_client().auth_test()


async def check_all() -> list[PingResult]:
    """並行探測四家。永遠回傳長度 4 的 list（順序固定）。"""
    return list(
        await asyncio.gather(
            _timed("OpenAI", _openai_call()),
            _timed("Finnhub", _finnhub_call()),
            _timed("Tavily", _tavily_call()),
            _timed("Slack", _slack_call()),
        )
    )
