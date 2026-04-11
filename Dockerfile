# Author: by fuduxixi
FROM python:3.12-alpine AS bot

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements-bot.txt ./requirements.txt
RUN pip install --no-compile -r requirements.txt

COPY telegram_xai_media_bot.py ./
COPY scripts/run_bot_docker.sh ./scripts/run_bot_docker.sh
RUN mkdir -p /app/tmp /app/logs /app/data && chmod +x /app/scripts/run_bot_docker.sh

ENV BOT_LOG_LEVEL=INFO
CMD ["./scripts/run_bot_docker.sh"]

FROM python:3.12-alpine AS web

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements-web.txt ./requirements.txt
RUN pip install --no-compile -r requirements.txt

COPY web-config.py ./
RUN mkdir -p /app/tmp /app/logs /app/data

ENV FLASK_ENV=production
EXPOSE 5000
CMD ["python", "web-config.py"]
