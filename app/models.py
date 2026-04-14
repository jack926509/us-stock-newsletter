"""
Pydantic 資料模型 / Schema 驗證

用於 Anthropic messages.parse() 的結構化輸出驗證，
確保 AI 回傳格式正確，避免格式不符導致後續流程崩潰。
"""

from typing import Literal

from pydantic import BaseModel, Field


class NewsletterPlan(BaseModel):
    """OpenAI Planning 步驟的輸出格式。"""
    title: str = Field(..., description="日報主標題")
    topics: list[str] = Field(..., min_length=1, max_length=5, description="焦點主題列表")


class Source(BaseModel):
    """文章來源。"""
    title: str = Field(default="查看原文", description="來源標題")
    url: str = Field(default="", description="來源 URL")


class Section(BaseModel):
    """日報章節。"""
    title: str = Field(..., description="章節標題")
    body: str = Field(..., description="章節正文（純文字）")
    sources: list[Source] = Field(default_factory=list, max_length=3, description="消息來源")


# ─── ai-hedge-fund 個股分析結構 ───────────────────────────────

class AnalystSignal(BaseModel):
    """單一分析師對單一 ticker 的觀點。"""
    agent: str = Field(..., description="分析師識別（如 warren_buffett）")
    signal: Literal["bullish", "bearish", "neutral"] = Field(default="neutral")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = Field(default="", description="分析師的簡短理由")


class TickerVerdict(BaseModel):
    """Portfolio Manager 對單一 ticker 的最終判斷 + 所有分析師原始 signal。"""
    ticker: str = Field(..., description="股票代號")
    action: Literal["buy", "sell", "hold", "short", "cover"] = Field(default="hold")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = Field(default="", description="Portfolio Manager 綜合理由")
    signals: list[AnalystSignal] = Field(default_factory=list)


class Newsletter(BaseModel):
    """最終整合的電子報結構。"""
    subject: str = Field(..., description="日報主旨")
    market_summary: str = Field(default="", description="大盤情緒一句話摘要")
    sections: list[Section] = Field(..., min_length=1, max_length=5, description="章節列表")
    verdicts: list[TickerVerdict] = Field(
        default_factory=list,
        description="個股 AI 分析師共識（由 ai-hedge-fund 產出，editor 不生成此欄位）",
    )
    insights: str = Field(default="", description="投資啟示與風險提醒")
