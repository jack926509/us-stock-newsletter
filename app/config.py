"""
環境變數驗證 & 全域設定常數

使用 Pydantic Settings 在啟動時驗證所有必要的環境變數，
缺少任何必要的 API Key 將立即報錯而非在運行時產生隱晦錯誤。

LLM 統一走 OpenAI 官方 API；預設模型為 gpt-4o-mini（便宜且支援 tool calling）。
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


# ─── Settings（啟動時驗證） ───────────────────────────────────
class Settings(BaseSettings):
    """所有必要環境變數，啟動時自動驗證。"""

    # API Keys（必填）
    openai_api_key: str = Field(..., description="OpenAI API Key（統一 LLM 入口）")
    finnhub_api_key: str = Field(..., description="Finnhub API Key")
    tavily_api_key: str = Field(..., description="Tavily API Key")
    slack_bot_token: str = Field(..., description="Slack Bot Token (xoxb-...)")
    slack_channel: str = Field(..., description="Slack 頻道 ID（C...）或 #channel-name")

    # ─── 各階段使用的模型（OpenAI 模型 ID） ────────────────────
    # 預設選用 gpt-4o-mini（便宜 + tool calling 穩定）；可由 env 覆寫
    # 若想再省可改為 gpt-4.1-nano；想要更高品質可改為 gpt-4.1-mini / gpt-4o
    planner_model: str = Field(
        default="gpt-4o-mini",
        description="Planner 階段使用的模型",
    )
    writer_model: str = Field(
        default="gpt-4o-mini",
        description="Writer 階段使用的模型",
    )
    editor_model: str = Field(
        default="gpt-4o-mini",
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
        default="gpt-4o-mini",
        description="ai-hedge-fund 內部使用的 OpenAI 模型",
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

# Slack Block Kit 限制
# https://api.slack.com/reference/block-kit/blocks
SLACK_SECTION_TEXT_MAX = 3000   # section.text.text 上限
SLACK_HEADER_TEXT_MAX = 150     # header.text.text 上限
SLACK_BLOCKS_PER_MSG_MAX = 50   # 單則訊息最多 50 個 block

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
