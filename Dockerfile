FROM python:3.12-slim

WORKDIR /app

# git 用於 Zeabur build 階段補 checkout submodule；gcc 給部分套件編譯用
RUN apt-get update \
    && apt-get install -y --no-install-recommends git gcc \
    && rm -rf /var/lib/apt/lists/*

# 先複製 requirements.txt 以善用 Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製其餘代碼（含 vendor/ai_hedge_fund submodule 內容）
COPY . .

# 若 Zeabur 未自動 checkout submodule，於 build 階段補救
RUN if [ -f .gitmodules ] && [ ! -f vendor/ai_hedge_fund/src/main.py ]; then \
      git submodule update --init --recursive || echo "WARN: submodule init failed"; \
    fi

# 建立非 root 執行環境以提升安全性
RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
