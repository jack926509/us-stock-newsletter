"""
AI 編輯模組 (Editor)

將生成的章節與市場快照組合成結構化輸出。
使用 Anthropic messages.create() + JSON 解析 + Pydantic 強型別驗證。
"""

import json
from datetime import datetime
import pytz
from tenacity import retry, stop_after_attempt, wait_exponential

from app.clients import anthropic_client
from app.config import settings, log
from app.models import Newsletter, TickerVerdict
from app.ai.planner import AIGenerationError


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
        # 每檔最多列 3 個分析師概要，讓 editor 有融合素材
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
    """整合章節內容並輸出符合 Newsletter Pydantic model 的結構。

    若傳入 verdicts，editor 會把個股共識融進 market_summary / insights，
    但 verdicts 欄位本身**不由 LLM 生成**，於回傳前手動覆蓋以避免幻覺。
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
            # 加入 verdicts 區塊後 JSON 可能逼近原本的 2500 tokens 上限，
            # 曾經在 prod 看到 JSONDecodeError: Unterminated string at char 2978。
            # 拉高到 8000 給足裕量，Sonnet 4.6 單次 8k tokens 成本仍可控。
            max_tokens=8000,
            system=(
                f"你是Bloomberg/WSJ風格的主編。整合分析報告為結構化輸出（繁體中文）。\n"
                f"今日日期: {today}\n"
                f"大盤數據: {market_snapshot}\n\n"
                "規則：body/insights 只輸出純文字，不含任何 HTML；"
                "sections 數量恰好符合傳入的章節數；每個 sources 最多 3 筆；"
                "股票代碼用【TICKER】包住；subject 限 15 字內。\n"
                + verdicts_rule
                + "\n必須以 JSON 格式回覆，格式如下：\n"
                '{"subject": "主旨", "market_summary": "大盤摘要", '
                '"sections": [{"title": "...", "body": "...", "sources": [{"title": "...", "url": "..."}]}], '
                '"insights": "投資啟示"}\n'
                "不輸出任何其他文字。"
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
        )
        # 若 Claude 被 max_tokens 截斷，JSON 必定不完整 — 早點放棄讓 tenacity retry
        stop_reason = getattr(resp, "stop_reason", None)
        if stop_reason == "max_tokens":
            log.warning(
                "Editor 輸出被 max_tokens 截斷 (stop_reason=max_tokens)，觸發 retry"
            )
            raise AIGenerationError("Editor output truncated by max_tokens")

        raw = resp.content[0].text.strip()
        # 移除可能的 markdown code block
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()
        data = json.loads(raw)
        newsletter = Newsletter(**data)
        # verdicts 直接覆蓋 — 不讓 LLM 生成，避免幻覺
        newsletter.verdicts = verdicts
        return newsletter

    except Exception as e:
        log.error("Failed to edit newsletter: %s", e)
        raise AIGenerationError(f"Editor error: {e}") from e
