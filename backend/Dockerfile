# 后端镜像：Python 3.12 slim + FastAPI
FROM python:3.12-slim

WORKDIR /app

# 先复制依赖清单利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
