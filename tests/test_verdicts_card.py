"""build_verdicts_blocks Slack Block Kit 輸出測試"""

import os

os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("FINNHUB_API_KEY", "test")
os.environ.setdefault("TAVILY_API_KEY", "test")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SLACK_CHANNEL", "C_TEST")

from app.formatter import build_verdicts_blocks  # noqa: E402
from app.models import AnalystSignal, TickerVerdict  # noqa: E402


def _all_text(blocks):
    out: list[str] = []
    for b in blocks:
        if "text" in b and isinstance(b["text"], dict):
            out.append(b["text"].get("text", ""))
    return "\n".join(out)


def test_empty_returns_empty_list():
    assert build_verdicts_blocks([]) == []


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
    blocks = build_verdicts_blocks(verdicts)
    text = _all_text(blocks)
    # header block
    assert blocks[0]["type"] == "header"
    assert "AI 分析師個股共識" in blocks[0]["text"]["text"]
    assert "*AAPL*" in text
    assert "BUY" in text
    assert "72%" in text
    assert "護城河穩固" in text
    assert "Warren Buffett" in text
    assert "技術面分析" in text


def test_mrkdwn_escape_applied():
    verdicts = [
        TickerVerdict(
            ticker="T&T",
            action="hold",
            confidence=0.5,
            reasoning="<script>bad</script>",
            signals=[],
        )
    ]
    text = _all_text(build_verdicts_blocks(verdicts))
    assert "T&amp;T" in text
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_multiple_verdicts_all_rendered():
    verdicts = [
        TickerVerdict(ticker=t, action="hold", confidence=0.5)
        for t in ["AAPL", "NVDA", "MSFT"]
    ]
    text = _all_text(build_verdicts_blocks(verdicts))
    for t in ["AAPL", "NVDA", "MSFT"]:
        assert f"*{t}*" in text
