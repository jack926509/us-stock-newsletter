"""
環境變數驗證 & 全域設定常數

使用 Pydantic Settings 在啟動時驗證所有必要的環境變數，
缺少任何必要的 API Key 將立即報錯而非在運行時產生隱晦錯誤。

LLM 統一走 OpenRouter（OpenAI 相容端點），方便切換低成本模型。
"""

import logging

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

# ─── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("newsletter")


# ─── OpenRouter 端點 ──────────────────────────────────────────
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


# ─── Settings（啟動時驗證） ───────────────────────────────────
class Settings(BaseSettings):
    """所有必要環境變數，啟動時自動驗證。"""

    # API Keys（必填）
    openrouter_api_key: str = Field(..., description="OpenRouter API Key（統一 LLM 入口）")
    finnhub_api_key: str = Field(..., description="Finnhub API Key")
    tavily_api_key: str = Field(..., description="Tavily API Key")
    telegram_token: str = Field(..., description="Telegram Bot Token")
    telegram_chat_id: str = Field(..., description="Telegram Chat/Channel ID")

    # OpenRouter 路由元資料（選填，回報給 OpenRouter 用於 leaderboard / 限流追蹤）
    openrouter_referer: str = Field(
        default="https://github.com/jack926509/us-stock-newsletter",
        description="OpenRouter HTTP-Referer header",
    )
    openrouter_app_title: str = Field(
        default="US Stock Newsletter",
        description="OpenRouter X-Title header",
    )

    # ─── 各階段使用的模型（皆為 OpenRouter 模型 ID） ───────────
    # 預設選用便宜的 Gemini Flash Lite / Flash 系列；可由 env 覆寫
    planner_model: str = Field(
        default="google/gemini-2.5-flash-lite",
        description="Planner 階段使用的模型",
    )
    writer_model: str = Field(
        default="google/gemini-2.5-flash-lite",
        description="Writer 階段使用的模型",
    )
    editor_model: str = Field(
        default="google/gemini-2.5-flash",
        description="Editor 階段使用的模型（需要穩定的 tool calling）",
    )

    # 排程設定（有預設值）
    cron_hour: int = Field(default=8, description="Cron 觸發小時")
    cron_minute: int = Field(default=0, description="Cron 觸發分鐘")
    timezone: str = Field(default="Asia/Taipei", description="時區")

    # API 端點安全
    admin_api_key: str = Field(default="", description="手動觸發 API Key（選填）")

    # 服務端口
    port: int = Field(default=8080, description="服務監聽端口")

    # ─── ai-hedge-fund 整合（全部為 optional） ─────────────────
    financial_datasets_api_key: str = Field(
        default="",
        description="Financial Datasets API Key（預設免費清單 AAPL/MSFT/NVDA/GOOGL/TSLA 不需要填）",
    )
    hedge_fund_analysts: list[str] = Field(
        default_factory=lambda: [
            "warren_buffett",
            "fundamentals_analyst",
            "technical_analyst",
            "sentiment_analyst",
        ],
        description="ai-hedge-fund 要啟用的分析師列表（env 用逗號分隔）",
    )
    hedge_fund_model: str = Field(
        default="google/gemini-2.5-flash-lite",
        description="ai-hedge-fund 內部使用的模型（透過 OpenRouter 呼叫）",
    )
    hedge_fund_timeout: int = Field(
        default=240,
        description="ai-hedge-fund 整輪分析的 timeout（秒）",
    )
    watchlist_path: str = Field(
        default="watchlist.json",
        description="自選股清單檔案路徑（相對於 repo 根目錄）",
    )

    @field_validator("hedge_fund_analysts", mode="before")
    @classmethod
    def _split_analysts(cls, v):
        """支援從 env 讀入逗號字串，自動拆成 list。"""
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# 在 import 時即驗證 — 缺少必要變數會立即報錯
try:
    settings = Settings()
    log.info("✅ 環境變數驗證通過")
except Exception as e:
    log.error("❌ 環境變數驗證失敗: %s", e)
    raise


# ─── 常數 ─────────────────────────────────────────────────────
FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/news"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TELEGRAM_MAX_LEN = 4000

MARKET_SYMBOLS = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "DIA": "Dow Jones",
}

# 冷卻時間（秒），防止手動觸發過於頻繁
TRIGGER_COOLDOWN_SECONDS = 300

# ─── 自選股 / ai-hedge-fund 相關常數 ───────────────────────────
# Financial Datasets API 免費層覆蓋的 ticker；若 watchlist.json 缺失或壞掉則 fallback
DEFAULT_WATCHLIST: tuple[str, ...] = ("AAPL", "MSFT", "NVDA", "GOOGL", "TSLA")
# watchlist 硬上限，避免 LLM 呼叫成本失控
MAX_WATCHLIST_SIZE = 10

# Ticker 規格（單一來源，formatter / watchlist 共用）
# 接受 1 字母開頭 + 0-9 字母/數字/. / -（如 BRK.B, BRK-B）
TICKER_PATTERN = r"[A-Z][A-Z0-9.\-]{0,9}"
