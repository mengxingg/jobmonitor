#!/bin/bash
# ============================================================
# run_precision_crawl.sh — 精准爬取全链路脚本
#
# 针对 DeepSeek 和 小红书 两个渠道，使用带 keyword 参数的
# 精准搜索 URL，只抓取 AI 产品经理岗位。
#
# 流程：
#   1. 清理 Notion 中 DeepSeek 的旧脏数据（会计、行政岗）
#   2. 抓取 DeepSeek (Moka 系统) 产品经理岗位
#   3. 抓取小红书 AI 产品经理岗位
#   4. AI 评分 + Notion 同步（由各爬虫自动触发）
# ============================================================

set -e

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
LOG_FILE="${PROJECT_DIR}/logs/precision_crawl_$(date '+%Y%m%d_%H%M%S').log"

# 确保目录存在
mkdir -p "${PROJECT_DIR}/logs"
mkdir -p "${PROJECT_DIR}/data"

echo "========================================" | tee -a "$LOG_FILE"
echo " 🎯 精准爬取全链路脚本" | tee -a "$LOG_FILE"
echo " 时间: $TIMESTAMP" | tee -a "$LOG_FILE"
echo " 目标: DeepSeek + 小红书 (AI 产品经理)" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# ── Step 1: 清理 DeepSeek 旧脏数据 ──
echo "[Step 1/4] 清理 Notion 中 DeepSeek 旧脏数据..." | tee -a "$LOG_FILE"
conda run -n job_env python "${PROJECT_DIR}/cleanup_deepseek.py" 2>&1 | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# ── Step 2: 抓取 DeepSeek ──
echo "[Step 2/4] 抓取 DeepSeek (Moka 系统) 产品经理岗位..." | tee -a "$LOG_FILE"
echo "  URL: https://app.mokahr.com/social-recruitment/high-flyer/140576#/jobs?keyword=产品经理" | tee -a "$LOG_FILE"
conda run -n job_env python "${PROJECT_DIR}/crawler_deepseek.py" 2>&1 | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# ── Step 3: 抓取小红书 ──
echo "[Step 3/4] 抓取小红书 AI 产品经理岗位..." | tee -a "$LOG_FILE"
echo "  URL: https://job.xiaohongshu.com/social/position?positionName=AI产品经理" | tee -a "$LOG_FILE"
conda run -n job_env python "${PROJECT_DIR}/crawler_xiaohongshu.py" 2>&1 | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# ── Step 4: 最终检查 ──
echo "[Step 4/4] 检查最终结果..." | tee -a "$LOG_FILE"
if [ -f "${PROJECT_DIR}/data/openclaw_jobs.json" ]; then
    JOB_COUNT=$(cat "${PROJECT_DIR}/data/openclaw_jobs.json" | python3 -c "
import sys, json
data = json.load(sys.stdin)
deepseek = [j for j in data if j.get('company') == 'DeepSeek']
xhs = [j for j in data if j.get('company') == '小红书']
print(f'DeepSeek: {len(deepseek)} 条, 小红书: {len(xhs)} 条, 总计: {len(data)} 条')
" 2>/dev/null || echo "解析失败")
    echo "  📊 $JOB_COUNT" | tee -a "$LOG_FILE"
else
    echo "  ⚠️ 未找到输出文件" | tee -a "$LOG_FILE"
fi

echo "" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo " ✅ 精准爬取全链路执行完毕" | tee -a "$LOG_FILE"
echo " 日志: $LOG_FILE" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
