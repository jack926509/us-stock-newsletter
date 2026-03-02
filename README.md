# 美股新聞編輯室 🗞️

自動化美股日報服務，每天定時透過 Telegram 發送，部署在 Zeabur。

## 技術架構

```
每天 7:30 AM ET (可調整)
    ↓
Finnhub API → 市場報價 (SPY/QQQ/DIA) + 最新新聞
    ↓
GPT-4o Planning → 主標題 + 3 個焦點主題
    ↓
Tavily 深度搜尋 x3 (並行)
    ↓
GPT-4o Section Writer x3 (並行)
    ↓
GPT-4o Editor → HTML 電子報
    ↓
Telegram Bot 發送
```

## 部署到 Zeabur

1. Fork 此 repo
2. 登入 [Zeabur](https://zeabur.com) → New Project → Deploy from GitHub
3. 在 Variables 設定環境變數：

| 變數 | 說明 | 取得方式 |
|------|------|---------|
| `OPENAI_API_KEY` | OpenAI API Key | https://platform.openai.com |
| `FINNHUB_API_KEY` | Finnhub API Key | https://finnhub.io |
| `TAVILY_API_KEY` | Tavily API Key | https://tavily.com |
| `TELEGRAM_TOKEN` | Telegram Bot Token | @BotFather |
| `TELEGRAM_CHAT_ID` | 頻道/群組 ID | @userinfobot 或 @getidsbot |
| `CRON_HOUR` | 觸發小時 (預設 7) | - |
| `CRON_MINUTE` | 觸發分鐘 (預設 30) | - |
| `TIMEZONE` | 時區 (預設 America/New_York) | - |

## 手動觸發測試

```bash
curl -X POST https://你的服務.zeabur.app/run
```

## 本地測試

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入 API Keys
python main.py
# 開啟 http://localhost:8080/run 觸發
```
