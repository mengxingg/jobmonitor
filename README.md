# 🎯 Job Engine — AI 驱动的智能岗位抓取与匹配引擎

> **自动抓取 BOSS直聘 / 猎聘 / 字节跳动 / 小红书 / DeepSeek / 月之暗面 / 智谱AI / MiniMax / 腾讯 → DeepSeek AI 匹配评估 → Notion 数据库同步**  
> 专为 AI 产品经理（AI PM）求职场景设计，一套全自动化的岗位筛选流水线。

![alt text](image.png)

---

## 🚀 JobMonitor: 基于 AI 驱动的职业情报哨兵系统

JobMonitor 是一个专门为 **AI 产品经理** 打造的高频职业机会捕捉系统。它不仅仅是一个爬虫，而是一套集成了自动化采集、防封禁策略、大模型深度评估、结构化数据同步的个人招聘操作系统（InterviewOS）后端引擎。

---

## 🌟 项目亮点 (Key Highlights)

- **拟人化对抗策略**：针对招聘平台严苛的风控（如猎聘账号异常验证），集成了 Jitter Sleep（随机休眠）、模拟人类滚动浏览等行为指纹，有效延长账号爬取周期。
- **多模态 JD 解析**：自适应企业直招（`/job/`）与猎头代理（`/a/`）两种完全不同的页面 DOM 结构，确保 100% 提取完整岗位职责，拒绝低价值摘要。
- **全局去重 (Deduplication)**：基于"纯净 URL"哈希的全局去重机制，确保同一岗位在不同日期仅被处理一次，极致节省 DeepSeek API Token 成本。
- **AI 深度评估器**：利用 DeepSeek-V3 对 JD 进行 0-100 分匹配度测评，自动提取岗位亮点、入职风险及面试建议。
- **多平台覆盖**：支持 9 大招聘渠道（BOSS直聘、猎聘、字节跳动、小红书、DeepSeek、月之暗面、智谱AI、MiniMax、腾讯），统一数据模型。

---

## 🏗️ 系统架构 (Architecture)

```
数据采集层
  ├── DrissionPage 接管本地 Chrome（BOSS直聘 / 猎聘）
  ├── Playwright 有界面浏览器（小红书 / 字节跳动 / 月之暗面 / 智谱AI / MiniMax / 腾讯）
  └── requests API 直连（腾讯 / 字节跳动 / 月之暗面 / 智谱AI / MiniMax）

逻辑控制层
  ├── Risk Control Interceptor（风控拦截器）
  ├── 拟人化行为模拟（随机休眠、平滑滚动、鼠标轨迹）
  └── 降级抓取策略（CSS 失效 → inner_text 全文兜底）

智能评估层
  └── DeepSeek API 5 维匹配评分（匹配度 / 薪资 / 地点 / 发展 / 团队）

数据展示层
  ├── Notion API 同步（URL 去重 + 增量更新）
  └── Electron 可视化看板（地图分布 + 评分统计）
```

---

## 📋 项目概览

Job Engine 是一个**端到端的智能求职辅助工具**，核心流程如下：

```
招聘网站列表页
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  ① 多平台爬虫（9 大渠道）                                        │
│     - BOSS直聘 / 猎聘（DrissionPage 自动化浏览器）                │
│     - 字节跳动 / 小红书 / DeepSeek（Playwright 有界面浏览器）      │
│     - 月之暗面 / 智谱AI / MiniMax（Playwright + API 双通道）      │
│     - 腾讯（requests API 直连）                                   │
│     - 拟人化行为模拟（随机休眠、平滑滚动、防反爬）                  │
│     - 产品经理关键词过滤 + 公司黑名单过滤                          │
│     - 全局去重（本地 JSON 缓存 + URL 主键）                       │
└──────────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  ② 详情页 JD 提取                                                │
│     - 访问每个岗位的详情页                                        │
│     - API 拦截 / DOM 提取 / inner_text 降级 三通道获取职位描述     │
│     - 三道风控防线（拟人休眠 + 模拟浏览 + 风控哨兵）               │
│     - 原子化保存：每成功一条即刻写入 JSON                          │
└──────────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  ③ DeepSeek AI 匹配评估                                          │
│     - 5 维评分：匹配度(30%) / 薪资(25%) / 地点(15%)               │
│                  / 发展(15%) / 团队(15%)                          │
│     - 输出：评分 + 优势/不足分析 + 一句话总结                      │
└──────────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  ④ Notion 数据库同步                                              │
│     - 自动去重（URL 查重，已存在则 PATCH 更新 / 否则 SKIP）        │
│     - 写入：岗位信息 + AI 评分 + 匹配分析                          │
│     - 支持批量同步 + 指数退避重试                                  │
└──────────────────────────────────────────────────────────────────┘
    │
    ▼
📊 AI PM Job Dashboard（可视化看板）
   - 基于 Electron + 地图可视化的岗位数据展示
   - 支持 Netlify 部署
```

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| **🤖 多平台支持** | 9 大渠道：BOSS直聘、猎聘、字节跳动、小红书、DeepSeek、月之暗面、智谱AI、MiniMax、腾讯 |
| **🛡️ 反爬策略** | 拟人化随机休眠、平滑滚动、API 拦截优先、DOM 兜底、inner_text 降级、风控哨兵 |
| **🧠 AI 评估** | 基于 DeepSeek API 的 5 维岗位匹配评分，含优劣势分析 |
| **📋 Notion 同步** | 自动去重（新建/更新），完整的字段映射，指数退避重试 |
| **🚫 黑名单过滤** | 支持公司黑名单（如字节、腾讯等大厂），自动跳过 |
| **🎓 学历过滤** | 前置过滤低于本科的岗位，节省 Token |
| **📝 错题本** | 处理失败的岗位自动记录到 `failed_jobs_inbox.md` |
| **🔄 定时调度** | 支持 PM2 托管或内建 schedule 定时执行 |
| **📊 可视化看板** | 配套 Electron 桌面应用，地图展示岗位分布 |
| **🔒 环境锁定** | 所有指令强制 `conda run -n job_env`，禁止 base 环境运行 |
| **💾 原子化保存** | 每成功一条即刻写入 JSON，防止中途崩溃数据丢失 |

---

## 🏗️ 项目结构

```
job_engine/
├── main.py                  # 🚀 主入口（读取当前 Chrome 标签页 → AI → Notion）
├── scheduler.py             # ⏰ 全平台爬虫调度器 v2.0（三方 + 官网 共 10 个爬虫）
├── openclaw_bridge.py       # 🔗 OpenClaw → AI Matcher → Notion 桥接脚本
│
├── spider_boss.py           # 🕷️ BOSS直聘爬虫（DrissionPage）
├── spider_liepin.py         # 🕷️ 猎聘爬虫（DrissionPage）
├── scraper.py               # 🔌 旧版抓取模块（Playwright CDP 只读模式）
│
├── crawler_deepseek.py      # 🕷️ DeepSeek 招聘爬虫（Playwright）
├── crawler_xiaohongshu.py   # 🕷️ 小红书招聘爬虫（Playwright）
├── crawler_moonshot.py      # 🕷️ 月之暗面（Kimi）招聘爬虫（Playwright）
├── crawler_zhipu.py         # 🕷️ 智谱 AI 招聘爬虫（Playwright）
├── crawler_minimax.py       # 🕷️ MiniMax（稀宇科技）招聘爬虫（Playwright）
├── crawler_tencent.py       # 🕷️ 腾讯招聘爬虫（requests API 直连）
├── bytedance_visual_crawler.py  # 🕷️ 字节跳动招聘爬虫（Playwright）
├── moka_standard_crawler.py # 🕷️ Moka 标准招聘系统爬虫
│
├── job_model.py             # 📦 标准化岗位数据模型 JobItem
├── ai_matcher.py            # 🧠 DeepSeek AI 匹配评估模块
├── notion_sync.py           # 📋 Notion 数据库同步模块
├── config.py                # ⚙️ 配置加载（环境变量 + 黑名单）
├── login_auth.py            # 🔐 浏览器登录授权辅助脚本
│
├── run_boss.py              # ▶️ BOSS直聘独立执行入口
├── run_liepin.py            # ▶️ 猎聘独立执行入口
├── test_pipeline.py         # 🧪 测试流水线（读取本地 JSON → AI → Notion）
├── run_pipeline.sh          # 🐚 全链路 Shell 脚本（同步 targets → 全平台抓取 → Notion）
├── run_precision_crawl.sh   # 🐚 精准爬取 Shell 脚本（DeepSeek + 小红书）
│
├── run_scraper.sh           # 🐚 Shell 启动脚本（conda 环境）
├── requirements.txt         # 📦 Python 依赖
├── .env                     # 🔑 环境变量（API Key 等）
├── blacklist.txt            # 🚫 公司黑名单
├── .clinerules              # 📋 Cline 项目规则（开发规范）
│
├── data/
│   ├── openclaw_jobs.json   # 📊 主数据文件（所有爬虫汇总）
│   ├── test_jobs.json       # 🧪 测试数据（10 条模拟岗位）
│   └── xhs_simple_jobs.json # 📊 小红书独立数据
│
├── screenshots/             # 📸 抓取过程截图
├── logs/                    # 📝 日志文件
│
├── failed_jobs_inbox.md     # 📝 处理失败的岗位记录
├── processed_jobs.txt       # ✅ BOSS 已处理岗位缓存
├── history_jobs.json        # ✅ 猎聘已处理岗位缓存
│
├── .chrome_profile/         # 🌐 BOSS直聘浏览器用户数据
├── .chrome_profile_liepin/  # 🌐 猎聘浏览器用户数据
│
└── ai-pm-job-dashboard/     # 📊 Electron 可视化看板（独立子项目）
    ├── app/                 #   前端页面
    ├── electron/            #   Electron 主进程
    ├── scripts/             #   数据处理脚本
    └── docs/                #   文档
```

---

## 🚀 快速开始

### 前置条件

- Python 3.10+
- Chrome 浏览器（用于 DrissionPage 自动化）
- DeepSeek API Key
- Notion API Key + 数据库
- Conda 环境 `job_env`（所有命令强制使用）

### 安装

```bash
# 1. 克隆项目
git clone <your-repo-url>
cd job_engine

# 2. 安装依赖（必须在 job_env 环境中）
conda run -n job_env pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env   # 或直接编辑 .env
# 填入你的 API Key：
#   DEEPSEEK_API_KEY=sk-xxx
#   NOTION_API_KEY=ntn_xxx
#   NOTION_JOBS_DB=你的 Notion 数据库 ID
```

### 使用方式

#### 🎯 方式一：读取当前 Chrome 标签页（快速上手）

```bash
# 1. 启动 Chrome，打开远程调试端口
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222

# 2. 在 Chrome 中打开 BOSS直聘列表页（如 https://www.zhipin.com/web/geek/jobs?query=AI产品经理）

# 3. 运行主程序
conda run -n job_env python main.py                    # 完整流程：抓取 → AI → Notion
conda run -n job_env python main.py --dry-run          # 仅抓取 + AI 评估，不写入 Notion
conda run -n job_env python main.py --print-only       # 仅打印结果到控制台
```

#### 🕷️ 方式二：独立爬虫

```bash
# BOSS直聘
conda run -n job_env python run_boss.py                          # 完整流程
conda run -n job_env python run_boss.py --dry-run                # 仅抓取预览

# 猎聘
conda run -n job_env python run_liepin.py --full                 # 完整流程
conda run -n job_env python run_liepin.py                        # 默认 dry-run 安全模式
conda run -n job_env python run_liepin.py --keyword "AI产品经理"  # 指定关键词
conda run -n job_env python run_liepin.py --max-pages 3          # 限制翻页数

# 字节跳动
conda run -n job_env python bytedance_visual_crawler.py

# 小红书
conda run -n job_env python crawler_xiaohongshu.py

# DeepSeek
conda run -n job_env python crawler_deepseek.py

# 月之暗面（Kimi）
conda run -n job_env python crawler_moonshot.py

# 智谱 AI
conda run -n job_env python crawler_zhipu.py

# MiniMax（稀宇科技）
conda run -n job_env python crawler_minimax.py

# 腾讯
conda run -n job_env python crawler_tencent.py
```

#### ⏰ 方式三：全平台一键同步（推荐）

```bash
# 全量同步：抓取所有平台 → AI 评分 → Notion 推送
conda run -n job_env python scheduler.py

# 仅抓取不推送 Notion
conda run -n job_env python scheduler.py --no-bridge
```

> **⚠️ 登录注意事项（重要）**
>
> 以下爬虫需要手动扫码登录，首次运行**必须在交互式终端中执行**：
>
> | 爬虫 | 需要登录 | 说明 |
> |------|---------|------|
> | **BOSS直聘** (`spider_boss.py`) | ✅ 需要 | 首次运行会弹出浏览器窗口，请扫码登录 BOSS直聘 |
> | **猎聘** (`spider_liepin.py`) | ✅ 需要 | 首次运行会弹出浏览器窗口，请扫码登录猎聘 |
> | **字节跳动** (`bytedance_visual_crawler.py`) | ❌ 无需 | 官网招聘页面无需登录 |
> | **小红书** (`crawler_xiaohongshu.py`) | ❌ 无需 | 官网招聘页面无需登录 |
> | **DeepSeek** (`crawler_deepseek.py`) | ❌ 无需 | Moka 系统无需登录 |
> | **月之暗面** (`crawler_moonshot.py`) | ❌ 无需 | Moka 系统无需登录 |
> | **智谱AI** (`crawler_zhipu.py`) | ❌ 无需 | Moka 系统无需登录 |
> | **MiniMax** (`crawler_minimax.py`) | ❌ 无需 | 飞书招聘系统无需登录 |
> | **腾讯** (`crawler_tencent.py`) | ❌ 无需 | API 直连，无需浏览器 |
>
> **登录一次后，Cookie 会保存在本地 Chrome Profile 中**（`.chrome_profile/` 和 `.chrome_profile_liepin/`），后续运行会自动复用登录态，无需重复扫码。
>
> 如果使用 `scheduler.py` 全量运行，调度器会按顺序依次启动各爬虫。当运行到 BOSS 或猎聘时，浏览器窗口会自动弹出，请完成扫码后回到终端按回车继续。

#### 🧪 方式四：通过 Shell 脚本一键运行（带日志归档）

```bash
# 全链路流水线（同步 targets → 全平台抓取 → Notion 推送）
conda run -n job_env bash run_pipeline.sh

# 精准爬取（仅 DeepSeek + 小红书）
conda run -n job_env bash run_precision_crawl.sh
```

> `run_pipeline.sh` 会自动将日志归档到 `logs/` 目录，方便回溯排查。

#### 🧪 方式五：测试流水线

```bash
conda run -n job_env python test_pipeline.py                     # 完整流程
conda run -n job_env python test_pipeline.py --dry-run           # 仅 AI 评估
conda run -n job_env python test_pipeline.py --print-only        # 仅打印原始数据
```

---

## 📊 已覆盖平台总览

目前项目已覆盖 **9 个招聘渠道**，所有数据统一存储在 `data/openclaw_jobs.json` 中：

| # | 平台 | 爬虫脚本 | 抓取方式 | 已抓取数量 |
|---|------|---------|---------|-----------|
| 1 | **BOSS直聘** | `spider_boss.py` / `run_boss.py` | DrissionPage 浏览器自动化 | ✅ 已集成 |
| 2 | **猎聘** | `spider_liepin.py` / `run_liepin.py` | DrissionPage 浏览器自动化 | ✅ 已集成 |
| 3 | **字节跳动** | `bytedance_visual_crawler.py` | Playwright 浏览器 | ✅ 已集成 |
| 4 | **小红书** | `crawler_xiaohongshu.py` | Playwright 浏览器 | ✅ 已集成 |
| 5 | **DeepSeek** | `crawler_deepseek.py` | Playwright 浏览器 | ✅ 已集成 |
| 6 | **月之暗面 (Kimi)** | `crawler_moonshot.py` | Playwright + API 双通道 | ✅ 55 条 |
| 7 | **智谱 AI** | `crawler_zhipu.py` | Playwright + API 双通道 | ✅ 11 条 |
| 8 | **MiniMax (稀宇科技)** | `crawler_minimax.py` | Playwright 浏览器 | ✅ 4 条 |
| 9 | **腾讯** | `crawler_tencent.py` | requests API 直连 | ✅ 22 条 |

> 💡 **提示**：每个爬虫运行后会自动触发 `openclaw_bridge.py` 将数据同步到 Notion 数据库，无需手动操作。

---

## ⏰ 自动定时获取数据

### 方式一：使用 PM2 托管（推荐）

```bash
# 安装 PM2（如果未安装）
npm install -g pm2

# 启动调度器
pm2 start scheduler.py --name job-engine --interpreter python3

# 查看状态
pm2 status

# 查看日志
pm2 logs job-engine

# 重启
pm2 restart job-engine

# 停止
pm2 stop job-engine
```

### 方式二：使用内建定时调度

修改 `scheduler.py` 中的 `USE_SCHEDULE = True`，然后运行：

```bash
conda run -n job_env python scheduler.py
```

### 方式三：使用系统 crontab（Linux/Mac）

```bash
# 每天早 8 点、晚 8 点各执行一次
crontab -e

# 添加以下行（注意使用绝对路径）
0 8,20 * * * cd /Users/gmx/interview/job_engine && /Users/gmx/opt/anaconda3/envs/job_env/bin/python scheduler.py >> logs/cron.log 2>&1
```

### 方式四：使用 Shell 脚本一键运行

```bash
conda run -n job_env bash run_pipeline.sh
```

---

## ⚙️ 配置说明

### 环境变量（`.env`）

| 变量 | 说明 | 示例 |
|------|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | `sk-xxx` |
| `DEEPSEEK_BASE_URL` | DeepSeek API 地址 | `https://api.deepseek.com` |
| `NOTION_API_KEY` | Notion Integration Token | `ntn_xxx` |
| `NOTION_JOBS_DB` | Notion 数据库 ID | `35d48230835880e2aae1c634cd44a380` |
| `TARGET_URL` | 目标搜索 URL（可选） | `https://...` |
| `CHROME_CDP_URL` | Chrome 远程调试地址 | `http://127.0.0.1:9222` |
| `FORCE_UPDATE` | 强制更新 Notion 已有页面 | `1` |

### 黑名单（`blacklist.txt`）

每行一个公司名，支持部分匹配。抓取到这些公司的岗位时会自动跳过。

```
字节跳动
腾讯
阿里巴巴
...
```

### Notion 数据库字段

确保你的 Notion 数据库包含以下字段：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `Title` | title | 岗位名称 |
| `Company` | rich_text | 公司名称 |
| `Platform` | rich_text | 来源平台 |
| `URL` | url | 岗位链接（去重主键） |
| `Location` | rich_text | 工作地点 |
| `Salary Range` | rich_text | 薪资范围 |
| `Match Score` | number | AI 匹配评分 (0-100) |
| `Match Reasons` | rich_text | AI 匹配优势 |
| `Mismatch Reasons` | rich_text | AI 匹配不足 |
| `Notes` | rich_text | AI 总体评价 |
| `Status` | select | 状态：新发现/已查看/已投递/已放弃 |
| `Priority` | select | 优先级：高/中/低 |
| `Discovered Date` | date | 发现日期 |

---

## 🧠 AI 匹配评估

基于 DeepSeek API 的 5 维评分系统，候选人画像为 **AI 产品经理（4 年交易系统开发经验）**：

| 维度 | 权重 | 评估内容 |
|------|------|----------|
| 🎯 匹配度 | 30% | 岗位职责、技能要求与候选人经验的匹配程度 |
| 💰 薪资 | 25% | 薪资范围与候选人期望（30-60K）的匹配程度 |
| 📍 地点 | 15% | 远程 > 一线城市 > 其他 |
| 📈 发展 | 15% | 职业成长空间、赛道前景 |
| 👥 团队 | 15% | 公司阶段、团队文化、平台价值 |

输出格式：
```json
{
  "score": 85,
  "match_reasons": ["大模型应用方向高度匹配", "薪资范围符合预期"],
  "mismatch_reasons": ["地点不在优先城市列表"],
  "summary": "字节跳动 AI PM 岗位，大模型应用方向与候选人背景高度匹配"
}
```

---

## 🛡️ 反爬策略详解

### BOSS直聘（spider_boss.py）

| 策略 | 说明 |
|------|------|
| API 拦截优先 | 监听 `wapi/zpgeek/search/joblist.json` 接口 |
| DOM 兜底 | API 失败时回退到 JS DOM 提取 |
| 平滑滚动 | 模拟真人逐段滚动，触发懒加载 |
| 翻页随机休眠 | 每页间隔 5~10 秒随机等待 |
| 学历前置过滤 | 低于本科的岗位直接丢弃，节省 Token |
| 本地去重缓存 | `processed_jobs.txt` 记录已处理 URL |

### 猎聘（spider_liepin.py）

| 策略 | 说明 |
|------|------|
| 三道风控防线 | 拟人休眠(6.5~15.3s) → 模拟浏览滚动 → 风控哨兵检测 |
| 登录重定向检测 | 检测是否被重定向到登录页，自动等待扫码 |
| 短信验证拦截 | 检测"账号行为异常"等关键词，挂起等待人工处理 |
| URL 清洗去重 | 截断追踪参数，基于纯净 URL 去重 |
| 全局去重 | `history_jobs.json` 持久化已处理记录 |

### 企业官网爬虫（通用策略）

| 策略 | 说明 |
|------|------|
| 降级抓取 | CSS 选择器失效时自动 `inner_text` 全文抓取 |
| 原子化保存 | 每成功一条即刻写入 JSON，防止崩溃数据丢失 |
| 实时心跳日志 | 每个详情页访问前打印 `⏱️ [N/M]` 进度标记 |
| 随机休眠 | 详情页之间 2~3 秒随机等待 |
| 产品经理过滤 | 列表页阶段进行 keyword 匹配，非产品岗自动跳过 |
| 截图留证 | 每个详情页自动截图保存到 `screenshots/` |

---

## 📊 可视化看板

项目附带一个独立的 **Electron 桌面应用**（`ai-pm-job-dashboard/`），提供：

- 🗺️ 岗位地图分布（中国省份 GeoJSON 可视化）
- 📋 岗位列表与筛选
- 📈 AI 评分分布统计
- 🔍 关键词搜索与过滤

详见 [ai-pm-job-dashboard/README.md](ai-pm-job-dashboard/README.md)

---

## 🧪 调试工具

项目包含多个调试脚本，用于排查抓取问题：

| 脚本 | 用途 |
|------|------|
| `debug_probe.py` | DOM 结构探针：探测页面实际 DOM 结构 |
| `debug_liepin_probe.py` | 猎聘 DOM 结构探测 |
| `debug_liepin_login_check.py` | 猎聘登录状态检测 |
| `debug_liepin_drission.py` | 猎聘 DrissionPage 抓取调试 |
| `debug_verify_list.py` | 验证码/安全验证检测 |
| `debug_listener.py` | 网络请求监听调试 |
| `analyze_html.py` | HTML 结构分析工具 |
| `bytedance_api_sniffer.py` | 字节跳动 API 嗅探工具 |

---

## 📝 日志与错误处理

- **控制台日志**：实时输出抓取进度、AI 评分、同步状态
- **`failed_jobs_inbox.md`**：处理失败的岗位自动记录，格式为 Markdown 待办列表
- **`scraper_cron.log`**：定时任务日志
- **错误截图**：抓取失败时自动保存页面截图（`screenshots/` 目录）
- **Notion 重试**：API 网络异常时自动指数退避重试（最多 3 次）

---

## 📋 开发规范（.clinerules）

项目根目录的 `.clinerules` 文件定义了 Cline 开发助手的操作规范：

### 物理通信红线
- **强制文件化**：禁止在终端注入超过 5 行的 Python 代码，复杂逻辑必须创建 `.py` 文件执行
- **零容忍截断**：终端输出异常时立即中止并切换为文件驱动模式

### 抓取与同步范式
- **原始数据优先**：CSS 失效时使用 `inner_text` 全文降级抓取
- **原子化保存**：每成功一条即刻写入 JSON
- **同步前置检查**：运行桥接脚本前检查 JSON 文件大小（<1KB 禁止写入）

### 环境与路径规范
- **环境锁定**：所有指令强制 `conda run -n job_env` 前缀
- **路径绝对化**：脚本内部使用绝对路径或 `pathlib` 引用文件

### 数据清理与幂等性
- **查重逻辑强制化**：以 URL 为唯一主键，Notion 写入前先查重
- **脏数据物理隔离**：列表页阶段进行 keyword 匹配过滤非产品岗

---

## 🔧 技术栈

| 技术 | 用途 |
|------|------|
| [DrissionPage](https://github.com/g1879/DrissionPage) | 浏览器自动化（BOSS直聘 / 猎聘） |
| [Playwright](https://playwright.dev/) | 有界面浏览器抓取（企业官网） |
| [requests](https://requests.readthedocs.io/) | API 直连抓取（腾讯） |
| [DeepSeek API](https://platform.deepseek.com/) | AI 匹配评估 |
| [Notion API](https://developers.notion.com/) | 数据库同步 |
| [Electron](https://www.electronjs.org/) | 桌面可视化看板 |
| [Python 3.10+](https://www.python.org/) | 主开发语言 |

---

## 📄 License

MIT

---

## 🙏 致谢

- 感谢 [DrissionPage](https://github.com/g1879/DrissionPage) 提供的优秀浏览器自动化框架
- 感谢 [DeepSeek](https://deepseek.com/) 提供的高性价比 AI API
- 感谢 [Playwright](https://playwright.dev/) 提供的跨浏览器自动化能力
