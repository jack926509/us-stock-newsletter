"""
環境變數驗證 & 全域設定常數

使用 Pydantic Settings 在啟動時驗證所有必要的環境變數，
缺少任何必要的 API Key 將立即報錯而非在運行時產生隱晦錯誤。
"""

import logging
from pydantic_settings import BaseSettings
from pydantic import Field

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
    openai_api_key: str = Field(..., description="OpenAI API Key")
    finnhub_api_key: str = Field(..., description="Finnhub API Key")
    tavily_api_key: str = Field(..., description="Tavily API Key")
    telegram_token: str = Field(..., description="Telegram Bot Token")
    telegram_chat_id: str = Field(..., description="Telegram Chat/Channel ID")

    # 排程設定（有預設值）
    cron_hour: int = Field(default=8, description="Cron 觸發小時")
    cron_minute: int = Field(default=0, description="Cron 觸發分鐘")
    timezone: str = Field(default="Asia/Taipei", description="時區")

    # API 端點安全
    admin_api_key: str = Field(default="", description="手動觸發 API Key（選填）")

    # 服務端口
    port: int = Field(default=8080, description="服務監聽端口")

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
