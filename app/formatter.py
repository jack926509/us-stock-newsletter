"""
Slack Block Kit 格式化工具

把 Newsletter 拆成 Block Kit blocks（list[dict]）給 sender 直接呼叫
chat.postMessage(blocks=...) 使用。
"""

import re
from datetime import datetime

from app.config import (
    SLACK_HEADER_TEXT_MAX,
    SLACK_SECTION_TEXT_MAX,
    TICKER_PATTERN,
)

# Regex 用於捕獲 【TICKER】格式（與 watchlist 共用同一 pattern，支援 BRK.B / BRK-B）
_TICKER_RE = re.compile(rf"【({TICKER_PATTERN})】")


def escape_mrkdwn(text: str) -> str:
    """Slack mrkdwn 只需要跳脫 &、<、>（其他字元 Slack 不會拆解）。

    參考：https://api.slack.com/reference/surfaces/formatting#escaping
    """
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def highlight_ticker(body: str) -> str:
    """把 【NVDA】 轉成 Slack 粗體 *NVDA*。"""
    if not body:
        return ""
    return _TICKER_RE.sub(r"*\1*", body)


def get_trend_arrow(change: float) -> str:
    """根據漲跌加上表情符號。"""
    return "📈" if change > 0 else "📉"


# ─── Block builders ──────────────────────────────────────────
# 一律回傳 list[dict]，由 sender 統一組裝送出。

def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _quote_lines(text: str) -> str:
    """每行加上 `>` 變成 Slack blockquote。"""
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))


def _split_for_section(text: str) -> list[str]:
    """Section block text 上限 3000，需要時於段落/換行邊界切割。"""
    if len(text) <= SLACK_SECTION_TEXT_MAX:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > SLACK_SECTION_TEXT_MAX:
        cut = remaining.rfind("\n\n", 0, SLACK_SECTION_TEXT_MAX)
        if cut == -1:
            cut = remaining.rfind("\n", 0, SLACK_SECTION_TEXT_MAX)
        if cut == -1:
            cut = SLACK_SECTION_TEXT_MAX
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def build_header_blocks(subject: str, now: datetime) -> list[dict]:
    """日報主旨區塊：header + 副標。"""
    weekday = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]
    date_str = now.strftime(f"%Y/%m/%d（週{weekday}）")
    title = _truncate(f"📰 美股日報 ── {date_str}", SLACK_HEADER_TEXT_MAX)
    return [
        {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": True}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"🔥 *{escape_mrkdwn(subject)}*",
            },
        },
        {"type": "divider"},
    ]


def build_market_blocks(market: dict, summary: str) -> list[dict]:
    """大盤指數快照區塊。"""
    fields: list[dict] = []
    for sym, v in market.items():
        change_val = float(v.get("change", 0.0))
        arrow = "🟢" if change_val >= 0 else "🔴"
        price_val = float(v.get("price", 0.0))
        name = escape_mrkdwn(v.get("name", sym))
        fields.append({
            "type": "mrkdwn",
            "text": f"{arrow} *{name}*\n`{price_val:,.2f}`  _{change_val:+.2f}%_",
        })

    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "📊 *大盤指數快照*"}},
    ]
    if fields:
        # fields 最多 10 個，本專案最多 3 檔指數，不會爆
        blocks.append({"type": "section", "fields": fields})
    if summary:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": _quote_lines(escape_mrkdwn(summary))},
        })
    return blocks


def build_section_blocks(idx: int, total: int, title: str, body: str, sources: list) -> list[dict]:
    """單個焦點章節區塊。"""
    icons = ["🎯", "🔥", "💡", "📌", "⚡"]
    icon = icons[idx] if idx < len(icons) else "🔹"

    title_text = _truncate(f"{icon} [{idx + 1}/{total}] {title}", SLACK_HEADER_TEXT_MAX)
    body_text = highlight_ticker(escape_mrkdwn(body))
    body_text = _quote_lines(body_text)

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": title_text, "emoji": True}},
    ]
    for chunk in _split_for_section(body_text):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})

    if sources:
        link_parts: list[str] = []
        for i, s in enumerate(sources[:3], 1):
            label = (getattr(s, "title", "") or "來源").strip()
            if len(label) > 30:
                label = label[:29] + "…"
            url = getattr(s, "url", "")
            if url:
                # Slack link format: <url|label>，label 內的 |、>、< 要轉義
                safe_label = escape_mrkdwn(label).replace("|", "｜")
                link_parts.append(f"<{url}|[{i}] {safe_label}>")
        if link_parts:
            blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "🔗 " + "  ·  ".join(link_parts)}
                ],
            })
    return blocks


_ACTION_EMOJI = {
    "buy": "🟢",
    "cover": "🟢",
    "sell": "🔴",
    "short": "🔴",
    "hold": "🟡",
}

_SIGNAL_EMOJI = {
    "bullish": "⬆️",
    "bearish": "⬇️",
    "neutral": "➡️",
}

_AGENT_DISPLAY = {
    "warren_buffett": "Warren Buffett",
    "peter_lynch": "Peter Lynch",
    "charlie_munger": "Charlie Munger",
    "stanley_druckenmiller": "Druckenmiller",
    "ben_graham": "Ben Graham",
    "bill_ackman": "Bill Ackman",
    "cathie_wood": "Cathie Wood",
    "michael_burry": "Michael Burry",
    "phil_fisher": "Phil Fisher",
    "mohnish_pabrai": "Mohnish Pabrai",
    "nassim_taleb": "Nassim Taleb",
    "aswath_damodaran": "Aswath Damodaran",
    "rakesh_jhunjhunwala": "Rakesh Jhunjhunwala",
    "fundamentals": "基本面分析",
    "fundamentals_analyst_agent": "基本面分析",
    "technicals": "技術面分析",
    "technical_analyst_agent": "技術面分析",
    "sentiment": "市場情緒",
    "sentiment_analyst_agent": "市場情緒",
    "news_sentiment": "新聞情緒",
    "news_sentiment_agent": "新聞情緒",
    "valuation": "估值分析",
    "valuation_analyst_agent": "估值分析",
    "growth_agent": "成長分析",
    "growth_analyst_agent": "成長分析",
    "risk_management_agent": "風控評估",
    "portfolio_management_agent": "組合管理",
}


def _agent_display(agent: str) -> str:
    """把 ai-hedge-fund 的內部 key 轉成顯示名稱。"""
    key = (agent or "").lower().strip()
    if key in _AGENT_DISPLAY:
        return _AGENT_DISPLAY[key]
    stripped = re.sub(r"_(analyst_)?agent$", "", key)
    if stripped in _AGENT_DISPLAY:
        return _AGENT_DISPLAY[stripped]
    return (agent or "").replace("_", " ").title()


def build_verdicts_blocks(verdicts: list) -> list[dict]:
    """AI 多分析師個股共識區塊。空清單回傳空 list。"""
    if not verdicts:
        return []

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": "📊 AI 分析師個股共識", "emoji": True}},
    ]

    for v in verdicts:
        ticker = escape_mrkdwn(getattr(v, "ticker", ""))
        action = str(getattr(v, "action", "hold")).lower()
        emoji = _ACTION_EMOJI.get(action, "🟡")
        conf = int(round(float(getattr(v, "confidence", 0.0)) * 100))
        action_upper = escape_mrkdwn(action.upper())

        header_line = f"{emoji} *{ticker}*  ·  {action_upper}  ·  信心 {conf}%"

        body_lines: list[str] = []
        reasoning = (getattr(v, "reasoning", "") or "").strip()
        if reasoning:
            body_lines.append(_quote_lines(escape_mrkdwn(reasoning[:280])))

        signals = getattr(v, "signals", []) or []
        for s in signals[:4]:
            s_signal = str(getattr(s, "signal", "neutral")).lower()
            s_emoji = _SIGNAL_EMOJI.get(s_signal, "➡️")
            name = _agent_display(getattr(s, "agent", ""))
            s_conf = int(round(float(getattr(s, "confidence", 0.0)) * 100))
            reason = (getattr(s, "reasoning", "") or "").strip()
            line = f"{s_emoji} _{escape_mrkdwn(name)}_ {s_conf}%"
            if reason:
                line += f"：{escape_mrkdwn(reason[:80])}"
            body_lines.append(line)

        text = header_line + ("\n" + "\n".join(body_lines) if body_lines else "")
        for chunk in _split_for_section(text):
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
        blocks.append({"type": "divider"})

    # 最後一個 divider 拿掉，視覺乾淨些
    if blocks and blocks[-1].get("type") == "divider":
        blocks.pop()
    return blocks


def build_footer_blocks(insights: str) -> list[dict]:
    """投資啟示 + 免責聲明區塊。"""
    insights_text = highlight_ticker(escape_mrkdwn(insights or ""))
    blocks: list[dict] = [
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "🧭 *投資啟示與風險提醒*"}},
    ]
    if insights_text:
        for chunk in _split_for_section(_quote_lines(insights_text)):
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": "⚠️ 本文僅供參考，不構成投資建議，入市須謹慎。"},
            {"type": "mrkdwn", "text": "🤖 Powered by AI Agent"},
        ],
    })
    return blocks
