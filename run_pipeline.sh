#!/bin/bash
# ============================================================
# run_pipeline.sh
# 全链路闭环：OpenClaw 抓取 → AI 评分 → Notion 同步
#
# 使用方法：
#   chmod +x run_pipeline.sh
#   ./run_pipeline.sh
#
# 数据流：
#   1. 同步 targets.json 到 OpenClaw skill 目录
#   2. 通过 OpenClaw agent 调用 job-monitor skill 抓取所有目标公司
#   3. 输出到 data/openclaw_jobs.json
#   4. openclaw_bridge.py 读取 → ai_matcher 评分 → notion_sync 写入 Notion
# ============================================================

set -e

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
LOG_FILE="${PROJECT_DIR}/logs/pipeline_$(date '+%Y%m%d_%H%M%S').log"

# 确保 logs 目录存在
mkdir -p "${PROJECT_DIR}/logs"
mkdir -p "${PROJECT_DIR}/data"

echo "========================================" | tee -a "$LOG_FILE"
echo " Job Monitor 全链路流水线" | tee -a "$LOG_FILE"
echo " 时间: $TIMESTAMP" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# ── Step 1: 同步 targets.json 到 OpenClaw skill 目录 ──
echo "[Step 1/4] 同步 targets.json 到 OpenClaw skill 目录..." | tee -a "$LOG_FILE"
cp "${PROJECT_DIR}/targets.json" ~/.openclaw/workspace/skills/job-monitor/targets.json
echo "  ✓ 已同步 $(cat targets.json | grep -c '"company"') 家目标公司" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# ── Step 2: 通过 OpenClaw agent 运行 job-monitor skill ──
echo "[Step 2/4] 运行 OpenClaw agent 执行 job-monitor 抓取任务..." | tee -a "$LOG_FILE"
echo "  (使用 browser-automation + crawl4ai 渲染 SPA 页面)" | tee -a "$LOG_FILE"

openclaw agent \
  --local \
  --agent main \
  -m "请执行 job-monitor 技能：根据 targets.json 中的公司列表，逐一访问各公司的官方招聘页面，抓取 AI 产品经理相关岗位。将结果保存到 ${PROJECT_DIR}/data/openclaw_jobs.json" \
  2>&1 | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"

# ── Step 3: 检查抓取结果 ──
echo "[Step 3/4] 检查抓取结果..." | tee -a "$LOG_FILE"
if [ -f "${PROJECT_DIR}/data/openclaw_jobs.json" ]; then
    JOB_COUNT=$(cat "${PROJECT_DIR}/data/openclaw_jobs.json" | python3 -c "import sys,json; data=json.load(sys.stdin); print(len(data))" 2>/dev/null || echo "0")
    echo "  ✓ 抓取完成，共获取 ${JOB_COUNT} 条岗位" | tee -a "$LOG_FILE"
else
    echo "  ⚠ 未找到输出文件，可能抓取未完成" | tee -a "$LOG_FILE"
fi
echo "" | tee -a "$LOG_FILE"

# ── Step 4: 桥接 → AI 评分 → Notion 同步 ──
echo "[Step 4/4] 执行桥接脚本 (AI 评分 + Notion 同步)..." | tee -a "$LOG_FILE"
conda run -n job_env python "${PROJECT_DIR}/openclaw_bridge.py" 2>&1 | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo " 流水线执行完毕" | tee -a "$LOG_FILE"
echo " 日志: $LOG_FILE" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
