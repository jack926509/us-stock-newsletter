"""
ai-hedge-fund adapter

把 `vendor/ai_hedge_fund/` 的 `run_hedge_fund()` 包成 async、
轉換輸出到 `TickerVerdict` Pydantic 物件，錯誤時降級回傳空 list，
絕不中斷日報主流程。

ai-hedge-fund 的 src/ 使用 `from src.agents...` 絕對匯入，所以
要把 `vendor/ai_hedge_fund/` 的父層加入 sys.path，而不是 src 本身。
"""

import asyncio
import json
import os
import sys
import types
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.config import log, settings
from app.models import AnalystSignal, TickerVerdict

# ─── 首次 import 時把 ai-hedge-fund 放進 sys.path ──────────────
_REPO_ROOT = Path(__file__).resolve().parents[2]
_VENDOR_ROOT = _REPO_ROOT / "vendor" / "ai_hedge_fund"
if _VENDOR_ROOT.exists() and str(_VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_VENDOR_ROOT))


# ai-hedge-fund 的 src/llm/models.py **無條件** import 所有 provider
# (openai, groq, deepseek, xai, gigachat, ollama, google-genai, anthropic)
# 為了避免 Docker image 安裝 8 個 langchain provider 套件，
# 在首次 import 前為未使用的 provider 注入 stub 模組。
# 我們實際只會走到 ChatAnthropic 分支，其他 class 不會被實例化，
# 所以 stub 只要存在屬性即可。
# ai-hedge-fund 上游的 ANALYST_CONFIG key（src/utils/analysts.py）
# 我們允許設定使用者用較短的別名（fundamentals / technicals / sentiment），
# 在送進 run_hedge_fund 之前自動轉換成上游真實 key，避免 KeyError。
_ANALYST_ALIASES = {
    "fundamentals": "fundamentals_analyst",
    "fundamentals_analyst": "fundamentals_analyst",
    "technicals": "technical_analyst",
    "technical": "technical_analyst",
    "technical_analyst": "technical_analyst",
    "sentiment": "sentiment_analyst",
    "sentiment_analyst": "sentiment_analyst",
    "news_sentiment": "news_sentiment_analyst",
    "news_sentiment_analyst": "news_sentiment_analyst",
    "valuation": "valuation_analyst",
    "valuation_analyst": "valuation_analyst",
    "growth": "growth_analyst",
    "growth_analyst": "growth_analyst",
}


def _resolve_analysts(raw: list[str]) -> list[str]:
    """把 user 友善的短別名轉成 ai-hedge-fund 的真實 key，去重保留順序。"""
    seen: set[str] = set()
    resolved: list[str] = []
    for name in raw:
        key = (name or "").strip().lower()
        if not key:
            continue
        canonical = _ANALYST_ALIASES.get(key, key)
        if canonical in seen:
            continue
        seen.add(canonical)
        resolved.append(canonical)
    return resolved


_STUB_PROVIDERS = {
    "langchain_deepseek": ["ChatDeepSeek"],
    "langchain_google_genai": ["ChatGoogleGenerativeAI"],
    "langchain_groq": ["ChatGroq"],
    "langchain_xai": ["ChatXAI"],
    "langchain_anthropic": ["ChatAnthropic"],
    "langchain_gigachat": ["GigaChat"],
    "langchain_ollama": ["ChatOllama"],
}


def _install_provider_stubs() -> None:
    """為未使用的 langchain provider 建立空 stub，避免 ImportError。"""
    for module_name, class_names in _STUB_PROVIDERS.items():
        if module_name in sys.modules:
            continue
        try:
            __import__(module_name)
            continue  # 真實套件存在就不 stub
        except Exception:  # noqa: BLE001
            pass
        stub = types.ModuleType(module_name)
        for cls_name in class_names:
            setattr(
                stub,
                cls_name,
                type(cls_name, (), {"__init__": lambda self, *a, **kw: None}),
            )
        sys.modules[module_name] = stub


_runner_cache = None


def _import_runner():
    """延後匯入並快取，避免每次 pipeline 都重跑 stub 安裝與 import。"""
    global _runner_cache
    if _runner_cache is not None:
        return _runner_cache
    _install_provider_stubs()
    from src.main import run_hedge_fund  # type: ignore  # noqa: WPS433

    _runner_cache = run_hedge_fund
    return _runner_cache


async def run_hedge_fund_analysis(tickers: list[str]) -> list[TickerVerdict]:
    """對 watchlist 跑 ai-hedge-fund，回傳每檔股票的 TickerVerdict。

    任何失敗（submodule 缺失、API 錯誤、LLM 錯誤、timeout）都會被吞掉，
    回傳空 list 讓 pipeline 繼續跑新聞流程。
    """
    if not tickers:
        return []

    try:
        run_hedge_fund = _import_runner()
    except Exception as e:  # noqa: BLE001
        log.error("ai-hedge-fund submodule 未就緒或無法 import：%s — 本次跳過個股分析", e)
        return []

    # vendor 的 get_model() 對 OpenAI provider 透過 langchain-openai 讀取
    # OPENAI_API_KEY 環境變數；把已驗證過的 settings.openai_api_key 強制寫回，
    # 確保 vendor 內部 LLM 呼叫拿得到 key（即使 Zeabur 把 env 名稱大小寫弄亂）。
    os.environ["OPENAI_API_KEY"] = settings.openai_api_key
    if settings.financial_datasets_api_key and not os.environ.get("FINANCIAL_DATASETS_API_KEY"):
        os.environ["FINANCIAL_DATASETS_API_KEY"] = settings.financial_datasets_api_key

    end = date.today().isoformat()
    start = (date.today() - timedelta(days=90)).isoformat()
    portfolio: dict[str, Any] = {
        "cash": 100_000.0,
        "margin_requirement": 0.0,
        "margin_used": 0.0,
        "positions": {
            t: {
                "long": 0,
                "short": 0,
                "long_cost_basis": 0.0,
                "short_cost_basis": 0.0,
                "short_margin_used": 0.0,
            }
            for t in tickers
        },
        "realized_gains": {
            t: {"long": 0.0, "short": 0.0} for t in tickers
        },
    }

    selected_analysts = _resolve_analysts(list(settings.hedge_fund_analysts))
    if not selected_analysts:
        log.warning("hedge_fund_analysts 為空或全為未知別名，跳過個股分析")
        return []

    def _run_sync():
        return run_hedge_fund(
            tickers=tickers,
            start_date=start,
            end_date=end,
            portfolio=portfolio,
            show_reasoning=False,
            selected_analysts=selected_analysts,
            model_name=settings.hedge_fund_model,
            model_provider="OpenAI",
        )

    log.info(
        "🧠 啟動 ai-hedge-fund 分析：%d 檔 × %d 位分析師 %s (model=%s)",
        len(tickers),
        len(selected_analysts),
        selected_analysts,
        settings.hedge_fund_model,
    )

    try:
        raw = await asyncio.wait_for(
            asyncio.to_thread(_run_sync),
            timeout=settings.hedge_fund_timeout,
        )
    except asyncio.TimeoutError:
        log.error(
            "ai-hedge-fund 分析超時 (%ds)，跳過個股區塊",
            settings.hedge_fund_timeout,
        )
        return []
    except Exception as e:  # noqa: BLE001
        log.exception("ai-hedge-fund 分析失敗：%s — 跳過個股區塊", e)
        return []

    verdicts = _normalize(raw, tickers)
    log.info("✅ ai-hedge-fund 分析完成：%d 份 verdict", len(verdicts))
    return verdicts


# ─── Output normalization ─────────────────────────────────────

def _coerce_confidence(raw: Any) -> float:
    """把 0~1 或 0~100 的 confidence 統一成 0.0~1.0。"""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if v > 1.0:
        v = v / 100.0
    return max(0.0, min(1.0, v))


def _coerce_signal(raw: Any) -> str:
    s = str(raw or "").lower().strip()
    if s in {"bullish", "bearish", "neutral"}:
        return s
    return "neutral"


def _coerce_action(raw: Any) -> str:
    s = str(raw or "").lower().strip()
    if s in {"buy", "sell", "hold", "short", "cover"}:
        return s
    return "hold"


def _extract_reasoning(sig: Any) -> str:
    """ai-hedge-fund 的 reasoning 可能是 str / dict / None。

    各 agent 回傳格式不同：
    - Warren Buffett / 人格型 agent：直接回傳 str
    - Fundamentals：{"profitability_signal": {"signal": "...", "details": "..."}, ...}
    - Sentiment：{"insider_trading": {"signal": "...", "confidence": N, "metrics": {...}}, ...}
    - Technicals：{"trend_following": {"signal": "...", "confidence": N, "metrics": {...}}, ...}
    - Risk Manager：{"error": "..."} 或 {"reasoning": {...}}

    統一把嵌套 dict 壓縮成人類可讀的短摘要。
    """
    if sig is None:
        return ""
    if isinstance(sig, str):
        return sig.strip()
    if not isinstance(sig, dict):
        return str(sig)[:400]

    # 如果有直接的 reasoning key 且是 str，優先用
    r = sig.get("reasoning")
    if isinstance(r, str):
        return r.strip()

    # 如果有 error key（常見於 risk_manager），直接回傳
    if "error" in sig:
        return str(sig["error"])[:200]

    # 嵌套 sub-signal dict：
    # e.g. {"profitability_signal": {"signal": "bullish", "details": "ROE: 143%"}, ...}
    # e.g. {"insider_trading": {"signal": "bearish", "confidence": 62, "metrics": {...}}, ...}
    # e.g. {"trend_following": {"signal": "bullish", "confidence": 70, "metrics": {...}}, ...}
    parts: list[str] = []
    for key, val in sig.items():
        if not isinstance(val, dict):
            continue
        sub_signal = val.get("signal", "")
        label = key.replace("_signal", "").replace("_", " ").strip()
        # 優先用 details（fundamentals 用），否則從 signal 方向 + confidence 組合
        details = val.get("details")
        if isinstance(details, str) and details.strip():
            arrow = {"bullish": "↑", "bearish": "↓"}.get(sub_signal, "→")
            parts.append(f"{label} {arrow} {details.strip()}")
        elif sub_signal:
            arrow = {"bullish": "↑", "bearish": "↓"}.get(sub_signal, "→")
            conf = val.get("confidence")
            conf_str = f" {int(conf)}%" if conf is not None else ""
            parts.append(f"{label}{arrow}{conf_str}")

    if parts:
        return "；".join(parts[:4])

    # fallback: 如果 reasoning 是 dict 但無法解析
    if isinstance(r, dict):
        try:
            return json.dumps(r, ensure_ascii=False)[:300]
        except Exception:  # noqa: BLE001
            return str(r)[:300]

    return ""


# 這些是 ai-hedge-fund 的基礎設施 node，不是真正的分析師
_INFRA_AGENTS = {"risk_management_agent", "portfolio_management_agent", "portfolio_manager"}

# PM 預設回傳「無法操作」的 reasoning 片段（多種可能措辭）
_PM_DEFAULT_REASONING = {"no valid trade", "insufficient data", "no trade available"}


def _is_default_pm_decision(dec: dict[str, Any]) -> bool:
    """判斷 PM 是否回傳了預設/空操作（HOLD + 高信心 + 無實際理由）。"""
    action = str(dec.get("action", "")).lower()
    reasoning = str(dec.get("reasoning", "")).lower()
    conf = dec.get("confidence", 0)
    if action != "hold":
        return False
    if any(frag in reasoning for frag in _PM_DEFAULT_REASONING):
        return True
    # PM 在無法分析時常給 confidence=100
    try:
        if float(conf) >= 95:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _normalize(raw: Any, tickers: list[str]) -> list[TickerVerdict]:
    """把 run_hedge_fund() 的巢狀 dict 壓成 list[TickerVerdict]。"""
    if not isinstance(raw, dict):
        log.warning("ai-hedge-fund 回傳非 dict (%s)，跳過個股區塊", type(raw))
        return []

    decisions = raw.get("decisions") or {}
    analyst_signals = raw.get("analyst_signals") or {}

    # Portfolio Manager 的 decisions 可能是 JSON 字串
    if isinstance(decisions, str):
        try:
            decisions = json.loads(decisions)
        except Exception:  # noqa: BLE001
            decisions = {}

    verdicts: list[TickerVerdict] = []
    for ticker in tickers:
        per_signals: list[AnalystSignal] = []
        if isinstance(analyst_signals, dict):
            for agent_name, ticker_map in analyst_signals.items():
                # 跳過 risk_manager / portfolio_manager 等基礎設施 node
                if agent_name in _INFRA_AGENTS:
                    continue
                if not isinstance(ticker_map, dict):
                    continue
                sig = ticker_map.get(ticker)
                if not sig:
                    continue
                if not isinstance(sig, dict):
                    continue

                reasoning_raw = sig.get("reasoning")
                per_signals.append(
                    AnalystSignal(
                        agent=str(agent_name),
                        signal=_coerce_signal(sig.get("signal")),
                        confidence=_coerce_confidence(sig.get("confidence", 0)),
                        reasoning=_extract_reasoning(reasoning_raw)[:300],
                    )
                )

        dec: dict[str, Any] = {}
        if isinstance(decisions, dict):
            raw_dec = decisions.get(ticker)
            if isinstance(raw_dec, dict):
                dec = raw_dec

        # 如果 PM 回傳的是預設/空操作，用分析師平均信心替代
        is_default = _is_default_pm_decision(dec)
        if is_default and per_signals:
            avg_conf = sum(s.confidence for s in per_signals) / len(per_signals)
            # 多數看多 → buy, 多數看空 → sell, 否則 hold
            bull_count = sum(1 for s in per_signals if s.signal == "bullish")
            bear_count = sum(1 for s in per_signals if s.signal == "bearish")
            if bull_count > bear_count:
                derived_action = "buy"
            elif bear_count > bull_count:
                derived_action = "sell"
            else:
                derived_action = "hold"
            action = derived_action
            confidence = avg_conf
            reasoning = ""
        else:
            action = _coerce_action(dec.get("action"))
            confidence = _coerce_confidence(dec.get("confidence", 0))
            reasoning = _extract_reasoning(dec.get("reasoning"))[:400]

        verdicts.append(
            TickerVerdict(
                ticker=ticker,
                action=action,
                confidence=confidence,
                reasoning=reasoning,
                signals=per_signals,
            )
        )

    return verdicts
