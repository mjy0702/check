#!/bin/bash
set -e

cd "$(dirname "$0")/backend"

echo "패키지 설치 확인..."
pip install -r requirements.txt -q

if [ -f ../.env ]; then
  export $(grep -v '^#' ../.env | xargs)
fi

echo "서버 시작: http://localhost:8000"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
