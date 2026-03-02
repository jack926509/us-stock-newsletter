import os
import asyncio
import httpx
import json
import re
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from openai import OpenAI
import telegram
from fastapi import FastAPI
import uvicorn
import pytz

app = FastAPI(title="美股新聞編輯室")

# ─── Config ───────────────────────────────────────────────
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
FINNHUB_API_KEY   = os.getenv("FINNHUB_API_KEY")
TAVILY_API_KEY    = os.getenv("TAVILY_API_KEY")
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")
CRON_HOUR         = int(os.getenv("CRON_HOUR", "7"))
CRON_MINUTE       = int(os.getenv("CRON_MINUTE", "30"))
TIMEZONE          = os.getenv("TIMEZONE", "America/New_York")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ─── Step 1: Finnhub 市場數據 ─────────────────────────────
async def get_market_data() -> dict:
    symbols = {
        "SPY": "S&P 500",
        "QQQ": "Nasdaq 100",
        "DIA": "Dow Jones"
    }
    result = {}
    async with httpx.AsyncClient(timeout=15) as client:
        for sym, name in symbols.items():
            try:
                r = await client.get(
                    f"https://finnhub.io/api/v1/quote",
                    params={"symbol": sym, "token": FINNHUB_API_KEY}
                )
                data = r.json()
                price = data.get("c", 0)   # current price
                prev  = data.get("pc", price)  # previous close
                chg   = data.get("dp", 0)  # percent change
                result[sym] = {"name": name, "price": price, "change": chg}
            except Exception as e:
                print(f"Finnhub error for {sym}: {e}")
                result[sym] = {"name": name, "price": 0, "change": 0}
    return result

# ─── Step 2: Finnhub 市場新聞 ─────────────────────────────
async def get_finnhub_news(category: str = "general", count: int = 5) -> list:
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(
                "https://finnhub.io/api/v1/news",
                params={"category": category, "token": FINNHUB_API_KEY}
            )
            news = r.json()
            return news[:count]
        except Exception as e:
            print(f"Finnhub news error: {e}")
            return []

# ─── Step 3: Tavily 深度搜尋 ──────────────────────────────
async def tavily_search(query: str, max_results: int = 3, time_range: str = "day") -> list:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "topic": "news",
                "max_results": max_results,
                "time_range": time_range,
                "include_raw_content": True,
            }
        )
        resp.raise_for_status()
        return resp.json().get("results", [])

# ─── Step 4: OpenAI — Planning ────────────────────────────
def plan_newsletter(news_items: list) -> dict:
    news_text = "\n\n".join([
        f"標題: {n.get('headline', n.get('title', ''))}\n摘要: {n.get('summary', n.get('content', ''))[:300]}"
        for n in news_items
    ])
    resp = openai_client.chat.completions.create(
        model="gpt-4o",
        max_tokens=500,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": """你是擁有10年經驗的華爾街投資策略師。根據新聞判斷市場情緒，輸出 JSON：
{"title": "日報主標題（含關鍵股票或事件）", "topics": ["主題1(3-6字)", "主題2(3-6字)", "主題3(3-6字)"]}"""
            },
            {"role": "user", "content": f"最新美股新聞：\n{news_text}"}
        ]
    )
    return json.loads(resp.choices[0].message.content)

# ─── Step 5: OpenAI — Section Writer ─────────────────────
def write_section(topic: str, research: list) -> str:
    research_text = "\n\n".join([
        f"標題: {r.get('title')}\nURL: {r.get('url')}\n內容: {r.get('content','')[:600]}"
        for r in research
    ])
    resp = openai_client.chat.completions.create(
        model="gpt-4o",
        max_tokens=800,
        messages=[
            {
                "role": "system",
                "content": """你是專業美股證券分析師。針對主題撰寫分析報告章節（繁體中文）：
1. 標題（含公司名+股票代碼如NVDA）
2. 核心數據（如有）
3. 分析內容（為什麼重要、對投資者影響）
語氣：客觀、數據驅動。必須引用來源URL。嚴禁捏造數據。"""
            },
            {"role": "user", "content": f"主題: {topic}\n\n研究資料:\n{research_text}"}
        ]
    )
    return resp.choices[0].message.content

# ─── Step 6: OpenAI — Editor (HTML 電子報) ───────────────
def edit_newsletter(title: str, sections: list, market: dict) -> dict:
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")
    market_snapshot = " | ".join([
        f"{v['name']}: {v['price']:.2f} ({v['change']:+.2f}%)"
        for v in market.values()
    ])
    sections_text = "\n\n\n".join(sections)
    resp = openai_client.chat.completions.create(
        model="gpt-4o",
        max_tokens=2500,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": f"""你是Bloomberg/WSJ風格的主編。整合分析報告為HTML電子報（繁體中文）。
今日日期: {today}
大盤數據: {market_snapshot}

輸出 JSON 格式：
{{"subject": "郵件主旨", "content": "HTML內容（只用h2/h3/p/ul/li/a/b標籤）"}}

HTML 結構：
1. <p> 市場快照（大盤走勢+今日情緒總結）
2. 每章節：<h2>標題</h2><p>內容（股票代碼粗體）</p>
3. <h3>消息來源</h3><ul>所有參考連結</ul>
4. <p> 投資啟示（風險/機會提醒）
5. <p style="font-size:0.8em;color:gray;"> 免責聲明：本文內容僅供參考，不構成投資建議。投資有風險，入市須謹慎。"""
            },
            {"role": "user", "content": f"主標題: {title}\n\n章節內容:\n{sections_text}"}
        ]
    )
    return json.loads(resp.choices[0].message.content)

# ─── Step 7: Telegram 發送 ────────────────────────────────
async def send_to_telegram(subject: str, content_html: str):
    bot = telegram.Bot(token=TELEGRAM_TOKEN)

    # 發送標題
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=f"📰 *{subject}*",
        parse_mode="Markdown"
    )

    # HTML → Telegram Markdown
    text = re.sub(r'<h2>(.*?)</h2>', r'\n\n🔹 *\1*\n', content_html)
    text = re.sub(r'<h3>(.*?)</h3>', r'\n*\1*\n', text)
    text = re.sub(r'<b>(.*?)</b>', r'*\1*', text)
    text = re.sub(r'<strong>(.*?)</strong>', r'*\1*', text)
    text = re.sub(r'<a href="(.*?)">(.*?)</a>', r'[\2](\1)', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()

    # 分段發送（Telegram 上限 4096 字）
    MAX_LEN = 4000
    chunks = [text[i:i+MAX_LEN] for i in range(0, len(text), MAX_LEN)]
    for chunk in chunks:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=chunk,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

# ─── 主流程 ───────────────────────────────────────────────
async def run_newsletter():
    print(f"[{datetime.now()}] 🚀 開始生成美股日報...")
    try:
        # 1. 並行取得市場數據 + Finnhub 新聞
        market_task = get_market_data()
        news_task   = get_finnhub_news(category="general", count=5)
        market, initial_news = await asyncio.gather(market_task, news_task)
        print(f"  ✅ 市場數據: {[f'{k}={v[\"price\"]}' for k,v in market.items()]}")

        # 2. OpenAI Planning
        plan   = plan_newsletter(initial_news)
        title  = plan["title"]
        topics = plan["topics"]
        print(f"  ✅ 主標題: {title}")
        print(f"  ✅ 主題: {topics}")

        # 3. 並行 Tavily 深度搜尋每個主題
        research_tasks = [tavily_search(t, max_results=3, time_range="month") for t in topics]
        research_results = await asyncio.gather(*research_tasks)

        # 4. 並行撰寫各章節（ThreadPoolExecutor 跑同步 OpenAI calls）
        import concurrent.futures
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            section_futures = [
                loop.run_in_executor(pool, write_section, topics[i], research_results[i])
                for i in range(len(topics))
            ]
            sections = list(await asyncio.gather(*section_futures))
        print(f"  ✅ 三個章節撰寫完成")

        # 5. 整合編輯
        newsletter = await loop.run_in_executor(
            None, edit_newsletter, title, sections, market
        )
        print(f"  ✅ 電子報整合完成: {newsletter['subject']}")

        # 6. 發送 Telegram
        await send_to_telegram(newsletter["subject"], newsletter["content"])
        print(f"[{datetime.now()}] ✅ 美股日報發送完成！")

    except Exception as e:
        print(f"[{datetime.now()}] ❌ 錯誤: {e}")
        import traceback
        traceback.print_exc()
        # 發送錯誤通知
        try:
            bot = telegram.Bot(token=TELEGRAM_TOKEN)
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=f"⚠️ 美股日報生成失敗：{str(e)}"
            )
        except:
            pass

# ─── FastAPI + Scheduler ──────────────────────────────────
scheduler = AsyncIOScheduler(timezone=TIMEZONE)

@app.on_event("startup")
async def startup():
    scheduler.add_job(
        run_newsletter,
        CronTrigger(hour=CRON_HOUR, minute=CRON_MINUTE, timezone=pytz.timezone(TIMEZONE))
    )
    scheduler.start()
    print(f"📅 排程已啟動：每天 {CRON_HOUR:02d}:{CRON_MINUTE:02d} ({TIMEZONE})")

@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()

@app.get("/")
def health():
    next_run = scheduler.get_jobs()[0].next_run_time if scheduler.get_jobs() else None
    return {
        "status": "ok",
        "service": "美股新聞編輯室",
        "next_run": str(next_run)
    }

@app.post("/run")
async def manual_trigger():
    """手動觸發（測試用）"""
    asyncio.create_task(run_newsletter())
    return {"status": "triggered", "message": "日報生成中，請查看 Telegram"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
