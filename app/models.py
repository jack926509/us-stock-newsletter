"""
Pydantic 資料模型 / Schema 驗證

用於驗證 OpenAI 回傳的 JSON 結構，避免格式不符時導致整個流程崩潰。
"""

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


class Newsletter(BaseModel):
    """最終整合的電子報結構。"""
    subject: str = Field(..., description="日報主旨")
    market_summary: str = Field(default="", description="大盤情緒一句話摘要")
    sections: list[Section] = Field(..., min_length=1, max_length=5, description="章節列表")
    insights: str = Field(default="", description="投資啟示與風險提醒")
