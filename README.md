# 美股新聞編輯室 🗞️

全自動美股日報服務，**每週一至週五**早上 8:00（Asia/Taipei）定時透過 Slack 推播。以多層 AI Agent 架構生成 Bloomberg/WSJ 風格的市場分析報告，部署於 Zeabur 雲端平台。

---

## ✨ 核心特色

| 特色 | 說明 |
|------|------|
| 🤖 **多層 AI Agent** | Planner → Writer → Editor 三段式分工，品質遠優於單一 prompt |
| 🧠 **ai-hedge-fund 整合** | 內嵌 [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) 為分析大腦，Buffett / 基本面 / 技術面 / 情緒多位 AI 分析師同步給出個股 signal |
| 📋 **watchlist.json** | 自選股清單放在 repo，於 GitHub 網頁直接編輯即可更新，Zeabur webhook 自動重新部署 |
| 🛡️ **高可用容錯** | Tenacity 自動重試 + Pydantic JSON 格式驗證 + hedge fund adapter 降級機制，防止 AI 幻覺破壞流程 |
| 💰 **成本最佳化** | 規劃/撰寫用 `claude-haiku-4-6`，最終編輯升級 `claude-sonnet-4-6`，節省大量 API 費用 |
| ⚡ **並行加速** | 市場報價、Tavily 搜尋、章節撰寫、個股分析全部 `asyncio.gather` 並行執行 |
| 🔒 **防護機制** | Semaphore 控制並發上限、手動觸發冷卻 300 秒防刷 |
| 💬 **Slack Block Kit 版面** | 主貼文 + thread 細節，header/section/context 結構化呈現，章節進度編號、個股共識卡片、來源連結 |

---

## 🏗️ 技術架構

```
┌─────────────────────────────────────────────────────────┐
│                     FastAPI Server                       │
│         APScheduler（週一~五 08:00 Asia/Taipei）          │
└──────────────────────────┬──────────────────────────────┘
                           │ 觸發
                           ▼
┌─────────────────────────────────────────────────────────┐
│              Pipeline 協調層（pipeline.py）               │
└──┬──────────────────────────────────────────────────────┘
   │
   ├─ Step 1 ──▶ 【並行】Finnhub 市場報價 + Finnhub 突發新聞
   │
   ├─ Step 2 ──▶ claude-haiku-4-5 Planner → 主標題 + 焦點主題
   │
   ├─ Step 3 ──▶ 【並行】Tavily Deep Search × 焦點數量
   │
   ├─ Step 4 ──▶ 【並行 Semaphore=3】claude-haiku-4-5 Writer × 焦點數量
   │
   ├─ Step 5 ──▶ claude-sonnet-4-6 Editor → 整合 + Pydantic 結構化輸出
   │
   └─ Step 6 ──▶ Slack Block Kit 格式化 → 主貼文 + thread 推播
```

```mermaid
graph TD
    A[APScheduler<br/>週一到五 08:00 Asia/Taipei] --> B
    B[Finnhub API<br/>市場報價 + 突發新聞] --> C
    C[claude-haiku-4-5 Planner<br/>主標題 + 焦點主題] --> D
    D[Tavily Search ×N<br/>深度新聞搜尋 並行] --> E
    E[claude-haiku-4-5 Writer ×N<br/>章節草稿 並行+Semaphore] --> F
    F[claude-sonnet-4-6 Editor<br/>Pydantic 結構化輸出] --> G
    G[Slack Bot<br/>Block Kit + thread 推播]
```

---

## 📁 專案結構

```
us-stock-newsletter/
├── main.py                  # FastAPI 入口（Zeabur 部署用）：APScheduler + /run 端點
├── run_once.py              # 單次執行入口（Claude Code routines / cron / 手動跑）
├── requirements.txt         # Python 依賴清單
├── .env.example             # 環境變數範本
├── tests/
│   ├── test_formatter.py    # 格式化函式測試（7 項）
│   └── test_merge_blocks.py # 訊息合併邏輯測試（5 項）
│
└── app/
    ├── config.py            # Pydantic Settings 環境變數驗證 + 全域常數
    ├── clients.py           # Singleton 客戶端（OpenAI / Slack / httpx）
    ├── models.py            # Pydantic 資料模型（Newsletter / Section / Source）
    ├── pipeline.py          # 核心流程協調器（6 步驟串接）
    ├── formatter.py         # Slack Block Kit 格式化工具
    ├── sender.py            # Slack chat.postMessage（主貼文 + thread）
    │
    ├── ai/
    │   ├── errors.py        # AIGenerationError 共用例外
    │   ├── planner.py       # Step 2：claude-haiku-4-5 規劃主標題與焦點主題（tool-use 結構化）
    │   ├── writer.py        # Step 4：claude-haiku-4-5 撰寫章節草稿
    │   ├── editor.py        # Step 5：claude-sonnet-4-6 整合輸出結構化（tool-use → NewsletterDraft）
    │   └── hedge_fund.py    # ai-hedge-fund adapter（vendor submodule，個股 verdicts）
    │
    └── data/
        ├── finnhub.py       # Step 1：Finnhub 市場指數報價 + 突發新聞
        └── tavily.py        # Step 3：Tavily 深度新聞搜尋
```

---

## 🔄 完整工作流程

### Step 1 — 資料收集（並行）

`pipeline.py` 使用 `asyncio.gather` **同時**發出兩個請求：

- **`get_market_data()`**：並行取得 SPY / QQQ / DIA 三大指數即時報價與漲跌幅
- **`get_finnhub_news()`**：取得最新 5 筆美股市場新聞

> 兩個請求皆具備 Tenacity 最多重試 3 次（指數退避 2–10 秒）。若個別指數報價失敗，以預設值 `{price: 0, change: 0}` 降級處理，不中斷流程。

### Step 2 — AI 規劃主題（Planner）

`ai/planner.py` 將新聞摘要送給 **claude-haiku-4-5**，輸出：

```json
{
  "title": "NVDA 財報超預期，AI 基建商機持續升溫",
  "topics": ["NVDA 財報分析", "AI 伺服器供應鏈", "Fed 利率影響科技股"]
}
```

透過 Anthropic **tool-use** 強制結構化輸出（`tool_choice` 指定唯一工具，`input_schema` 由 `NewsletterPlan.model_json_schema()` 產生），直接拿到 schema-valid 的 dict 後 Pydantic 驗證，無需解析 markdown code fence。

### Step 3 — 深度搜尋（Tavily，並行）

`data/tavily.py` 針對每個焦點主題向 **Tavily Search API** 並行查詢，每個主題取 3 篇相關文章（標題 + URL + 摘要）。

若某個主題搜尋失敗，Pipeline 跳過該主題繼續，不中斷整體流程。

### Step 4 — AI 章節撰寫（Writer，並行 + Semaphore）

`ai/writer.py` 以 **claude-haiku-4-5** 為每個有效主題撰寫分析章節：

- `asyncio.gather` 並行啟動所有 Writer
- `asyncio.Semaphore(3)` 限制同時最多 3 個 OpenAI 請求，避免 429
- 輸出純文字草稿，股票代碼以 `【TICKER】` 標記

### Step 5 — AI 最終編輯（Editor）

`ai/editor.py` 以 **claude-sonnet-4-6** 將章節草稿整合為結構化輸出：

```json
{
  "subject": "今日主旨（15字內）",
  "market_summary": "大盤情緒一句話摘要",
  "sections": [
    {
      "title": "章節標題",
      "body": "正文（純文字，股票代碼用【TICKER】）",
      "sources": [{"title": "來源標題", "url": "https://..."}]
    }
  ],
  "insights": "投資啟示與風險提醒（2-3句）"
}
```

透過 Anthropic **tool-use**（`input_schema` 來自 `NewsletterDraft.model_json_schema()`，verdicts 由 pipeline 注入避免 LLM 幻覺）直接拿到 schema-valid 的結構，最多重試 3 次（指數退避 3–15 秒）。`stop_reason == "max_tokens"` 時主動觸發 retry。

### Step 6 — Slack 推播（Block Kit + thread）

`sender.py` + `formatter.py` 組裝 Slack Block Kit JSON：

1. **`build_header_blocks()`** — header block 標題 + 日期 + 主旨
2. **`build_market_blocks()`** — 三大指數漲跌快照（fields 雙欄）+ 市場短評
3. **`build_section_blocks()` × N** — 各段以粗體小標 + 內文呈現（不分章節，串成一篇）+ 來源 context block 連結
4. **`build_verdicts_blocks()`** — AI 多分析師個股共識卡片（可空）
5. **`build_footer_blocks()`** — 投資啟示 + context 區免責聲明

**主貼文 + thread 策略**：每天只有 1 則訊息出現在頻道（header + 大盤）。所有焦點段落串成「一整篇文章」放進 thread 的單一回覆裡（用 divider 視覺分段），個股共識與 footer 各自再一則 thread reply。頻道乾淨、細節完整。

**安全限制**：自動處理 section text 3000 字、單則訊息 50 blocks 上限；對 `429` rate limit 讀 `Retry-After` 退避，網路錯誤指數退避（最多 4 次）。

**錯誤告警**：任一步驟嚴重失敗，Pipeline 自動把錯誤訊息以 Block Kit 推到同一個 Slack 頻道。

---

## 📋 自選股管理（watchlist.json）

個股分析清單放在 repo 根目錄的 `watchlist.json`：

```json
{
  "tickers": ["AAPL", "MSFT", "NVDA", "GOOGL", "TSLA"],
  "notes": "免費清單，不需 FINANCIAL_DATASETS_API_KEY"
}
```

### 如何更新

#### 方式一：Slack（即時，需 Volume 持久化）

```
/watchlist add AAPL NVDA
/watchlist remove TSLA
/watchlist clear     # 互動按鈕二次確認
```

**前提**：要設好 `WATCHLIST_PATH=/data/watchlist.json` 並掛 Zeabur Volume，否則改動會在重新部署時被 git repo 覆蓋。設定方式見下方。

#### 方式二：GitHub commit（永久進版控）

1. 於 GitHub 網頁直接打開 `watchlist.json` → 點鉛筆圖示編輯 → commit 到 `main`
2. Zeabur 會自動偵測 push 並重新部署
3. 下一次排程（或手動 `/run`）就會以新清單跑 AI 多分析師

> 推薦工作流：用 Slack 試水溫，覺得某檔值得長期觀察就 commit 進 repo。

### 規則

- ticker 格式：**大寫英文字母 / 數字 / `.` / `-`**，長度 1–10
- 重複會去重、無效會被過濾
- **硬上限 10 檔**（防止 LLM 成本爆炸）
- 清單為空或檔案壞掉會自動 fallback 到 `DEFAULT_WATCHLIST = [AAPL, MSFT, NVDA, GOOGL, TSLA]` 並記 log
- 預設清單完全落在 Financial Datasets **免費層**，不需付費 key；若加入 META / AMZN / AMD / TSM 等，請於 Zeabur 新增 `FINANCIAL_DATASETS_API_KEY`

### Zeabur PostgreSQL 設定（讓 Slack 編輯持久化）

watchlist 持久化採 PostgreSQL 雙模式：
- `DATABASE_URL` 有設 → 走 DB（Zeabur 部署的標準路徑）
- 未設 → fallback 到 `WATCHLIST_PATH` 檔案模式（本地開發、不需 PG 的場景）

#### 部署步驟

1. Zeabur Project → **Add Service** → **Marketplace** → 選 **PostgreSQL**，等服務啟動
2. 回到 us-stock-newsletter Service → **Variables** tab → 新增
   - **Key**：`DATABASE_URL`
   - **Value**：`${POSTGRES.POSTGRES_CONNECTION_STRING}`（或對應 Postgres Service 的 template 變數名稱；展開後會是 `postgresql://user:pass@host:5432/dbname`）
3. **Restart Service**
4. Logs 應看到 `✅ PostgreSQL 連線池就緒；watchlist 表已 ensure`
5. 首次啟動時若 `watchlist` table 為空，會自動以 `WATCHLIST_PATH` 檔（沒有就用 repo 根 `watchlist.json`）為 seed 寫入 DB。之後 Slack 任何 add/remove/clear 都寫進 DB，跨重啟與重部署都保留。

#### 從 Volume 切換到 Postgres

若你之前已經設定過 Volume + `WATCHLIST_PATH=/data/watchlist.json`：

1. 先設好 `DATABASE_URL`（沿用上面步驟）
2. **保留** `WATCHLIST_PATH=/data/watchlist.json`（重啟時會被當作 seed 讀進 DB）
3. Restart Service → 確認 logs 出現 `🌱 PostgreSQL watchlist 從檔案種子初始化（N 檔）`
4. 確認後可以拔掉 Volume（已備份到 DB）；`WATCHLIST_PATH` 變數可保留或移除都無妨

#### 排查

- 用 `/ping` 看 PostgreSQL latency；連不上會列出錯誤訊息
- 表結構：`watchlist(ticker TEXT PRIMARY KEY, added_at TIMESTAMPTZ DEFAULT NOW())`，可用 `psql` 直接查 `SELECT * FROM watchlist ORDER BY added_at;`

---

## 🤖 部署選項一：Claude Code Routines（推薦）

從 v6 起，可改用 [Claude Code Routines](https://code.claude.com/docs/en/web-scheduled-tasks) 取代 Zeabur 長駐容器。Routines 在 Anthropic 雲端定時 clone repo 並執行你預設的 prompt，無需自己維運伺服器、無 24h container 計費。

### 架構差異

| | Zeabur | Claude Code Routines |
|---|---|---|
| 排程 | APScheduler（在 container 內） | Anthropic 雲端排程器 |
| 入口 | `uvicorn main:app` 長駐 + `/run` HTTP | 每次排程 clone repo 跑 `python run_once.py` |
| 計費 | Zeabur container hour | Claude 訂閱用量 |
| 環境變數 | Zeabur Variables | Claude 「Cloud environment」 |
| 相依套件 | Dockerfile build 時 `pip install` | Routine setup script 一次性安裝（快取） |

### 設定步驟

1. **準備好 GitHub repo**（含 `vendor/ai_hedge_fund` submodule）。Routines 預設 clone default branch，submodule 由 setup script 補拉。

2. **建立 Cloud environment** — 進 [claude.ai](https://claude.ai/) → Settings → Cloud environments → New environment：
   - 加入下表所有必填環境變數（同 Zeabur 那欄）
   - **Setup script**：
     ```bash
     git submodule update --init --recursive
     pip install -r requirements.txt
     ```
     setup script 結果會被快取，後續 routine run 不會重跑安裝。

3. **建立 Routine** — 進 [claude.ai/code/routines](https://claude.ai/code/routines) → New routine：
   - **Name**：`美股日報 weekday 08:00 TPE`
   - **Repository**：選擇你 fork 的 repo
   - **Environment**：選剛建好的 cloud environment
   - **Schedule**：選 *Weekdays*；時區設 `Asia/Taipei`、時間 `08:00`（內部會轉成你的本地時區）
   - **Prompt**（自然語言指令）：
     ```
     在 repo 根目錄執行 `python run_once.py`，
     將 stdout/stderr 輸出印出，並在執行結束後回報 exit code。
     不要修改任何檔案、不要 commit、不要建 PR。
     ```
   - 不需要 `Allow unrestricted branch pushes`，因為這個 routine 不寫回 repo。

4. **驗證** — Routine 頁面有 *Run now* 按鈕；按一下確認能拿到 Slack 推播。確認 OK 後排程才會生效。

### 限制

- Routines 目前 minimum interval 為 1 hour，但本服務只用 daily 觸發，沒影響。
- Routines 在 research preview 期間有每日次數上限與 GitHub trigger 上限；對「每天一次」的本服務遠遠夠用。
- 預估啟動冷啟時間 30s–60s（clone repo + setup script cache miss 時更長）；建議比目標時間早 5 分鐘排程。
- 因為不是長駐服務，**`/run` 手動觸發 API 與 `/` 健康檢查不可用**。手動觸發改用 routines 頁面的 *Run now* 按鈕。

---

## 🚀 部署選項二：Zeabur

1. Fork 此 repo
2. 登入 [Zeabur](https://zeabur.com) → New Project → Deploy from GitHub
3. **⚠️ 啟用 Submodule Clone**：Service → Settings → Source → 勾選 "Clone Submodules"（或於 `.zeabur/config.yaml` 設定），讓 Zeabur 在 build 階段自動 checkout `vendor/ai_hedge_fund`
4. 在 Variables 設定以下環境變數：

| 變數 | 必填 | 預設值 | 說明 | 取得方式 |
|------|:---:|--------|------|---------|
| `ANTHROPIC_API_KEY` | ✅ | — | Anthropic API Key | [console.anthropic.com](https://console.anthropic.com) |
| `FINNHUB_API_KEY` | ✅ | — | Finnhub API Key | [finnhub.io](https://finnhub.io) |
| `TAVILY_API_KEY` | ✅ | — | Tavily API Key | [tavily.com](https://tavily.com) |
| `SLACK_BOT_TOKEN` | ✅ | — | Slack Bot Token（`xoxb-...`） | Slack App → OAuth & Permissions |
| `SLACK_CHANNEL` | ✅ | — | 頻道 ID（建議）或 `#channel-name` | 頻道 → Get channel details → 底部 |
| `SLACK_SIGNING_SECRET` | | `""` | Slack signing secret；要用 slash command 才需要 | Slack App → Basic Information → App Credentials |
| `DATABASE_URL` | | `""` | PostgreSQL DSN；設定後 watchlist 改存 DB（推薦部署用），未設則 fallback 到檔案模式 | Zeabur Postgres Service 提供 `${...POSTGRES_CONNECTION_STRING}` 模板變數 |
| `WATCHLIST_PATH` | | `watchlist.json` | watchlist 檔案路徑（檔案模式用 / DB 模式只當 seed 來源）| — |
| `CRON_HOUR` | | `8` | 排程觸發小時 | — |
| `CRON_MINUTE` | | `0` | 排程觸發分鐘 | — |
| `TIMEZONE` | | `Asia/Taipei` | 時區 | — |
| `ADMIN_API_KEY` | | `""` | 手動觸發保護金鑰 | 自行設定高強度隨機字串 |
| `FINANCIAL_DATASETS_API_KEY` | | `""` | ai-hedge-fund 個股數據源；免費清單不需要 | [financialdatasets.ai](https://financialdatasets.ai) |
| `HEDGE_FUND_ANALYSTS` | | `warren_buffett,fundamentals_analyst,technical_analyst,sentiment_analyst` | 啟用的 AI 分析師（逗號分隔，亦接受短別名 fundamentals/technicals/sentiment） | — |
| `HEDGE_FUND_MODEL` | | `claude-haiku-4-6` | ai-hedge-fund 內部用的 Claude 模型 | — |
| `HEDGE_FUND_TIMEOUT` | | `240` | 整輪個股分析超時秒數 | — |

> ⚠️ 系統在**啟動時**即驗證所有必填變數，缺少任何 Key 服務會立即報錯，不會在運行途中才隱晦失敗。
> ⚠️ `ANTHROPIC_API_KEY` 會**同時**被主 pipeline 與 ai-hedge-fund 使用，不需要額外 OpenAI key。

---

## 🕹️ API 端點

### `GET /` — 健康檢查

```bash
curl https://your-service.zeabur.app/
```

```json
{
  "status": "ok",
  "service": "美股新聞編輯室",
  "next_run": "2026-03-13 08:00:00+08:00"
}
```

### `POST /run` — 手動觸發

```bash
# 設定了 ADMIN_API_KEY
curl -X POST https://your-service.zeabur.app/run \
  -H "authorization: your-admin-key"

# 未設定 ADMIN_API_KEY
curl -X POST https://your-service.zeabur.app/run
```

> 觸發後立即回傳，日報流程在背景非同步執行。每次觸發間隔至少 **300 秒**，過於頻繁回傳 HTTP 429。

### `POST /slack/command` — Slack Slash Command 共用入口

6 個 top-level slash command（無前綴）全部都把 Request URL 指到這一個端點，後端依 payload 的 `command` 欄位分派：

```
/status                          # 下次排程 / 上次結果 / 暫停狀態 / cooldown
/ping                            # 並行健檢 OpenAI / Finnhub / Tavily / Slack（+ PostgreSQL）
/run                             # 觸發日報（共用 300s cooldown）

/pause                           # 暫停排程（無限期）
/pause 30m                       # 暫停 30 分鐘後自動恢復
/pause 2h                        # 暫停 2 小時
/resume                          # 立即恢復

/watchlist                       # 列出自選股
/watchlist add AAPL NVDA         # 加（多檔以空格分隔）
/watchlist remove TSLA           # 移除
/watchlist clear                 # 互動按鈕二次確認後清空
```

所有回應都是 **ephemeral**（只有發起人看到）；`run` 的結果一樣推到 `SLACK_CHANNEL`。

> ⚠️ 因為命名不帶前綴，請確認 workspace 沒有其他 app 已經占用了同名 slash command（例如 GitHub / Datadog / PagerDuty）。若有衝突 Slack 會擋下後者註冊。

**安全層**（必須全部過才會被處理）：
1. `X-Slack-Signature` HMAC-SHA256 驗證
2. `X-Slack-Request-Timestamp` 5 分鐘內（防 replay）
3. 請求來源 channel == `SLACK_CHANNEL`
4. `SLACK_SIGNING_SECRET` 已設定（否則 endpoint 回 503）

### `POST /slack/interactivity` — Slack 互動元件入口

處理 `/watchlist clear` 的確認按鈕。同樣套用上面的 4 層安全驗證。

### Slack App 設定步驟

1. https://api.slack.com/apps → 你的 App
2. **Slash Commands** — 為下列 6 條各自 **Create New Command**，Request URL 全部填一樣：

   | Command | Short Description | Usage Hint |
   |---|---|---|
   | `/status` | 美股日報排程狀態 | _(留空)_ |
   | `/ping` | 美股日報連線健檢 | _(留空)_ |
   | `/run` | 立即觸發美股日報 | _(留空)_ |
   | `/pause` | 暫停美股日報排程 | `[30m \| 2h \| 1d]` |
   | `/resume` | 恢復美股日報排程 | _(留空)_ |
   | `/watchlist` | 美股日報自選股管理 | `[add \| remove \| clear] [TICKER...]` |

   - Request URL（每條都填）：`https://your-service.zeabur.app/slack/command`
   - 全部完成後 **Save**

3. **Interactivity & Shortcuts** → 開啟 **Interactivity**
   - Request URL：`https://your-service.zeabur.app/slack/interactivity`
   - **Save Changes**

4. **Basic Information** → **App Credentials** → 複製 **Signing Secret** → Zeabur Variables 設 `SLACK_SIGNING_SECRET`

5. **Install App** → **Reinstall to Workspace**（每次新增 slash command 都建議重裝一次，避免 workspace 端快取漏指令）

---

## 💻 本地開發

```bash
# 0. Clone 時一定要帶 --recurse-submodules 才會抓到 vendor/ai_hedge_fund
git clone --recurse-submodules https://github.com/jack926509/us-stock-newsletter.git
cd us-stock-newsletter
# 已經 clone 過但沒有 submodule：
# git submodule update --init --recursive

# 1. 建立虛擬環境
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. 安裝套件（會同時裝 langgraph / langchain / langchain-anthropic 供 ai-hedge-fund 用）
pip install -r requirements.txt

# 3. 設定環境變數
cp .env.example .env
# 編輯 .env 填入各 API Key

# 4. 執行單元測試（32 項）
pytest tests/ -v

# 5. 啟動服務
uvicorn main:app --reload
```

---

## 🔧 優化修改記錄

### v1 — 基礎架構

| 項目 | 說明 |
|------|------|
| HTML 切割安全修復 | 遞迴式切割改為迭代式，消除 Stack Overflow 風險 |
| OpenAI 並發保護 | 加入 `Semaphore(3)` 防止 Writer 並行觸發 429 |
| 錯誤告警推播 | Pipeline 嚴重失敗時自動推播 Slack 錯誤通知 |

### v2 — Telegram UX 初版 + Bug 修復

| 項目 | 說明 |
|------|------|
| 視覺分隔線 | Header 加入 `━` 分隔線，中英雙語標題 |
| 消息來源換行修復 | 來源連結直接黏在 `</blockquote>` 後的 Bug，加入 `\n` 分隔 |
| `reraise` 語意修正 | `finnhub.py` 移除與手動 `raise` 衝突的 `reraise=False` |
| 截斷長度統一 | 修正來源標題 `>15` 截 14 的邏輯矛盾 |
| config 說明修正 | `cron_hour` description 移除已過時的「美東時間」 |

### v3 — 全面程式碼品質修復（8 項）

| 項目 | 說明 |
|------|------|
| `tavily.py` reraise 修正 | 移除 `reraise=False` 與手動 `raise` 的語意衝突 |
| 測試格式修正 | `test_formatter.py` 日期斷言配合新的 `%Y/%m/%d` 格式 |
| `.env.example` 同步 | 預設值更新為 `CRON_HOUR=8`、`TIMEZONE=Asia/Taipei` |
| 錯誤告警 HTML 注入修復 | `pipeline.py` 的 `str(e)` 加入 `escape_html()` 防止 HTML 注入 |
| 未使用 import 清理 | `planner.py` 移除未使用的 `import json` |
| 背景 Task GC 修復 | `main.py` 儲存 `create_task()` 引用至 `_background_tasks` set，防止 GC 回收 |
| 過時註解清理 | `main.py` 移除「新寫好的模組引用」過時註解 |
| Tavily 頻寬節省 | `include_raw_content` 改為 `False`，Writer 未使用完整原文 |

### v5 — Anthropic API 遷移（本次）

| 項目 | 說明 |
|------|------|
| AI 供應商切換 | `openai` → `anthropic`，`AsyncOpenAI` → `AsyncAnthropic` |
| 模型升級 | `gpt-4o-mini` → `claude-haiku-4-5`（Planner + Writer）；`gpt-4o` → `claude-sonnet-4-6`（Editor） |
| 結構化輸出 | Anthropic SDK 0.49 的 tool-use（`tool_choice` + `input_schema`）取代手動 JSON / markdown code fence 解析 |
| Timeout 集中管理 | 各 call 分散的 `timeout=` 改為在 `AsyncAnthropic(timeout=90.0)` 統一設定 |
| 錯誤鏈保留 | Writer/Editor 的 `raise ... from e` 保留原始例外鏈，方便追查根因 |
| ValidationError 移除 | `messages.parse()` 內建驗證，planner/editor 不再需要 `except ValidationError` 分支 |
| 環境變數 | `OPENAI_API_KEY` → `ANTHROPIC_API_KEY` |

### v4 — Telegram UX 重構

| 項目 | 說明 |
|------|------|
| 智能訊息合併 | 新增 `_merge_blocks()` 將 6 個區塊合併為 2-3 則訊息，大幅降低通知轟炸 |
| Header 精簡化 | 雙分隔線 5 行 → 單行 `📰 美股日報 ── 日期` 僅 2 行，手機端節省 60% 垂直空間 |
| 章節進度編號 | 標題加入 `[1/3]` `[2/3]` `[3/3]`，讀者掌握閱讀位置 |
| 來源截斷上限提升 | 15 → 30 字元，保留更多標題語義 |
| 測試覆蓋擴充 | 4 項 → 12 項（新增 section、market card、merge 合併邏輯測試） |

---

## 🚧 未來可優化項目

### 功能擴充

- [ ] **更多市場指數**：加入 VIX 恐慌指數、原油（WTI）、黃金、比特幣，豐富大盤快照
- [ ] **多頻道支援**：允許設定多個 `SLACK_CHANNEL`，同時推播至多個 Slack 頻道
- [ ] **歷史對比**：記錄每日大盤數據，快照中加入「前日對比 / 週漲跌」欄位
- [ ] **市場情緒指數**：基於新聞語調與大盤走勢計算量化情緒分數（-5 到 +5），以圖示呈現

### AI 品質提升

- [ ] **新聞去重機制**：記錄已報導主題（Redis 或本地 JSON），避免連續多日重複同一話題
- [ ] **動態章節數量**：根據新聞豐富度彈性調整章節數（目前固定 3 個）
- [ ] **圖片卡片生成**：使用 DALL-E 或 Matplotlib 生成大盤走勢圖，透過 `sendPhoto` 推播
- [ ] **Writer 輸出結構化**：Writer 直接輸出 `Section` Pydantic 物件，省去 Editor 重新解析的成本

### 穩定性與效能

- [ ] **HTML 切割安全強化**：目前依 `\n\n` 切割，若 AI 產出無雙換行會硬切 HTML 標籤；改用解析器確保標籤完整性
- [ ] **Redis 快取**：快取 Finnhub 市場數據，重試或多次手動觸發時避免重複打 API
- [ ] **部分成功策略**：若 Editor JSON 驗證失敗，降級合併 Writer 草稿直接推播，不中斷整個流程
- [ ] **排程失敗自動重試**：若當日 Pipeline 失敗，自動在 30 分鐘後重試一次

### 可觀測性

- [ ] **結構化日誌**：改用 `structlog` 輸出 JSON 格式 Log，方便在 Zeabur 上過濾查詢
- [ ] **執行時間追蹤**：記錄每個 Step 的耗時，識別效能瓶頸
- [ ] **OpenTelemetry 整合**：接入分散式追蹤，監控 AI API 呼叫延遲與成功率

### 部署與維運

- [ ] **Zeabur Cron Job**：改用 Zeabur 原生 Cron Job 取代 APScheduler，更適合無狀態容器
- [ ] **多平台推播**：擴充支援 Discord Webhook、LINE Notify、Slack Bot
- [ ] **GitHub Actions CI**：PR 自動跑 `pytest` + `ruff` 靜態分析
- [ ] **Docker Compose 本地環境**：提供含 Redis 的完整本地開發環境設定
