"""ai-hedge-fund adapter 正規化輸出測試（不觸發真實 LLM / HTTP）"""

import os

# 確保 config 可以 import
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("FINNHUB_API_KEY", "test")
os.environ.setdefault("TAVILY_API_KEY", "test")
os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test")

from app.ai.hedge_fund import (  # noqa: E402
    _coerce_action,
    _coerce_confidence,
    _coerce_signal,
    _extract_reasoning,
    _normalize,
    _resolve_analysts,
)
from app.models import TickerVerdict  # noqa: E402


def test_coerce_confidence_handles_percentage():
    assert _coerce_confidence(75) == 0.75
    assert _coerce_confidence(0.6) == 0.6
    assert _coerce_confidence("50") == 0.5
    assert _coerce_confidence(None) == 0.0
    assert _coerce_confidence("garbage") == 0.0
    assert _coerce_confidence(250) == 1.0  # clamp upper
    assert _coerce_confidence(-5) == 0.0  # clamp lower


def test_coerce_signal_unknown_defaults_neutral():
    assert _coerce_signal("bullish") == "bullish"
    assert _coerce_signal("BEARISH") == "bearish"
    assert _coerce_signal(None) == "neutral"
    assert _coerce_signal("mooning") == "neutral"


def test_coerce_action_unknown_defaults_hold():
    assert _coerce_action("BUY") == "buy"
    assert _coerce_action("short") == "short"
    assert _coerce_action("invest_all") == "hold"


def test_extract_reasoning_from_dict_str_none():
    assert _extract_reasoning(None) == ""
    assert _extract_reasoning("plain reason") == "plain reason"
    assert _extract_reasoning({"reasoning": "nested"}) == "nested"
    out = _extract_reasoning({"reasoning": {"key": "val"}})
    assert "val" in out


def test_normalize_full_structure():
    raw = {
        "decisions": {
            "AAPL": {
                "action": "buy",
                "confidence": 72,
                "reasoning": "Strong moat + cash flow",
            }
        },
        "analyst_signals": {
            "warren_buffett_agent": {
                "AAPL": {
                    "signal": "bullish",
                    "confidence": 80,
                    "reasoning": "Great ROE",
                }
            },
            "fundamentals_agent": {
                "AAPL": {
                    "signal": "neutral",
                    "confidence": 0.5,
                    "reasoning": "Valuation stretched",
                }
            },
        },
    }

    verdicts = _normalize(raw, ["AAPL"])
    assert len(verdicts) == 1
    v = verdicts[0]
    assert isinstance(v, TickerVerdict)
    assert v.ticker == "AAPL"
    assert v.action == "buy"
    assert abs(v.confidence - 0.72) < 1e-6
    assert "moat" in v.reasoning
    assert len(v.signals) == 2
    signals = {s.agent: s for s in v.signals}
    assert signals["warren_buffett_agent"].signal == "bullish"
    assert signals["warren_buffett_agent"].confidence == 0.80
    assert signals["fundamentals_agent"].signal == "neutral"
    assert signals["fundamentals_agent"].confidence == 0.5


def test_normalize_missing_ticker_uses_defaults():
    raw = {"decisions": {}, "analyst_signals": {}}
    verdicts = _normalize(raw, ["NVDA"])
    assert len(verdicts) == 1
    assert verdicts[0].ticker == "NVDA"
    assert verdicts[0].action == "hold"
    assert verdicts[0].confidence == 0.0
    assert verdicts[0].signals == []


def test_normalize_decisions_as_json_string():
    raw = {
        "decisions": '{"TSLA": {"action": "sell", "confidence": 60, "reasoning": "overvalued"}}',
        "analyst_signals": {},
    }
    verdicts = _normalize(raw, ["TSLA"])
    assert verdicts[0].action == "sell"
    assert abs(verdicts[0].confidence - 0.60) < 1e-6


def test_normalize_non_dict_input_returns_empty():
    assert _normalize(None, ["AAPL"]) == []
    assert _normalize("oops", ["AAPL"]) == []


def test_resolve_analysts_maps_short_aliases_to_canonical_keys():
    out = _resolve_analysts(
        ["warren_buffett", "fundamentals", "technicals", "sentiment"]
    )
    assert out == [
        "warren_buffett",
        "fundamentals_analyst",
        "technical_analyst",
        "sentiment_analyst",
    ]


def test_resolve_analysts_dedupes_and_preserves_order():
    out = _resolve_analysts(
        ["fundamentals", "fundamentals_analyst", "TECHNICALS", "warren_buffett"]
    )
    assert out == ["fundamentals_analyst", "technical_analyst", "warren_buffett"]


def test_resolve_analysts_passes_through_unknown_keys():
    # 未知 key 直接送下去，讓上游決定報錯（保留可擴充性）
    out = _resolve_analysts(["peter_lynch", "  ", ""])
    assert out == ["peter_lynch"]
