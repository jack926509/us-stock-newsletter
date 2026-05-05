"""ai-hedge-fund adapter 正規化輸出測試（不觸發真實 LLM / HTTP）"""

import os

# 確保 config 可以 import
os.environ.setdefault("OPENAI_API_KEY", "test")
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
    assert _extract_reasoning({"error": "Missing price data"}) == "Missing price data"


def test_extract_reasoning_fundamentals_subsignals():
    sig = {
        "profitability_signal": {
            "signal": "bullish",
            "details": "ROE: 143.60%, Net Margin: 27.68%",
        },
        "growth_signal": {
            "signal": "bearish",
            "details": "Revenue Growth: -2.3%",
        },
    }
    out = _extract_reasoning(sig)
    assert "profitability" in out
    assert "ROE" in out
    assert "growth" in out
    assert "{" not in out  # 不應包含 raw JSON


def test_extract_reasoning_sentiment_subsignals():
    sig = {
        "insider_trading": {
            "signal": "bearish",
            "confidence": 62,
            "metrics": {"total_trades": 40},
        },
    }
    out = _extract_reasoning(sig)
    assert "insider trading" in out
    assert "↓" in out
    assert "{" not in out


def test_extract_reasoning_technical_subsignals():
    sig = {
        "trend_following": {"signal": "bullish", "confidence": 70},
        "momentum": {"signal": "bearish", "confidence": 45},
    }
    out = _extract_reasoning(sig)
    assert "trend following" in out
    assert "momentum" in out


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


def test_normalize_filters_infra_agents():
    """risk_management_agent / portfolio_management_agent 不應出現在 signals。"""
    raw = {
        "decisions": {
            "AAPL": {"action": "buy", "confidence": 72, "reasoning": "Good"},
        },
        "analyst_signals": {
            "warren_buffett_agent": {
                "AAPL": {"signal": "bullish", "confidence": 80, "reasoning": "ROE"},
            },
            "risk_management_agent": {
                "AAPL": {"remaining_position_limit": 5000, "reasoning": {"error": "no data"}},
            },
            "portfolio_management_agent": {
                "AAPL": {"signal": "hold", "confidence": 50, "reasoning": "N/A"},
            },
        },
    }
    verdicts = _normalize(raw, ["AAPL"])
    agent_names = [s.agent for s in verdicts[0].signals]
    assert "warren_buffett_agent" in agent_names
    assert "risk_management_agent" not in agent_names
    assert "portfolio_management_agent" not in agent_names


def test_normalize_derives_action_from_signals_when_pm_default():
    """PM 給 HOLD/100%/'No valid trade' 時，應從 analyst signals 推導方向。"""
    raw = {
        "decisions": {
            "AAPL": {
                "action": "hold",
                "confidence": 100,
                "reasoning": "No valid trade available",
            }
        },
        "analyst_signals": {
            "fundamentals_analyst_agent": {
                "AAPL": {"signal": "bullish", "confidence": 60, "reasoning": "ROE good"},
            },
            "sentiment_analyst_agent": {
                "AAPL": {"signal": "bullish", "confidence": 70, "reasoning": "Insider buy"},
            },
            "technical_analyst_agent": {
                "AAPL": {"signal": "bearish", "confidence": 50, "reasoning": "Trend weak"},
            },
        },
    }
    verdicts = _normalize(raw, ["AAPL"])
    v = verdicts[0]
    # 2 bullish vs 1 bearish → should derive "buy"
    assert v.action == "buy"
    # confidence should be average of analysts, not PM's 100%
    assert v.confidence < 1.0
    assert v.confidence > 0.0
    assert v.reasoning == ""  # PM default reasoning cleared


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
