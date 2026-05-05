"""build_verdicts_card Telegram HTML 輸出測試"""

import os

os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("FINNHUB_API_KEY", "test")
os.environ.setdefault("TAVILY_API_KEY", "test")
os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test")

from app.formatter import build_verdicts_card  # noqa: E402
from app.models import AnalystSignal, TickerVerdict  # noqa: E402


def test_empty_returns_empty_string():
    assert build_verdicts_card([]) == ""


def test_single_verdict_renders_core_fields():
    verdicts = [
        TickerVerdict(
            ticker="AAPL",
            action="buy",
            confidence=0.72,
            reasoning="護城河穩固且現金流強勁",
            signals=[
                AnalystSignal(
                    agent="warren_buffett",
                    signal="bullish",
                    confidence=0.8,
                    reasoning="ROE 超過 30%",
                ),
                AnalystSignal(
                    agent="technicals",
                    signal="neutral",
                    confidence=0.55,
                    reasoning="短期均線糾結",
                ),
            ],
        )
    ]
    html = build_verdicts_card(verdicts)
    assert "AI 分析師個股共識" in html
    assert "<b>AAPL</b>" in html
    assert "BUY" in html
    assert "72%" in html
    assert "護城河穩固" in html
    # analyst display names
    assert "Warren Buffett" in html
    assert "技術面分析" in html


def test_html_escape_applied():
    verdicts = [
        TickerVerdict(
            ticker="T&T",
            action="hold",
            confidence=0.5,
            reasoning="<script>bad</script>",
            signals=[],
        )
    ]
    html = build_verdicts_card(verdicts)
    assert "T&amp;T" in html
    assert "<script>bad</script>" not in html
    assert "&lt;script&gt;" in html


def test_multiple_verdicts_all_rendered():
    verdicts = [
        TickerVerdict(ticker=t, action="hold", confidence=0.5)
        for t in ["AAPL", "NVDA", "MSFT"]
    ]
    html = build_verdicts_card(verdicts)
    for t in ["AAPL", "NVDA", "MSFT"]:
        assert f"<b>{t}</b>" in html
