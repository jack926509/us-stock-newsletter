FROM python:3.12-slim

WORKDIR /app

# 先複製 requirements.txt 以善用 Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製其餘代碼
COPY . .

# 建立非 root 執行環境以提升安全性
RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
