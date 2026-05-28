#!/bin/bash
# 一键启动：Node 卡片引擎 (3001) + Python 飞书 WebSocket 网关
set -euo pipefail

JOB_ENGINE="${JOB_ENGINE_ROOT:-$HOME/interview/job_engine}"
PORTFOLIO="${PORTFOLIO_ROOT:-$HOME/my-ai-portfolio}"
NODE_PORT="${FEISHU_NODE_PORT:-3001}"

kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti:"${port}" 2>/dev/null || true)"
  if [ -n "${pids}" ]; then
    # macOS xargs 无 -r，用 while 兼容
    echo "${pids}" | tr ' ' '\n' | while read -r pid; do
      [ -n "${pid}" ] && kill -9 "${pid}" 2>/dev/null || true
    done
  fi
}

has_job_env() {
  command -v conda >/dev/null 2>&1 && conda env list 2>/dev/null | grep -qE '^job_env\s'
}

echo "🚀 [1/3] 正在清理历史遗留进程..."
kill_port "${NODE_PORT}"
pkill -f "feishu_gateway.py" 2>/dev/null || true
pkill -f "feishu-local-api" 2>/dev/null || true
sleep 2

if [ ! -d "${PORTFOLIO}" ]; then
  echo "❌ 找不到作品集目录: ${PORTFOLIO}"
  exit 1
fi
if [ ! -d "${JOB_ENGINE}" ]; then
  echo "❌ 找不到 job_engine 目录: ${JOB_ENGINE}"
  exit 1
fi

echo "🟢 [2/3] 启动 Node.js AI 新闻渲染引擎..."
cd "${PORTFOLIO}"
nohup npm run feishu-local-api > node_api.log 2>&1 &
NODE_PID=$!
echo "${NODE_PID}" > "${PORTFOLIO}/.feishu_node.pid"
echo "✅ Node 引擎已在后台 ${NODE_PORT} 端口待命 (PID ${NODE_PID})。"

echo "🐍 [3/3] 启动 Python 飞书总闸网关..."
cd "${JOB_ENGINE}"
# -u：无缓冲日志，tail -f gateway.log 可实时看到输出
if has_job_env; then
  nohup conda run --no-capture-output -n job_env python -u feishu_gateway.py > gateway.log 2>&1 &
else
  if command -v python3 >/dev/null 2>&1; then
    nohup python3 -u feishu_gateway.py > gateway.log 2>&1 &
  else
    nohup python -u feishu_gateway.py > gateway.log 2>&1 &
  fi
fi
PY_PID=$!
echo "${PY_PID}" > "${JOB_ENGINE}/.feishu_gateway.pid"
echo "✅ Python 哨兵已进入 24 小时潜行监听状态 (PID ${PY_PID})。"

echo ""
echo "🎉 双引擎点火完毕！"
echo "👉 查看 Node 日志:   tail -f ${PORTFOLIO}/node_api.log"
echo "👉 查看 Python 日志: tail -f ${JOB_ENGINE}/gateway.log"
echo "👉 停止服务:        ${JOB_ENGINE}/stop_feishu.sh"
