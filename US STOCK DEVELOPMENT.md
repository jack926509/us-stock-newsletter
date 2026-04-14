# 美股 AI 多維分析 Telegram Bot — 開發文件

> **版本**：v1.0  
> **最後更新**：2026-04-14  
> **技術棧**：Python 3.11 / FastAPI · TypeScript / Node.js 20 · LangGraph · Telegram Bot API  
> **部署平台**：Zeabur  
> **核心參考**：[ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) · [banini-tracker](https://github.com/hansai-art/8zz-banini-tracker)

---

## 目錄

1. [專案概覽](#1-專案概覽)
2. [系統架構](#2-系統架構)
3. [Repo 結構](#3-repo-結構)
4. [後端 API（Python FastAPI）](#4-後端-api)
5. [Agent 設計](#5-agent-設計)
6. [Telegram Bot（TypeScript）](#6-telegram-bot)
7. [資料流與排程](#7-資料流與排程)
8. [環境變數](#8-環境變數)
9. [本地開發](#9-本地開發)
10. [Zeabur 部署](#10-zeabur-部署)
11. [快取策略（Redis）](#11-快取策略)
12. [錯誤處理與限流](#12-錯誤處理與限流)
13. [開發時程](#13-開發時程)
14. [API 成本估算](#14-api-成本估算)
15. [未來擴充計畫](#15-未來擴充計畫)

---

## 1. 專案概覽

### 功能目標

| 功能 | 說明 |
|------|------|
| 每日早報 | 美東 09:30 開盤前 30 分鐘，推送大盤情緒 + 自選股 AI 多角度觀點 |
| 每日晚報 | 美東 16:00 收盤後 30 分鐘，推送收盤技術面 + 基本面總結 |
| 週報 | 每週五收盤後推送週績效 + 下週重點事件 |
| 即時分析 | 用戶 `/analyze AAPL` 觸發單股完整分析，60 秒內回傳 |
| 自選股管理 | `/add` `/remove` `/watchlist` 管理個人追蹤清單 |
| 大盤情緒 | `/sentiment` 查詢恐懼貪婪指數 + VIX |

### 免責聲明

本系統僅供資訊參考與研究用途，不構成任何投資建議。所有分析由 AI 生成，不保證準確性。投資有風險，請自行評估判斷。

---

## 2. 系統架構

```
┌─────────────────────────────────────────────────────────────┐
│                        Zeabur Cloud                          │
│                                                              │
│   ┌───────────────┐      HTTP      ┌──────────────────────┐  │
│   │  bot service  │ ─────────────► │  backend service     │  │
│   │  TypeScript   │                │  Python FastAPI      │  │
│   │  Node.js 20   │ ◄───────────── │  :8000               │  │
│   │               │    JSON resp   │                      │  │
│   └───────┬───────┘                └──────────┬───────────┘  │
│           │                                   │              │
│           │ Telegram API                      │ LangGraph    │
│           │                        ┌──────────┴──────────┐  │
│           │                        │     AI Agents        │  │
│           ▼                        │  Buffett / Lynch /   │  │
│   ┌───────────────┐                │  Fundamentals /      │  │
│   │  Redis cache  │ ◄──────────── │  Technicals /        │  │
│   │  (TTL 4hr)    │                │  Risk Manager        │  │
│   └───────────────┘                └──────────┬──────────┘  │
│                                               │              │
└───────────────────────────────────────────────┼─────────────┘
                                                │
                        ┌───────────────────────┼────────────┐
                        │   External APIs        │            │
                        │                        ▼            │
                        │  ┌─────────────┐  ┌───────────┐   │
                        │  │ Financial   │  │  OpenAI   │   │
                        │  │ Datasets API│  │  GPT-4o   │   │
                        │  └─────────────┘  └───────────┘   │
                        │  ┌─────────────┐                   │
                        │  │  yfinance   │  (免費，無需 key)  │
                        │  └─────────────┘                   │
                        └────────────────────────────────────┘
```

### 服務說明

| 服務 | 角色 | 語言 | Port |
|------|------|------|------|
| `backend` | 分析引擎 API | Python 3.11 | 8000 |
| `bot` | Telegram Bot + cron 排程 | TypeScript | — |
| `redis` | 分析結果快取 + 自選股儲存 | — | 6379 |

---

## 3. Repo 結構

```
us-stock-bot/
├── backend/                          # Python FastAPI 分析引擎
│   ├── agents/                       # AI 分析 Agent（fork ai-hedge-fund）
│   │   ├── __init__.py
│   │   ├── buffett.py                # Warren Buffett — 價值投資
│   │   ├── lynch.py                  # Peter Lynch — 成長股
│   │   ├── munger.py                 # Charlie Munger — 品質生意
│   │   ├── druckenmiller.py          # Stanley Druckenmiller — 總體宏觀
│   │   ├── fundamentals.py           # 基本面數據分析
│   │   ├── technicals.py             # 技術指標分析
│   │   ├── sentiment.py              # 市場情緒分析
│   │   └── risk_manager.py           # 風控與部位建議
│   ├── tools/                        # 資料抓取工具
│   │   ├── __init__.py
│   │   ├── financial_datasets.py     # Financial Datasets API 封裝
│   │   └── yfinance_tool.py          # yfinance 免費資料
│   ├── main.py                       # FastAPI entrypoint
│   ├── graph.py                      # LangGraph workflow 組裝
│   ├── report.py                     # 格式化輸出 → Telegram Markdown
│   ├── cache.py                      # Redis 快取操作
│   ├── models.py                     # Pydantic 請求/回應模型
│   └── requirements.txt
│
├── bot/                              # TypeScript Telegram Bot
│   ├── src/
│   │   ├── index.ts                  # Bot entrypoint + cron 排程
│   │   ├── commands.ts               # 所有指令處理器
│   │   ├── api.ts                    # 呼叫 backend API
│   │   ├── formatter.ts              # Telegram MarkdownV2 格式化
│   │   ├── watchlist.ts              # 自選股 CRUD（Redis）
│   │   └── types.ts                  # TypeScript 型別定義
│   ├── package.json
│   └── tsconfig.json
│
├── docker/
│   ├── backend.Dockerfile
│   └── bot.Dockerfile
│
├── docker-compose.yml                # 本地開發用
├── .env.example                      # 環境變數範本
└── README.md
```

---

## 4. 後端 API

### 4.1 端點設計

| 端點 | 方法 | 說明 |
|------|------|------|
| `POST /analyze` | POST | 觸發單股多 Agent 分析 |
| `POST /report/daily` | POST | 觸發每日批次報告（排程呼叫） |
| `GET /market/sentiment` | GET | 大盤恐懼貪婪指數 + VIX |
| `GET /agents` | GET | 列出可用 Agent 清單 |
| `GET /health` | GET | Zeabur 健康檢查 |

### 4.2 Pydantic 模型

```python
# backend/models.py

from pydantic import BaseModel
from typing import List, Optional, Literal
from datetime import date

class AnalyzeRequest(BaseModel):
    ticker: str                              # e.g. "AAPL"
    agents: List[str] = [
        "buffett", "lynch", "fundamentals",
        "technicals", "sentiment", "risk_manager"
    ]
    start_date: Optional[str] = "2024-01-01"
    end_date: Optional[str] = None           # 預設今日
    include_sentiment: bool = True

class AgentSignal(BaseModel):
    agent: str                               # "warren_buffett"
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: float                        # 0.0 ~ 1.0
    reasoning: str                           # 分析說明（中文輸出）

class AnalyzeResponse(BaseModel):
    ticker: str
    analysis_date: str
    price: Optional[float]                   # 當前價格
    signals: List[AgentSignal]
    risk_level: Literal["low", "medium", "high"]
    position_suggestion: Literal["long", "short", "hold"]
    position_size: float                     # 建議部位比例 0.0~1.0
    summary: str                             # 一段話總結
    cached: bool = False                     # 是否來自快取

class DailyReportRequest(BaseModel):
    report_type: Literal["morning", "evening", "weekly"]
    tickers: List[str]                       # 自選股清單
```

### 4.3 FastAPI 主程式

```python
# backend/main.py

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import redis.asyncio as redis
from .models import AnalyzeRequest, AnalyzeResponse, DailyReportRequest
from .graph import run_analysis
from .cache import get_cached, set_cache
import os

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化 Redis 連線池
    app.state.redis = redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379"),
        decode_responses=True
    )
    yield
    await app.state.redis.close()

app = FastAPI(title="US Stock AI API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest, request: Request):
    # 嘗試從 Redis 讀快取
    cache_key = f"analyze:{req.ticker}:{date.today().isoformat()}"
    cached = await get_cached(request.app.state.redis, cache_key)
    if cached:
        return {**cached, "cached": True}

    # 執行 LangGraph 分析流程
    try:
        result = await run_analysis(
            ticker=req.ticker,
            agents=req.agents,
            start_date=req.start_date,
            end_date=req.end_date,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # 寫入快取 TTL = 4 小時
    await set_cache(request.app.state.redis, cache_key, result, ttl=14400)
    return result

@app.post("/report/daily")
async def daily_report(req: DailyReportRequest, background_tasks: BackgroundTasks):
    # 非同步背景執行，避免 timeout
    background_tasks.add_task(run_batch_report, req.report_type, req.tickers)
    return {"status": "queued", "tickers": req.tickers, "type": req.report_type}

@app.get("/market/sentiment")
async def market_sentiment():
    # 直接從 yfinance 抓 VIX + 計算技術指標
    from .tools.yfinance_tool import get_market_overview
    return await get_market_overview()
```

### 4.4 LangGraph Workflow

```python
# backend/graph.py

from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from typing import TypedDict, Annotated
import operator
import os

class AgentState(TypedDict):
    ticker: str
    start_date: str
    end_date: str
    messages: Annotated[list, operator.add]
    agent_signals: Annotated[list, operator.add]
    risk_assessment: dict
    final_signal: str

def create_analysis_graph(active_agents: list[str]) -> StateGraph:
    llm = ChatOpenAI(
        model="gpt-4o",
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0,
    )
    workflow = StateGraph(AgentState)

    # 動態加入 Agent 節點
    agent_map = {
        "buffett":        buffett_agent,
        "lynch":          lynch_agent,
        "munger":         munger_agent,
        "druckenmiller":  druckenmiller_agent,
        "fundamentals":   fundamentals_agent,
        "technicals":     technicals_agent,
        "sentiment":      sentiment_agent,
    }
    for name in active_agents:
        if name in agent_map:
            workflow.add_node(name, agent_map[name](llm))

    # 所有 Agent 完成後進入 Risk Manager，最後輸出
    workflow.add_node("risk_manager", risk_manager_agent(llm))
    workflow.add_node("portfolio_manager", portfolio_manager_agent(llm))

    # 設定邊：所有 agent → risk_manager → portfolio_manager → END
    workflow.set_entry_point(active_agents[0])
    for i in range(len(active_agents) - 1):
        workflow.add_edge(active_agents[i], active_agents[i + 1])
    workflow.add_edge(active_agents[-1], "risk_manager")
    workflow.add_edge("risk_manager", "portfolio_manager")
    workflow.add_edge("portfolio_manager", END)

    return workflow.compile()

async def run_analysis(ticker: str, agents: list, start_date: str, end_date: str) -> dict:
    graph = create_analysis_graph(agents)
    state = await graph.ainvoke({
        "ticker": ticker,
        "start_date": start_date,
        "end_date": end_date or date.today().isoformat(),
        "messages": [],
        "agent_signals": [],
    })
    return format_output(state)
```

---

## 5. Agent 設計

### 5.1 採用的 Agent 清單

| Agent | 策略風格 | 分析重點 | 資料來源 |
|-------|----------|----------|----------|
| Warren Buffett | 價值投資 | 護城河、ROE、現金流、長期持有 | Financial Datasets |
| Peter Lynch | 成長股獵人 | PEG ratio、盈餘成長、業務理解 | Financial Datasets |
| Charlie Munger | 品質生意 | 高品質企業 + 合理估值 | Financial Datasets |
| Stanley Druckenmiller | 宏觀趨勢 | 不對稱機會、趨勢動能 | yfinance + 新聞 |
| Fundamentals Agent | 基本面 | 財務三表、估值比率 | Financial Datasets |
| Technicals Agent | 技術面 | RSI、MACD、均線、成交量 | yfinance |
| Sentiment Agent | 市場情緒 | 新聞情緒分析、社群觀點 | yfinance 新聞 |
| Risk Manager | 風控 | 波動率、下行風險、部位上限 | yfinance |

### 5.2 Agent 輸出格式

每個 Agent 需要輸出標準化 Signal 物件，以利 Portfolio Manager 綜合判斷：

```python
# backend/agents/buffett.py（節錄核心邏輯）

from langchain_core.messages import HumanMessage
from ..tools.financial_datasets import get_financial_metrics, get_income_statement

BUFFETT_SYSTEM_PROMPT = """
你是 Warren Buffett，世界頂尖的價值投資人。分析股票時請聚焦：
1. 護城河（品牌、網路效應、轉換成本、成本優勢）
2. 管理層品質與資本配置能力
3. 財務健全度：ROE > 15%、低負債、穩定現金流
4. 安全邊際：相對內在價值是否有折扣

輸出格式（JSON）：
{{
  "signal": "bullish|bearish|neutral",
  "confidence": 0.0~1.0,
  "reasoning": "中文分析，100字以內"
}}
"""

def buffett_agent(llm):
    async def analyze(state: AgentState) -> AgentState:
        ticker = state["ticker"]

        # 抓取財務數據
        metrics = await get_financial_metrics(ticker)
        income = await get_income_statement(ticker)

        prompt = f"""
        股票代號：{ticker}
        財務摘要：
        - P/E: {metrics.get('pe_ratio')}
        - ROE: {metrics.get('return_on_equity')}
        - 負債比率: {metrics.get('debt_to_equity')}
        - 自由現金流: {metrics.get('free_cash_flow')}
        - 5年EPS成長率: {metrics.get('eps_growth_5y')}

        請依照你的投資哲學分析此股票。
        """
        response = await llm.ainvoke([
            {"role": "system", "content": BUFFETT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ])

        import json
        signal = json.loads(response.content)
        signal["agent"] = "warren_buffett"

        return {
            **state,
            "agent_signals": state["agent_signals"] + [signal]
        }
    return analyze
```

### 5.3 Risk Manager 邏輯

```python
# backend/agents/risk_manager.py（節錄）

def risk_manager_agent(llm):
    async def assess_risk(state: AgentState) -> AgentState:
        ticker = state["ticker"]
        signals = state["agent_signals"]

        # 計算 Agent 共識
        bullish_count = sum(1 for s in signals if s["signal"] == "bullish")
        bearish_count = sum(1 for s in signals if s["signal"] == "bearish")
        avg_confidence = sum(s["confidence"] for s in signals) / len(signals)

        # 從 yfinance 取得波動率數據
        import yfinance as yf
        stock = yf.Ticker(ticker)
        hist = stock.history(period="3mo")
        volatility = hist["Close"].pct_change().std() * (252 ** 0.5)  # 年化波動率

        # 部位建議（基於共識強度 × 波動率反比）
        consensus_strength = (bullish_count - bearish_count) / len(signals)
        base_size = max(0, consensus_strength) * avg_confidence
        vol_adj_size = base_size * (0.15 / max(volatility, 0.15))  # 以 15% 波動率為基準
        position_size = min(vol_adj_size, 0.10)  # 最高 10% 投組

        # 風險等級
        if volatility > 0.40 or avg_confidence < 0.5:
            risk_level = "high"
        elif volatility > 0.25 or avg_confidence < 0.65:
            risk_level = "medium"
        else:
            risk_level = "low"

        return {
            **state,
            "risk_assessment": {
                "risk_level": risk_level,
                "position_size": round(position_size, 3),
                "volatility_annual": round(volatility, 3),
                "consensus_strength": round(consensus_strength, 3),
            }
        }
    return assess_risk
```

---

## 6. Telegram Bot

### 6.1 指令清單

| 指令 | 參數 | 說明 |
|------|------|------|
| `/start` | — | 歡迎訊息 + 指令說明 |
| `/analyze` | `<TICKER>` | 觸發完整多 Agent 分析 |
| `/watchlist` | — | 顯示自選股清單與昨日訊號 |
| `/add` | `<TICKER>` | 加入自選股 |
| `/remove` | `<TICKER>` | 移除自選股 |
| `/report` | `morning\|evening` | 手動觸發報告 |
| `/sentiment` | — | 大盤恐懼貪婪指數 + VIX |
| `/status` | — | Bot 運行狀態 + 上次排程時間 |
| `/help` | — | 完整說明 |

### 6.2 Bot 主程式與排程

```typescript
// bot/src/index.ts

import TelegramBot from 'node-telegram-bot-api'
import cron from 'node-cron'
import { handleAnalyze, handleWatchlist, handleAdd, handleRemove,
         handleReport, handleSentiment, handleStatus } from './commands'

const bot = new TelegramBot(process.env.TG_BOT_TOKEN!, { polling: true })

// ── 指令處理器 ──────────────────────────────────────────────
bot.onText(/\/analyze (.+)/, (msg, match) => handleAnalyze(bot, msg, match![1]))
bot.onText(/\/watchlist/, (msg) => handleWatchlist(bot, msg))
bot.onText(/\/add (.+)/, (msg, match) => handleAdd(bot, msg, match![1]))
bot.onText(/\/remove (.+)/, (msg, match) => handleRemove(bot, msg, match![1]))
bot.onText(/\/report (.+)/, (msg, match) => handleReport(bot, msg, match![1]))
bot.onText(/\/sentiment/, (msg) => handleSentiment(bot, msg))
bot.onText(/\/status/, (msg) => handleStatus(bot, msg))

// ── cron 排程（美東時區 UTC-4 夏令 / UTC-5 冬令）────────────

// 早報：美東 09:00（UTC 13:00）週一～五
cron.schedule('0 13 * * 1-5', async () => {
  console.log('[cron] 觸發早報')
  await triggerScheduledReport('morning')
}, { timezone: 'UTC' })

// 晚報：美東 16:30（UTC 20:30）週一～五
cron.schedule('30 20 * * 1-5', async () => {
  console.log('[cron] 觸發晚報')
  await triggerScheduledReport('evening')
}, { timezone: 'UTC' })

// 週報：週五美東 17:00（UTC 21:00）
cron.schedule('0 21 * * 5', async () => {
  console.log('[cron] 觸發週報')
  await triggerScheduledReport('weekly')
}, { timezone: 'UTC' })

async function triggerScheduledReport(type: 'morning' | 'evening' | 'weekly') {
  const channelId = process.env.TG_CHANNEL_ID!
  const tickers = await getWatchlistAll()  // 取出所有用戶自選股聯集

  if (tickers.length === 0) {
    tickers.push(...(process.env.DEFAULT_WATCHLIST?.split(',') ?? ['AAPL', 'NVDA', 'MSFT']))
  }

  await bot.sendMessage(channelId, `⏳ *${type === 'morning' ? '早報' : type === 'evening' ? '晚報' : '週報'}* 分析中，請稍候...`, {
    parse_mode: 'MarkdownV2'
  })

  const { analyzeAll } = await import('./api')
  const results = await analyzeAll(tickers)
  const { formatDailyReport } = await import('./formatter')
  const message = formatDailyReport(type, results)

  await bot.sendMessage(channelId, message, { parse_mode: 'MarkdownV2' })
}
```

### 6.3 API 呼叫層

```typescript
// bot/src/api.ts

const BACKEND_URL = process.env.BACKEND_URL ?? 'http://localhost:8000'

export interface AnalyzeResponse {
  ticker: string
  analysis_date: string
  price: number | null
  signals: AgentSignal[]
  risk_level: 'low' | 'medium' | 'high'
  position_suggestion: 'long' | 'short' | 'hold'
  position_size: number
  summary: string
  cached: boolean
}

export interface AgentSignal {
  agent: string
  signal: 'bullish' | 'bearish' | 'neutral'
  confidence: number
  reasoning: string
}

export async function analyzeStock(ticker: string): Promise<AnalyzeResponse> {
  const res = await fetch(`${BACKEND_URL}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ticker: ticker.toUpperCase(),
      agents: ['buffett', 'lynch', 'fundamentals', 'technicals', 'sentiment', 'risk_manager'],
      include_sentiment: true,
    }),
    signal: AbortSignal.timeout(90_000),  // 90 秒 timeout
  })

  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Backend error ${res.status}: ${err}`)
  }
  return res.json()
}

export async function analyzeAll(tickers: string[]): Promise<AnalyzeResponse[]> {
  // 並發執行，但限制同時最多 3 個
  const results: AnalyzeResponse[] = []
  for (let i = 0; i < tickers.length; i += 3) {
    const batch = tickers.slice(i, i + 3)
    const batchResults = await Promise.allSettled(batch.map(analyzeStock))
    for (const r of batchResults) {
      if (r.status === 'fulfilled') results.push(r.value)
    }
  }
  return results
}

export async function getMarketSentiment() {
  const res = await fetch(`${BACKEND_URL}/market/sentiment`)
  return res.json()
}
```

### 6.4 Telegram MarkdownV2 格式化

```typescript
// bot/src/formatter.ts

import { AnalyzeResponse } from './api'

// MarkdownV2 特殊字元跳脫
function esc(text: string): string {
  return text.replace(/[_*[\]()~`>#+\-=|{}.!]/g, '\\$&')
}

function signalEmoji(signal: string, suggestion: string): string {
  if (suggestion === 'long') return '🟢'
  if (suggestion === 'short') return '🔴'
  return '🟡'
}

function riskEmoji(level: string): string {
  return level === 'low' ? '🔵' : level === 'medium' ? '🟠' : '🔴'
}

function avgConfidence(data: AnalyzeResponse): number {
  const sum = data.signals.reduce((acc, s) => acc + s.confidence, 0)
  return sum / data.signals.length
}

export function formatSingleAnalysis(data: AnalyzeResponse): string {
  const emoji = signalEmoji(data.signals[0]?.signal, data.position_suggestion)
  const conf = Math.round(avgConfidence(data) * 100)

  const lines = [
    `📊 *${esc(data.ticker)}* 分析報告`,
    `${emoji} *${esc(data.position_suggestion.toUpperCase())}* \\| 信心 ${conf}% \\| 風險 ${riskEmoji(data.risk_level)}${esc(data.risk_level)}`,
    data.price ? `💵 當前價格：\\$${esc(data.price.toFixed(2))}` : '',
    '',
    ...data.signals.map(s => {
      const sEmoji = s.signal === 'bullish' ? '⬆️' : s.signal === 'bearish' ? '⬇️' : '➡️'
      return `${sEmoji} *${esc(agentDisplayName(s.agent))}*\n   ${esc(s.reasoning.slice(0, 150))}`
    }),
    '',
    `📌 建議部位上限：${Math.round(data.position_size * 100)}%`,
    `📅 分析時間：${esc(data.analysis_date)}${data.cached ? ' \\(快取\\)' : ''}`,
  ].filter(Boolean)

  return lines.join('\n')
}

export function formatDailyReport(
  type: 'morning' | 'evening' | 'weekly',
  results: AnalyzeResponse[]
): string {
  const now = new Date()
  const header = type === 'morning'
    ? `🌅 *美股早報* \\— ${esc(now.toLocaleDateString('zh-TW'))}`
    : type === 'evening'
    ? `🌙 *美股晚報* \\— ${esc(now.toLocaleDateString('zh-TW'))}`
    : `📆 *美股週報* \\— ${esc(now.toLocaleDateString('zh-TW'))}`

  const sections = results.map(r => {
    const emoji = signalEmoji(r.signals[0]?.signal, r.position_suggestion)
    const conf = Math.round(avgConfidence(r) * 100)
    return [
      `${emoji} *${esc(r.ticker)}*  信心 ${conf}%`,
      `   ${esc(r.summary.slice(0, 200))}`,
    ].join('\n')
  })

  return [header, '', ...sections, '', `_以上僅供參考，非投資建議_`].join('\n')
}

function agentDisplayName(agent: string): string {
  const map: Record<string, string> = {
    warren_buffett: 'Warren Buffett',
    peter_lynch: 'Peter Lynch',
    charlie_munger: 'Charlie Munger',
    stanley_druckenmiller: 'Druckenmiller',
    fundamentals_agent: '基本面分析',
    technicals_agent: '技術面分析',
    sentiment_agent: '市場情緒',
    risk_manager: '風控評估',
  }
  return map[agent] ?? agent
}
```

### 6.5 自選股管理

```typescript
// bot/src/watchlist.ts

import { createClient } from 'redis'

const redis = createClient({ url: process.env.REDIS_URL ?? 'redis://localhost:6379' })
redis.connect()

const WATCHLIST_KEY = (userId: number) => `watchlist:${userId}`
const MAX_SIZE = parseInt(process.env.MAX_WATCHLIST_SIZE ?? '10')

export async function getWatchlist(userId: number): Promise<string[]> {
  return redis.sMembers(WATCHLIST_KEY(userId))
}

export async function addToWatchlist(userId: number, ticker: string): Promise<'added' | 'exists' | 'full'> {
  const current = await redis.sCard(WATCHLIST_KEY(userId))
  if (current >= MAX_SIZE) return 'full'
  const added = await redis.sAdd(WATCHLIST_KEY(userId), ticker.toUpperCase())
  return added > 0 ? 'added' : 'exists'
}

export async function removeFromWatchlist(userId: number, ticker: string): Promise<boolean> {
  const removed = await redis.sRem(WATCHLIST_KEY(userId), ticker.toUpperCase())
  return removed > 0
}

// 取得所有用戶自選股的聯集（供排程使用）
export async function getWatchlistAll(): Promise<string[]> {
  const keys = await redis.keys('watchlist:*')
  if (keys.length === 0) return []
  const all = new Set<string>()
  for (const key of keys) {
    const tickers = await redis.sMembers(key)
    tickers.forEach(t => all.add(t))
  }
  return Array.from(all)
}
```

---

## 7. 資料流與排程

### 7.1 每日早報流程

```
[cron 13:00 UTC]
       │
       ▼
bot/index.ts: triggerScheduledReport('morning')
       │
       ├─ Redis: 取得所有用戶自選股聯集
       │
       ├─ 發送 "分析中..." 提示訊息到 TG channel
       │
       ├─ api.ts: analyzeAll(tickers) ─────────────────────┐
       │    ├─ POST /analyze AAPL                           │
       │    ├─ POST /analyze NVDA    (並發，最多 3 個)      │
       │    └─ POST /analyze MSFT                           │
       │                             ┌──────────────────────┘
       │              backend/graph.py: run_analysis()
       │                    │
       │                    ├─ 各 Agent 並行分析
       │                    │    ├─ buffett_agent()
       │                    │    ├─ lynch_agent()
       │                    │    ├─ fundamentals_agent()
       │                    │    ├─ technicals_agent()
       │                    │    └─ sentiment_agent()
       │                    │
       │                    ├─ risk_manager_agent()
       │                    └─ portfolio_manager_agent()
       │
       ├─ formatter.ts: formatDailyReport('morning', results)
       │
       └─ bot.sendMessage(channelId, formattedMessage)
```

### 7.2 即時 /analyze 流程

```
[用戶: /analyze AAPL]
       │
       ▼
commands.ts: handleAnalyze(bot, msg, 'AAPL')
       │
       ├─ 權限檢查：userId 在 TG_ALLOWED_USERS 白名單？
       │
       ├─ 發送 "分析中..." 提示（30~60 秒預告）
       │
       ├─ api.ts: analyzeStock('AAPL')
       │    │
       │    └─ POST /analyze → backend 處理（含 Redis 快取）
       │
       └─ 回傳 formatSingleAnalysis(result) 到用戶私訊
```

---

## 8. 環境變數

### .env.example

```bash
# ================================================================
# LLM
# ================================================================
OPENAI_API_KEY=sk-...
# 備用：若要換 Claude
# ANTHROPIC_API_KEY=sk-ant-...

# ================================================================
# 財務數據
# ================================================================
# AAPL / NVDA / MSFT / GOOGL / TSLA 免費，其他 ticker 需要此 Key
FINANCIAL_DATASETS_API_KEY=fd-...

# ================================================================
# Telegram Bot
# ================================================================
TG_BOT_TOKEN=123456:ABC-DEF...
TG_CHANNEL_ID=-100123456789          # 推播頻道 ID（負數開頭）
# 允許使用 /analyze 的用戶 ID（逗號分隔），留空則不限制
TG_ALLOWED_USERS=123456789,987654321

# ================================================================
# Redis
# ================================================================
REDIS_URL=redis://redis:6379         # Zeabur 內網使用 service name

# ================================================================
# 服務設定
# ================================================================
BACKEND_URL=http://backend:8000      # Zeabur 內網
PORT=8000                            # backend 監聽 port

# ================================================================
# 自選股預設值
# ================================================================
DEFAULT_WATCHLIST=AAPL,NVDA,MSFT,TSLA,META
MAX_WATCHLIST_SIZE=10

# ================================================================
# 分析限流（選用）
# ================================================================
DAILY_ANALYZE_LIMIT=10               # 每用戶每日最多 /analyze 次數
```

---

## 9. 本地開發

### 9.1 前置需求

| 工具 | 版本 | 說明 |
|------|------|------|
| Python | 3.11+ | 後端 |
| Node.js | 20 LTS | Bot |
| Docker Desktop | 最新 | 本地 compose |
| Poetry | 1.7+ | Python 套件管理 |

### 9.2 初次設定

```bash
# 1. 複製 repo
git clone https://github.com/jack926509/us-stock-bot.git
cd us-stock-bot

# 2. 複製 ai-hedge-fund agents（後端基礎）
git clone https://github.com/virattt/ai-hedge-fund.git _tmp_hedge
cp -r _tmp_hedge/src/agents/* backend/agents/
cp -r _tmp_hedge/src/tools/*  backend/tools/
rm -rf _tmp_hedge

# 3. 設定環境變數
cp .env.example .env
# 編輯 .env，填入 OPENAI_API_KEY、TG_BOT_TOKEN、TG_CHANNEL_ID

# 4. 安裝後端依賴
cd backend
poetry install
cd ..

# 5. 安裝 Bot 依賴
cd bot
npm install
cd ..
```

### 9.3 用 Docker Compose 啟動（推薦）

```bash
docker compose up --build
```

**docker-compose.yml：**

```yaml
version: '3.8'

services:
  backend:
    build:
      context: .
      dockerfile: docker/backend.Dockerfile
    ports:
      - "8000:8000"
    env_file: .env
    depends_on:
      redis:
        condition: service_healthy

  bot:
    build:
      context: .
      dockerfile: docker/bot.Dockerfile
    env_file: .env
    environment:
      BACKEND_URL: http://backend:8000
      REDIS_URL: redis://redis:6379
    depends_on:
      - backend
      - redis

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
```

### 9.4 不用 Docker（分開啟動）

```bash
# Terminal 1：Redis
docker run --rm -p 6379:6379 redis:7-alpine

# Terminal 2：Backend
cd backend
poetry run uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 3：Bot
cd bot
npm run dev
```

### 9.5 驗證 Backend 是否正常

```bash
# 健康檢查
curl http://localhost:8000/health

# 測試 AAPL 分析（免費 ticker，無需 API Key）
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"ticker": "AAPL", "agents": ["buffett", "technicals"]}'
```

---

## 10. Zeabur 部署

### 10.1 服務架構

| 服務名稱 | 類型 | 說明 |
|----------|------|------|
| `backend` | Python Service | FastAPI 分析引擎 |
| `bot` | Node.js Service | Telegram Bot + cron |
| `redis` | Zeabur Marketplace | 快取 + 自選股儲存 |

### 10.2 Dockerfile

**backend.Dockerfile：**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 系統依賴
RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**bot.Dockerfile：**

```dockerfile
FROM node:20-alpine

WORKDIR /app

COPY bot/package*.json ./
RUN npm ci --only=production

COPY bot/ .
RUN npm run build

CMD ["npm", "start"]
```

### 10.3 Zeabur 部署步驟

1. 前往 [zeabur.com](https://zeabur.com) 建立新 Project
2. 新增 Service → 選 Git，綁定此 Repo
3. 設定 backend Service：
   - Dockerfile 路徑：`docker/backend.Dockerfile`
   - 環境變數：填入 `.env` 中所有後端相關變數
   - Health Check Path：`/health`
4. 新增 Redis Service（Marketplace → Redis）
5. 新增 bot Service：
   - Dockerfile 路徑：`docker/bot.Dockerfile`
   - 環境變數：`TG_BOT_TOKEN`、`TG_CHANNEL_ID`、`TG_ALLOWED_USERS`
   - `BACKEND_URL`：設為 `http://backend:8000`（Zeabur 內網自動解析）
   - `REDIS_URL`：點選 Redis Service 的連線字串 → 複製

### 10.4 Zeabur 內網服務互連

Zeabur 同 Project 內的服務可以直接用 service name 互連：

```
backend 呼叫 redis：redis://redis:6379
bot 呼叫 backend：http://backend:8000
```

不需要暴露外部 port，安全且免費。

---

## 11. 快取策略

### 11.1 快取 Key 設計

| Key Pattern | TTL | 說明 |
|-------------|-----|------|
| `analyze:{TICKER}:{YYYY-MM-DD}` | 4 小時 | 單股分析結果 |
| `sentiment:{YYYY-MM-DD}` | 2 小時 | 大盤情緒數據 |
| `watchlist:{userId}` | 永久（Set） | 用戶自選股清單 |
| `report:morning:{YYYY-MM-DD}` | 1 天 | 早報快取（避免重複觸發） |

### 11.2 快取操作模組

```python
# backend/cache.py

import json
import redis.asyncio as redis
from typing import Optional

async def get_cached(r: redis.Redis, key: str) -> Optional[dict]:
    val = await r.get(key)
    if val:
        return json.loads(val)
    return None

async def set_cache(r: redis.Redis, key: str, data: dict, ttl: int = 14400):
    await r.setex(key, ttl, json.dumps(data, ensure_ascii=False))

async def delete_cache(r: redis.Redis, pattern: str):
    keys = await r.keys(pattern)
    if keys:
        await r.delete(*keys)
```

---

## 12. 錯誤處理與限流

### 12.1 Backend 錯誤回應

```python
# backend/main.py — 全域例外處理

from fastapi import Request
from fastapi.responses import JSONResponse

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "detail": "分析失敗，請稍後再試"}
    )
```

### 12.2 Bot 限流

```typescript
// bot/src/commands.ts — 每用戶每日限流

import { createClient } from 'redis'

const redis = createClient({ url: process.env.REDIS_URL })
await redis.connect()

const DAILY_LIMIT = parseInt(process.env.DAILY_ANALYZE_LIMIT ?? '10')

async function checkRateLimit(userId: number): Promise<boolean> {
  const key = `ratelimit:${userId}:${new Date().toISOString().slice(0, 10)}`
  const count = await redis.incr(key)
  if (count === 1) await redis.expire(key, 86400)  // 當天結束後自動清除
  return count <= DAILY_LIMIT
}

export async function handleAnalyze(bot: TelegramBot, msg: Message, ticker: string) {
  const userId = msg.from!.id

  // 白名單檢查
  const allowedUsers = process.env.TG_ALLOWED_USERS?.split(',').map(Number)
  if (allowedUsers?.length && !allowedUsers.includes(userId)) {
    return bot.sendMessage(msg.chat.id, '❌ 無使用權限')
  }

  // 限流檢查
  if (!(await checkRateLimit(userId))) {
    return bot.sendMessage(msg.chat.id, `⚠️ 今日分析次數已達上限（${DAILY_LIMIT} 次）`)
  }

  const waitMsg = await bot.sendMessage(msg.chat.id, `⏳ 正在分析 *${ticker}*，約需 30~60 秒...`, {
    parse_mode: 'MarkdownV2'
  })

  try {
    const result = await analyzeStock(ticker)
    const { formatSingleAnalysis } = await import('./formatter')
    await bot.sendMessage(msg.chat.id, formatSingleAnalysis(result), { parse_mode: 'MarkdownV2' })
  } catch (err) {
    await bot.sendMessage(msg.chat.id, `❌ 分析失敗：${err instanceof Error ? err.message : '未知錯誤'}`)
  } finally {
    await bot.deleteMessage(msg.chat.id, waitMsg.message_id)
  }
}
```

---

## 13. 開發時程

### Phase 1 — Week 1（核心後端）

**目標**：`POST /analyze AAPL` 本地可跑

- [ ] Fork ai-hedge-fund，複製 agents/ 與 tools/ 到 backend/
- [ ] 建立 `backend/main.py`（FastAPI + `/health` + `/analyze`）
- [ ] 建立 `backend/graph.py`（LangGraph workflow 精簡版，先只接 buffett + technicals）
- [ ] 建立 `backend/models.py`（Pydantic 模型）
- [ ] 本地測試：`curl POST /analyze AAPL` 回傳 JSON
- [ ] 加入 `backend/cache.py`（Redis 快取）

### Phase 2 — Week 2（Telegram Bot）

**目標**：手動 `/analyze AAPL` 可用

- [ ] 建立 bot/ TypeScript 專案結構
- [ ] 實作 `bot/src/api.ts`（呼叫 backend）
- [ ] 實作 `bot/src/formatter.ts`（MarkdownV2 格式化）
- [ ] 實作 `/analyze` 指令（含 loading 訊息）
- [ ] 實作 `/watchlist` `/add` `/remove` 指令
- [ ] Docker Compose 本地整合測試

### Phase 3 — Week 3（排程 + Zeabur）

**目標**：自動推播上線

- [ ] 加入 cron 排程（早報/晚報）
- [ ] 實作 `formatDailyReport()` 批次格式化
- [ ] 建立 `docker/backend.Dockerfile` 與 `docker/bot.Dockerfile`
- [ ] Zeabur 首次部署（backend + bot + redis）
- [ ] 設定 Zeabur 環境變數
- [ ] 驗證排程自動觸發

### Phase 4 — Week 4（完善與上線）

**目標**：穩定 v1.0 上線

- [ ] 加入其餘 Agent（lynch / munger / druckenmiller / sentiment）
- [ ] 實作 `/sentiment` 指令（大盤情緒）
- [ ] 加入每用戶限流
- [ ] 實作 `/status` 指令
- [ ] 週報功能
- [ ] 錯誤通知（分析失敗時 bot 通知管理員）
- [ ] 撰寫 README.md

---

## 14. API 成本估算

### 14.1 Token 消耗估算（每次分析）

| 組成 | Token 數 |
|------|----------|
| 財務數據 Context（每股） | ~1,500 |
| 每個 Agent Prompt + Response | ~600 |
| 6 個 Agents 小計 | ~3,600 |
| Risk Manager + Portfolio Manager | ~800 |
| **單股總計** | **~5,900** |

### 14.2 每日成本估算

| 場景 | 說明 | Token 數 | 費用（GPT-4o）|
|------|------|----------|----------------|
| 早報 5 支股票 | 5 × 5,900 | 29,500 | ~$0.15 |
| 晚報 5 支股票 | 5 × 5,900 | 29,500 | ~$0.15 |
| 用戶 /analyze 10 次 | 10 × 5,900 | 59,000 | ~$0.30 |
| **每日合計** | | **118,000** | **~$0.60** |
| **每月合計（22 交易日）** | | | **~$13** |

> GPT-4o 定價（2026）：$5/1M input tokens、$15/1M output tokens，混合估約 $0.005/1K tokens

### 14.3 節省成本建議

- 非排程的 `/analyze` 預設 TTL 4 小時快取，同一天同一支股票只跑一次
- 排程分析可改用 `gpt-4o-mini`（成本降低 15 倍），在 `graph.py` 調整 `model` 參數即可
- 限制每用戶每日 `/analyze` 10 次（由 Redis 計數）

---

## 15. 未來擴充計畫

### v1.1 — 前端 Dashboard
- 以 ai-hedge-fund Web App 前端為基礎，客製化中文 UI
- 同一個 backend FastAPI 同時服務 Telegram Bot + Web App
- 部署為 Zeabur 第四個 Service（Static Site + Vite React）

### v1.2 — 個人化 AI 分析偏好
- 用戶可設定「偏好的 Agent 組合」（例：只看 Buffett + Lynch）
- 用戶可設定報告語言（中/英）
- 每用戶獨立的分析歷史記錄（Redis）

### v1.3 — 量化策略回測整合
- 接入 XAUUSD LightGBM 模型（OHLCV 特徵）
- 加入 Kronos K 線預測模型輸出（需另建 GPU Service 或串接外部推論 API）

### v1.4 — 法說會與財報追蹤
- 抓取 Earnings Calendar，在法說會前 24 小時自動推播分析
- 財報發布後自動觸發 Fundamentals Agent 重新分析

---

*此文件由 Claude Sonnet 4.6 生成輔助，開發過程中請以實際測試結果為準。*
