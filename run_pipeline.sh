#!/bin/bash
# ============================================================
# run_pipeline.sh — 全链路闭环：全平台抓取 → AI 评分 → Notion 同步
#
# 使用方法：
#   chmod +x run_pipeline.sh
#   ./run_pipeline.sh
#
# 数据流：
#   1. 同步 targets.json 到 OpenClaw skill 目录
#   2. 通过 scheduler.py 串行运行所有爬虫（三方平台 + 官网）
#   3. 最后统一触发 openclaw_bridge.py 推送到 Notion
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
echo "[Step 1/3] 同步 targets.json 到 OpenClaw skill 目录..." | tee -a "$LOG_FILE"
cp "${PROJECT_DIR}/targets.json" ~/.openclaw/workspace/skills/job-monitor/targets.json
echo "  ✓ 已同步 $(grep -c '"company"' targets.json 2>/dev/null || echo 0) 家目标公司" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# ── Step 2: 全平台抓取（scheduler v2.0） ──
echo "[Step 2/3] 全平台抓取（scheduler v2.0）..." | tee -a "$LOG_FILE"
echo "  三方平台: BOSS直聘 + 猎聘" | tee -a "$LOG_FILE"
echo "  官网: 字节跳动 + DeepSeek + 小红书 + 腾讯 + 智谱AI + MiniMax + 月之暗面 + 阿里巴巴" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# ★ 关键修改：不使用 2>&1，让 print/logger 实时回显到终端
conda run -n job_env python "${PROJECT_DIR}/scheduler.py" --no-bridge 2>&1 | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"

# ── Step 3: 统一桥接 → AI 评分 → Notion 同步 ──
echo "[Step 3/3] 执行桥接脚本 (AI 评分 + Notion 同步)..." | tee -a "$LOG_FILE"

# ★ 同步前置检查
OUTPUT_FILE="${PROJECT_DIR}/data/openclaw_jobs.json"
if [ -f "$OUTPUT_FILE" ]; then
    FILE_SIZE=$(stat -f%z "$OUTPUT_FILE" 2>/dev/null || stat -c%s "$OUTPUT_FILE" 2>/dev/null || echo 0)
    if [ "$FILE_SIZE" -lt 1024 ]; then
        echo "  ⚠️ 文件大小异常 (${FILE_SIZE} bytes < 1KB)，跳过 Notion 写入" | tee -a "$LOG_FILE"
    else
        # ★ 关键修改：不使用 2>&1，让 print/logger 实时回显
        conda run -n job_env python "${PROJECT_DIR}/openclaw_bridge.py" 2>&1 | tee -a "$LOG_FILE"
    fi
else
    echo "  ⚠️ 未找到输出文件，跳过 Notion 写入" | tee -a "$LOG_FILE"
fi

echo "" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo " 流水线执行完毕" | tee -a "$LOG_FILE"
echo " 日志: $LOG_FILE" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
