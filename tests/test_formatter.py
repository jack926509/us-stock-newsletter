import pytest
from app.formatter import (
    escape_html,
    highlight_ticker,
    get_trend_arrow,
    build_header,
)
from datetime import datetime

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
    assert get_trend_arrow(0.0) == "📈"
    assert get_trend_arrow(-1.2) == "📉"

def test_build_header():
    # 2026-03-08 is Sunday (週日)
    dt = datetime(2026, 3, 8)
    header = build_header("今日重點新聞", dt)
    assert "2026/03/08（週日）" in header
    assert "今日重點新聞" in header
