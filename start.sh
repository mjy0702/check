#!/bin/bash
set -e

cd "$(dirname "$0")/backend"

echo "패키지 설치 확인..."
pip install -r requirements.txt -q

echo "Playwright Chromium 설치..."
python -m playwright install chromium

if [ -f ../.env ]; then
  export $(grep -v '^#' ../.env | xargs)
fi

PORT=${PORT:-8000}
echo "서버 시작: port $PORT"
uvicorn main:app --host 0.0.0.0 --port "$PORT"
