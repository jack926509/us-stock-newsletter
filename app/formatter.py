"""
HTML 格式化工具

用於轉換資料成可供 Telegram HTML Parse Mode 呈現的文字。
"""

import re
from datetime import datetime

# Regex 用於捕獲 【TICKER】格式
_TICKER_RE = re.compile(r'【([A-Z]{1,5})】')


def escape_html(text: str) -> str:
    """跳脫 HTML 特殊字元防止 Telegram 解析錯誤。"""
    if not text:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def highlight_ticker(body: str) -> str:
    """轉換 【NVDA】 標記為 Telegram 加粗標籤 <b>NVDA</b>。"""
    if not body:
        return ""
    return _TICKER_RE.sub(r'<b>\1</b>', body)


def get_trend_arrow(change: float) -> str:
    """根據漲跌加上表情符號。"""
    return "📈" if change >= 0 else "📉"


def build_header(subject: str, now: datetime) -> str:
    """產生開頭主旨段落（精簡單行標題）。"""
    weekday = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]
    date_str = now.strftime(f"%Y/%m/%d（週{weekday}）")
    return (
        f"📰 <b>美股日報</b> ── {date_str}\n\n"
        f"🔥 <b>{escape_html(subject)}</b>"
    )


def build_market_card(market: dict, summary: str) -> str:
    """產生大盤快照段落。"""
    lines = ["📊 <b>大盤指數快照</b>", ""]

    for sym, v in market.items():
        change_val = v.get("change", 0.0)
        arrow = "🟢" if change_val >= 0 else "🔴"
        change_str = f"{change_val:+.2f}%"
        price_val = v.get("price", 0.0)

        name = escape_html(v.get('name', sym))
        lines.append(
            f"{arrow} <b>{name}</b>  <code>{price_val:,.2f}</code>  <i>{change_str}</i>"
        )

    if summary:
        lines += ["", f"<blockquote>{escape_html(summary)}</blockquote>"]

    return "\n".join(lines)


def build_section_block(idx: int, total: int, title: str, body: str, sources: list) -> str:
    """產生單個章節段落（含進度編號）。"""
    icons = ["🎯", "🔥", "💡", "📌", "⚡"]
    icon = icons[idx] if idx < len(icons) else "🔹"

    title_safe = escape_html(title)
    body_safe = highlight_ticker(escape_html(body))

    text = f"{icon} <b>[{idx + 1}/{total}] {title_safe}</b>\n<blockquote>{body_safe}</blockquote>"

    if sources:
        links = []
        for i, s in enumerate(sources[:3], 1):
            label = escape_html(getattr(s, "title", "來源")).strip()
            if len(label) > 30:
                label = label[:29] + ".."

            url = getattr(s, "url", "")
            if url:
                links.append(f'<a href="{url}">[{i}] {label}</a>')

        if links:
            text += "\n🔗 " + " · ".join(links)

    return text


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
    key = agent.lower().strip()
    if key in _AGENT_DISPLAY:
        return _AGENT_DISPLAY[key]
    # 去掉 _agent / _analyst_agent 後綴再找一次
    stripped = re.sub(r"_(analyst_)?agent$", "", key)
    if stripped in _AGENT_DISPLAY:
        return _AGENT_DISPLAY[stripped]
    return agent.replace("_", " ").title()


def build_verdicts_card(verdicts: list) -> str:
    """產生 AI 分析師個股共識區塊。

    接受 list[TickerVerdict]（避免 import 循環，型別不強制）。
    空 list 時回傳空字串，讓 sender 自動略過。
    """
    if not verdicts:
        return ""

    lines = ["📊 <b>AI 分析師個股共識</b>", ""]
    for v in verdicts:
        ticker = escape_html(getattr(v, "ticker", ""))
        action = str(getattr(v, "action", "hold")).lower()
        emoji = _ACTION_EMOJI.get(action, "🟡")
        conf = int(round(float(getattr(v, "confidence", 0.0)) * 100))
        action_upper = escape_html(action.upper())

        # 標題行：AAPL · BUY · 信心 72%
        lines.append(f"{emoji} <b>{ticker}</b> · {action_upper} · 信心 {conf}%")

        # PM 的綜合理由（若有實際內容才顯示）
        reasoning = (getattr(v, "reasoning", "") or "").strip()
        if reasoning:
            lines.append(f"<blockquote>{escape_html(reasoning[:200])}</blockquote>")

        # 各分析師的訊號（精簡版）
        signals = getattr(v, "signals", []) or []
        if signals:
            sig_parts: list[str] = []
            for s in signals[:4]:
                s_signal = str(getattr(s, "signal", "neutral")).lower()
                s_emoji = _SIGNAL_EMOJI.get(s_signal, "➡️")
                name = _agent_display(getattr(s, "agent", ""))
                s_conf = int(round(float(getattr(s, "confidence", 0.0)) * 100))
                reason = (getattr(s, "reasoning", "") or "").strip()

                # 組裝每位分析師的單行
                line = f"{s_emoji} <i>{escape_html(name)}</i> {s_conf}%"
                if reason:
                    # 截取前 80 字元的摘要
                    short_reason = escape_html(reason[:80])
                    line += f"：{short_reason}"
                sig_parts.append(line)

            lines.append("<blockquote>" + "\n".join(sig_parts) + "</blockquote>")

        lines.append("")

    return "\n".join(lines).rstrip()


def build_footer(insights: str) -> str:
    """產生底部免責與投資啟示。"""
    insights_safe = highlight_ticker(escape_html(insights))
    return (
        f"🧭 <b>投資啟示與風險提醒</b>\n<blockquote>{insights_safe}</blockquote>\n\n"
        f"─────────────────────\n"
        f"<i><tg-spoiler>⚠️ 免責聲明：本文內容僅供參考，不構成投資建議，入市須謹慎。</tg-spoiler></i>\n"
        f"🤖 <i>Powered by AI Agent</i>"
    )
