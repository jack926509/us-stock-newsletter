import os

os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("FINNHUB_API_KEY", "test")
os.environ.setdefault("TAVILY_API_KEY", "test")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SLACK_CHANNEL", "C_TEST")

from datetime import datetime  # noqa: E402

from app.formatter import (  # noqa: E402
    build_header_blocks,
    build_market_blocks,
    build_section_blocks,
    escape_mrkdwn,
    get_trend_arrow,
    highlight_ticker,
)


def _all_text(blocks):
    """把一組 blocks 的所有文字攤平成一個大字串方便斷言。"""
    out: list[str] = []
    for b in blocks:
        if "text" in b and isinstance(b["text"], dict):
            out.append(b["text"].get("text", ""))
        for f in b.get("fields", []) or []:
            out.append(f.get("text", ""))
        for e in b.get("elements", []) or []:
            out.append(e.get("text", ""))
    return "\n".join(out)


def test_escape_mrkdwn():
    assert escape_mrkdwn("A & B") == "A &amp; B"
    assert escape_mrkdwn("<script>alert()</script>") == "&lt;script&gt;alert()&lt;/script&gt;"
    assert escape_mrkdwn(">_>") == "&gt;_&gt;"


def test_highlight_ticker_becomes_slack_bold():
    assert highlight_ticker("推薦買入【NVDA】") == "推薦買入*NVDA*"
    assert highlight_ticker("沒有股票名稱") == "沒有股票名稱"
    assert highlight_ticker("【AAPL】和【MSFT】") == "*AAPL*和*MSFT*"


def test_get_trend_arrow():
    assert get_trend_arrow(1.5) == "📈"
    assert get_trend_arrow(0.0) == "📉"
    assert get_trend_arrow(-1.2) == "📉"


def test_build_header_blocks():
    dt = datetime(2026, 3, 8)  # Sunday
    blocks = build_header_blocks("今日重點新聞", dt)
    assert blocks[0]["type"] == "header"
    assert "2026/03/08（週日）" in blocks[0]["text"]["text"]
    text = _all_text(blocks)
    assert "今日重點新聞" in text
    assert "*今日重點新聞*" in text  # Slack bold


def test_build_section_blocks_with_numbering_and_sources():
    from types import SimpleNamespace
    sources = [
        SimpleNamespace(title="Bloomberg News", url="https://example.com/1"),
        SimpleNamespace(title="Reuters", url="https://example.com/2"),
    ]
    blocks = build_section_blocks(0, 3, "NVDA 財報分析", "【NVDA】大漲", sources)
    # header + section + context
    assert blocks[0]["type"] == "header"
    assert "[1/3]" in blocks[0]["text"]["text"]
    assert "NVDA 財報分析" in blocks[0]["text"]["text"]

    text = _all_text(blocks)
    assert "*NVDA*" in text  # ticker 被轉粗體
    assert "<https://example.com/1|[1] Bloomberg News>" in text
    assert "<https://example.com/2|[2] Reuters>" in text


def test_build_market_blocks():
    market = {
        "SPY": {"name": "S&P 500", "price": 5123.45, "change": 0.52},
        "QQQ": {"name": "Nasdaq 100", "price": 17834.22, "change": -0.23},
    }
    blocks = build_market_blocks(market, "市場偏多")
    text = _all_text(blocks)
    assert "🟢" in text
    assert "🔴" in text
    assert "5,123.45" in text
    assert "市場偏多" in text
    # S&P 500 內的 & 必須跳脫
    assert "S&amp;P 500" in text
