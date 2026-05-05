import os

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("FINNHUB_API_KEY", "test")
os.environ.setdefault("TAVILY_API_KEY", "test")
os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test")

from app.formatter import (  # noqa: E402
    escape_html,
    highlight_ticker,
    get_trend_arrow,
    build_header,
    build_section_block,
    build_market_card,
)
from datetime import datetime  # noqa: E402


def test_escape_html():
    assert escape_html("A & B") == "A &amp; B"
    assert escape_html("<script>alert()</script>") == "&lt;script&gt;alert()&lt;/script&gt;"
    assert escape_html(">_>") == "&gt;_&gt;"

def test_highlight_ticker():
    assert highlight_ticker("推薦買入【NVDA】") == "推薦買入<b>NVDA</b>"
    assert highlight_ticker("沒有股票名稱") == "沒有股票名稱"
    assert highlight_ticker("【AAPL】和【MSFT】") == "<b>AAPL</b>和<b>MSFT</b>"

def test_get_trend_arrow():
    assert get_trend_arrow(1.5) == "📈"
    # 0% 變動視為持平→偏空（避免把無變動誤標為上漲）
    assert get_trend_arrow(0.0) == "📉"
    assert get_trend_arrow(-1.2) == "📉"

def test_build_header():
    # 2026-03-08 is Sunday (週日)
    dt = datetime(2026, 3, 8)
    header = build_header("今日重點新聞", dt)
    assert "2026/03/08（週日）" in header
    assert "今日重點新聞" in header
    assert "美股日報" in header

def test_build_section_block_with_numbering():
    """章節標題應包含進度編號 [1/3]。"""
    text = build_section_block(0, 3, "NVDA 財報分析", "【NVDA】大漲", [])
    assert "[1/3]" in text
    assert "NVDA 財報分析" in text

def test_build_section_block_with_sources():
    """消息來源應出現在獨立行，以 · 分隔。"""
    # 使用 SimpleNamespace 模擬 Source 物件，避免導入 models 觸發 config
    from types import SimpleNamespace
    sources = [
        SimpleNamespace(title="Bloomberg News", url="https://example.com/1"),
        SimpleNamespace(title="Reuters", url="https://example.com/2"),
    ]
    text = build_section_block(0, 2, "標題", "正文", sources)
    assert "\n🔗 " in text
    assert " · " in text
    assert "[1]" in text
    assert "[2]" in text

def test_build_market_card():
    """大盤快照應顯示漲跌與價格。"""
    market = {
        "SPY": {"name": "S&P 500", "price": 5123.45, "change": 0.52},
        "QQQ": {"name": "Nasdaq 100", "price": 17834.22, "change": -0.23},
    }
    card = build_market_card(market, "市場偏多")
    assert "🟢" in card
    assert "🔴" in card
    assert "5,123.45" in card
    assert "市場偏多" in card
