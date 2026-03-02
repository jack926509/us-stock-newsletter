import os
import asyncio
import logging
import httpx
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from openai import AsyncOpenAI
import telegram
from fastapi import FastAPI
import uvicorn
import pytz

# ─── Logging ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY")
FINNHUB_API_KEY  = os.getenv("FINNHUB_API_KEY")
TAVILY_API_KEY   = os.getenv("TAVILY_API_KEY")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CRON_HOUR        = int(os.getenv("CRON_HOUR", "7"))
CRON_MINUTE      = int(os.getenv("CRON_MINUTE", "30"))
TIMEZONE         = os.getenv("TIMEZONE", "America/New_York")

FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
FINNHUB_NEWS_URL  = "https://finnhub.io/api/v1/news"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TELEGRAM_MAX_LEN  = 4000

MARKET_SYMBOLS = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "DIA": "Dow Jones",
}

# ─── Singleton Clients ────────────────────────────────────
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
telegram_bot  = telegram.Bot(token=TELEGRAM_TOKEN)
http_client: httpx.AsyncClient | None = None  # initialised in lifespan

# ─── Pre-compiled HTML → Telegram Markdown patterns ───────
_HTML_PATTERNS = [
    (re.compile(r'<h2>(.*?)</h2>',              re.DOTALL), r'\n\n🔹 *\1*\n'),
    (re.compile(r'<h3>(.*?)</h3>',              re.DOTALL), r'\n*\1*\n'),
    (re.compile(r'<(?:b|strong)>(.*?)</(?:b|strong)>', re.DOTALL), r'*\1*'),
    (re.compile(r'<a href="(.*?)">(.*?)</a>',   re.DOTALL), r'[\2](\1)'),
    (re.compile(r'<[^>]+>'),                               ''),
    (re.compile(r'\n{3,}'),                                '\n\n'),
]

def html_to_telegram(html: str) -> str:
    text = html
    for pattern, replacement in _HTML_PATTERNS:
        text = pattern.sub(replacement, text)
    return text.strip()

# ─── Step 1: Finnhub 市場數據 ─────────────────────────────
async def get_market_data() -> dict:
    result = {}
    for sym, name in MARKET_SYMBOLS.items():
        try:
            r = await http_client.get(
                FINNHUB_QUOTE_URL,
                params={"symbol": sym, "token": FINNHUB_API_KEY},
            )
            data = r.json()
            result[sym] = {
                "name": name,
                "price": data.get("c", 0),
                "change": data.get("dp", 0),
            }
        except Exception as e:
            log.error("Finnhub quote error for %s: %s", sym, e)
            result[sym] = {"name": name, "price": 0, "change": 0}
    return result

# ─── Step 2: Finnhub 市場新聞 ─────────────────────────────
async def get_finnhub_news(category: str = "general", count: int = 5) -> list:
    try:
        r = await http_client.get(
            FINNHUB_NEWS_URL,
            params={"category": category, "token": FINNHUB_API_KEY},
        )
        return r.json()[:count]
    except Exception as e:
        log.error("Finnhub news error: %s", e)
        return []

# ─── Step 3: Tavily 深度搜尋 ──────────────────────────────
async def tavily_search(query: str, max_results: int = 3, time_range: str = "day") -> list:
    resp = await http_client.post(
        TAVILY_SEARCH_URL,
        json={
            "api_key": TAVILY_API_KEY,
            "query": query,
            "topic": "news",
            "max_results": max_results,
            "time_range": time_range,
            "include_raw_content": True,
        },
    )
    resp.raise_for_status()
    return resp.json().get("results", [])

# ─── Step 4: OpenAI — Planning ────────────────────────────
async def plan_newsletter(news_items: list) -> dict:
    news_text = "\n\n".join([
        f"標題: {n.get('headline', n.get('title', ''))}\n摘要: {n.get('summary', n.get('content', ''))[:300]}"
        for n in news_items
    ])
    resp = await openai_client.chat.completions.create(
        model="gpt-4o",
        max_tokens=500,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": '你是擁有10年經驗的華爾街投資策略師。根據新聞判斷市場情緒，輸出 JSON：\n{"title": "日報主標題（含關鍵股票或事件）", "topics": ["主題1(3-6字)", "主題2(3-6字)", "主題3(3-6字)"]}',
            },
            {"role": "user", "content": f"最新美股新聞：\n{news_text}"},
        ],
    )
    return json.loads(resp.choices[0].message.content)

# ─── Step 5: OpenAI — Section Writer ─────────────────────
async def write_section(topic: str, research: list) -> str:
    research_text = "\n\n".join([
        f"標題: {r.get('title')}\nURL: {r.get('url')}\n內容: {r.get('content', '')[:600]}"
        for r in research
    ])
    resp = await openai_client.chat.completions.create(
        model="gpt-4o",
        max_tokens=800,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是專業美股證券分析師。針對主題撰寫分析報告章節（繁體中文）：\n"
                    "1. 標題（含公司名+股票代碼如NVDA）\n"
                    "2. 核心數據（如有）\n"
                    "3. 分析內容（為什麼重要、對投資者影響）\n"
                    "語氣：客觀、數據驅動。必須引用來源URL。嚴禁捏造數據。"
                ),
            },
            {"role": "user", "content": f"主題: {topic}\n\n研究資料:\n{research_text}"},
        ],
    )
    return resp.choices[0].message.content

# ─── Step 6: OpenAI — Editor (HTML 電子報) ───────────────
async def edit_newsletter(title: str, sections: list, market: dict) -> dict:
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")
    sections_text = "\n\n\n".join(sections)
    market_snapshot = " | ".join([
        f"{v['name']}: {v['price']:.2f} ({v['change']:+.2f}%)"
        for v in market.values()
    ])
    resp = await openai_client.chat.completions.create(
        model="gpt-4o",
        max_tokens=2500,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    f"你是Bloomberg/WSJ風格的主編。整合分析報告為HTML電子報（繁體中文）。\n"
                    f"今日日期: {today}\n"
                    f"大盤數據: {market_snapshot}\n\n"
                    '輸出 JSON 格式：\n{"subject": "郵件主旨", "content": "HTML內容（只用h2/h3/p/ul/li/a/b標籤）"}\n\n'
                    "HTML 結構：\n"
                    "1. <p> 市場快照（大盤走勢+今日情緒總結）\n"
                    "2. 每章節：<h2>標題</h2><p>內容（股票代碼粗體）</p>\n"
                    "3. <h3>消息來源</h3><ul>所有參考連結</ul>\n"
                    "4. <p> 投資啟示（風險/機會提醒）\n"
                    '5. <p style="font-size:0.8em;color:gray;"> 免責聲明：本文內容僅供參考，不構成投資建議。投資有風險，入市須謹慎。'
                ),
            },
            {"role": "user", "content": f"主標題: {title}\n\n章節內容:\n{sections_text}"},
        ],
    )
    return json.loads(resp.choices[0].message.content)

# ─── Step 7: Telegram 發送 ────────────────────────────────
async def send_to_telegram(subject: str, content_html: str):
    await telegram_bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=f"📰 *{subject}*",
        parse_mode="Markdown",
    )
    text = html_to_telegram(content_html)
    for i in range(0, len(text), TELEGRAM_MAX_LEN):
        await telegram_bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text[i : i + TELEGRAM_MAX_LEN],
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

# ─── 主流程 ───────────────────────────────────────────────
async def run_newsletter():
    log.info("🚀 開始生成美股日報...")
    try:
        # 1. 並行取得市場數據 + Finnhub 新聞
        market, initial_news = await asyncio.gather(
            get_market_data(),
            get_finnhub_news(category="general", count=5),
        )
        log.info("市場數據: %s", [f"{k}={v['price']}" for k, v in market.items()])

        # 2. OpenAI Planning
        plan   = await plan_newsletter(initial_news)
        title  = plan["title"]
        topics = plan["topics"]
        log.info("主標題: %s", title)
        log.info("主題: %s", topics)

        # 3. 並行 Tavily 深度搜尋每個主題
        research_results = await asyncio.gather(
            *[tavily_search(t, max_results=3, time_range="month") for t in topics]
        )

        # 4. 並行撰寫各章節（原生 async OpenAI calls，無需 ThreadPoolExecutor）
        sections = list(await asyncio.gather(
            *[write_section(topics[i], research_results[i]) for i in range(len(topics))]
        ))
        log.info("三個章節撰寫完成")

        # 5. 整合編輯
        newsletter = await edit_newsletter(title, sections, market)
        log.info("電子報整合完成: %s", newsletter["subject"])

        # 6. 發送 Telegram
        await send_to_telegram(newsletter["subject"], newsletter["content"])
        log.info("✅ 美股日報發送完成！")

    except Exception as e:
        log.exception("❌ 美股日報生成失敗: %s", e)
        try:
            await telegram_bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=f"⚠️ 美股日報生成失敗：{e}",
            )
        except Exception:
            log.exception("Telegram 錯誤通知發送失敗")

# ─── FastAPI + Scheduler ──────────────────────────────────
scheduler = AsyncIOScheduler(timezone=TIMEZONE)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
    scheduler.add_job(
        run_newsletter,
        CronTrigger(hour=CRON_HOUR, minute=CRON_MINUTE, timezone=pytz.timezone(TIMEZONE)),
    )
    scheduler.start()
    log.info("📅 排程已啟動：每天 %02d:%02d (%s)", CRON_HOUR, CRON_MINUTE, TIMEZONE)
    yield
    scheduler.shutdown()
    await http_client.aclose()

app = FastAPI(title="美股新聞編輯室", lifespan=lifespan)

@app.get("/")
def health():
    next_run = scheduler.get_jobs()[0].next_run_time if scheduler.get_jobs() else None
    return {
        "status": "ok",
        "service": "美股新聞編輯室",
        "next_run": str(next_run),
    }

@app.post("/run")
async def manual_trigger():
    """手動觸發（測試用）"""
    asyncio.create_task(run_newsletter())
    return {"status": "triggered", "message": "日報生成中，請查看 Telegram"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
