#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_SESSION="ctos-backend-8000"
FRONTEND_SESSION="ctos-frontend-5173"

mkdir -p "$ROOT_DIR/logs"

# 后台开发服务不要使用 uvicorn --reload；reload 子进程在 detached 环境里会丢 stdin。
screen -S "$BACKEND_SESSION" -X quit >/dev/null 2>&1 || true
screen -S "$FRONTEND_SESSION" -X quit >/dev/null 2>&1 || true

stop_port() {
  local port="$1"
  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    kill $pids >/dev/null 2>&1 || true
    sleep 1
  fi
}

stop_port 8000
stop_port 5173

screen -dmS "$BACKEND_SESSION" bash -lc \
  "cd '$ROOT_DIR' && exec venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port 8000 > logs/uvicorn-8000.log 2>&1"

screen -dmS "$FRONTEND_SESSION" bash -lc \
  "cd '$ROOT_DIR/web' && exec npm run dev -- --host 0.0.0.0 --port 5173 > ../logs/vite-5173.log 2>&1"

echo "Backend:  http://localhost:8000"
echo "Frontend: http://localhost:5173"
echo "Logs:     $ROOT_DIR/logs/uvicorn-8000.log, $ROOT_DIR/logs/vite-5173.log"
