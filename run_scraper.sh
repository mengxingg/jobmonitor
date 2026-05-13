#!/bin/bash
# 加载 conda 环境
source /Users/gmx/opt/anaconda3/etc/profile.d/conda.sh
conda activate job_env

# 进入项目目录并执行
cd /Users/gmx/interview/job_engine
/Users/gmx/opt/anaconda3/envs/job_env/bin/python scraper_drission.py >> scraper_cron.log 2>&1
