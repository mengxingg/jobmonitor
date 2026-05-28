#!/bin/bash
# 一键停止：Node 卡片引擎 + Python 飞书网关
set -euo pipefail

JOB_ENGINE="${JOB_ENGINE_ROOT:-$HOME/interview/job_engine}"
PORTFOLIO="${PORTFOLIO_ROOT:-$HOME/my-ai-portfolio}"
NODE_PORT="${FEISHU_NODE_PORT:-3001}"

kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti:"${port}" 2>/dev/null || true)"
  if [ -n "${pids}" ]; then
    echo "${pids}" | tr ' ' '\n' | while read -r pid; do
      [ -n "${pid}" ] && kill -9 "${pid}" 2>/dev/null || true
    done
  fi
}

kill_pid_file() {
  local file="$1"
  if [ -f "${file}" ]; then
    local pid
    pid="$(cat "${file}" 2>/dev/null || true)"
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
      kill -9 "${pid}" 2>/dev/null || true
    fi
    rm -f "${file}"
  fi
}

echo "🛑 正在终止双引擎服务..."

kill_pid_file "${PORTFOLIO}/.feishu_node.pid"
kill_pid_file "${JOB_ENGINE}/.feishu_gateway.pid"

kill_port "${NODE_PORT}"
echo "✅ Node.js 渲染引擎已关闭 (${NODE_PORT})。"

pkill -f "feishu-local-api" 2>/dev/null || true
pkill -f "feishu_gateway.py" 2>/dev/null || true
echo "✅ Python 飞书总闸已关闭。"

echo "💤 系统已休眠。"
