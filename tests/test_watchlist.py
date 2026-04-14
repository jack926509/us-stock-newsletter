"""watchlist.json 讀取器測試"""

import json
import os

import pytest

# 為了能在無 .env 的情況下 import config，預先塞入必要的 env var
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("FINNHUB_API_KEY", "test")
os.environ.setdefault("TAVILY_API_KEY", "test")
os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test")

from app.config import DEFAULT_WATCHLIST, settings  # noqa: E402
from app.data.watchlist import load_watchlist  # noqa: E402


@pytest.fixture
def tmp_watchlist(tmp_path, monkeypatch):
    """提供一個 tmp watchlist 檔案並讓 settings 指向它。"""

    def _writer(payload):
        p = tmp_path / "watchlist.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(settings, "watchlist_path", str(p))
        return p

    return _writer


def test_valid_watchlist(tmp_watchlist):
    tmp_watchlist({"tickers": ["aapl", "NVDA", "msft"]})
    assert load_watchlist() == ["AAPL", "NVDA", "MSFT"]


def test_dedupes_and_uppercases(tmp_watchlist):
    tmp_watchlist({"tickers": ["TSLA", "tsla", "TSLA"]})
    assert load_watchlist() == ["TSLA"]


def test_invalid_tickers_filtered(tmp_watchlist):
    tmp_watchlist({"tickers": ["AAPL", "", "TOO_LONG_TICKER", "lowercase!", 123, "NVDA"]})
    assert load_watchlist() == ["AAPL", "NVDA"]


def test_hard_cap_10(tmp_watchlist):
    tmp_watchlist({"tickers": [f"T{i}" for i in range(20)]})
    result = load_watchlist()
    assert len(result) <= 10


def test_empty_list_falls_back(tmp_watchlist):
    tmp_watchlist({"tickers": []})
    assert load_watchlist() == list(DEFAULT_WATCHLIST)


def test_missing_file_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "watchlist_path", str(tmp_path / "nope.json"))
    assert load_watchlist() == list(DEFAULT_WATCHLIST)


def test_bad_json_falls_back(tmp_path, monkeypatch):
    p = tmp_path / "watchlist.json"
    p.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(settings, "watchlist_path", str(p))
    assert load_watchlist() == list(DEFAULT_WATCHLIST)


def test_wrong_structure_falls_back(tmp_watchlist):
    tmp_watchlist({"symbols": ["AAPL"]})  # 錯誤 key
    assert load_watchlist() == list(DEFAULT_WATCHLIST)
