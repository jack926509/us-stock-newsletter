"""
AI 編輯模組 (Editor)

將章節草稿與市場快照整合為結構化日報。
使用 Anthropic tool-use 強制 schema-valid 輸出（NewsletterDraft），
之後在 Python 端合成最終 Newsletter（注入 verdicts 避免 LLM 幻覺）。
"""

from datetime import datetime
import pytz
from tenacity import retry, stop_after_attempt, wait_exponential

from app.ai.errors import AIGenerationError
from app.clients import anthropic_client
from app.config import settings, log
from app.models import Newsletter, NewsletterDraft, TickerVerdict

_EDITOR_TOOL = {
    "name": "submit_newsletter",
    "description": "輸出整合後的美股日報結構（subject / market_summary / sections / insights）。",
    "input_schema": NewsletterDraft.model_json_schema(),
}


def _format_verdicts_context(verdicts: list[TickerVerdict]) -> str:
    """把 verdicts 轉成 Editor 的 user prompt 上下文。"""
    if not verdicts:
        return ""
    lines = ["\n【AI 分析師對自選股的共識】"]
    for v in verdicts:
        conf_pct = int(round(v.confidence * 100))
        lines.append(f"- {v.ticker} — {v.action.upper()} (信心 {conf_pct}%)")
        if v.reasoning:
            lines.append(f"  綜合理由：{v.reasoning[:200]}")
        for s in v.signals[:3]:
            s_conf = int(round(s.confidence * 100))
            lines.append(
                f"  · {s.agent}（{s.signal}，{s_conf}%）：{s.reasoning[:120]}"
            )
    return "\n".join(lines)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=3, max=15))
async def edit_newsletter(
    title: str,
    sections: list,
    market: dict,
    verdicts: list[TickerVerdict] | None = None,
) -> Newsletter:
    """整合章節內容並輸出 Newsletter。

    若傳入 verdicts，editor 會把個股共識融進 market_summary / insights，
    但 verdicts 欄位本身**不由 LLM 生成**，於回傳前由 pipeline 注入。
    """
    verdicts = verdicts or []
    today = datetime.now(pytz.timezone(settings.timezone)).strftime("%Y-%m-%d")
    sections_text = "\n\n\n".join(sections)

    market_snapshot = " | ".join([
        f"{v['name']}: {v['price']:.2f} ({v['change']:+.2f}%)"
        for v in market.values()
    ])

    verdicts_context = _format_verdicts_context(verdicts)
    verdicts_rule = (
        "若提供【AI 分析師對自選股的共識】，請在 market_summary 點出 1–2 檔最高信心個股的方向，"
        "並在 insights 結合個股觀點給出整體風險提醒（但不要逐字重複共識列表）。\n"
        if verdicts
        else ""
    )

    try:
        resp = await anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8000,
            system=(
                f"你是 Bloomberg/WSJ 風格的主編，整合分析報告為結構化輸出（繁體中文）。\n"
                f"今日日期: {today}\n"
                f"大盤數據: {market_snapshot}\n\n"
                "規則：body/insights 只輸出純文字，不含任何 HTML；"
                "sections 數量恰好符合傳入的章節數；每個 sources 最多 3 筆；"
                "股票代碼用【TICKER】包住；subject 限 15 字內。\n"
                + verdicts_rule
                + "請呼叫 submit_newsletter 工具回傳結果。"
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"主標題: {title}\n\n所有章節內容:\n{sections_text}"
                        + verdicts_context
                    ),
                }
            ],
            tools=[_EDITOR_TOOL],
            tool_choice={"type": "tool", "name": "submit_newsletter"},
        )

        if getattr(resp, "stop_reason", None) == "max_tokens":
            log.warning("Editor 輸出被 max_tokens 截斷，觸發 retry")
            raise AIGenerationError("Editor output truncated by max_tokens")

        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                draft = NewsletterDraft(**block.input)
                return Newsletter(
                    subject=draft.subject,
                    market_summary=draft.market_summary,
                    sections=draft.sections,
                    insights=draft.insights,
                    verdicts=verdicts,
                )

        raise AIGenerationError("Editor 未回傳 tool_use 區塊")

    except AIGenerationError:
        raise
    except Exception as e:
        log.error("Failed to edit newsletter: %s", e)
        raise AIGenerationError(f"Editor error: {e}") from e
