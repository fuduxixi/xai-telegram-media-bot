#!/bin/sh
# Author: by fuduxixi
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
LOG_DIR="$APP_DIR/logs"
LOG_FILE="$LOG_DIR/bot.log"
mkdir -p "$LOG_DIR"

cd "$APP_DIR"
export PYTHONUNBUFFERED=1

python "$APP_DIR/telegram_xai_media_bot.py" 2>&1 | tee -a "$LOG_FILE"
