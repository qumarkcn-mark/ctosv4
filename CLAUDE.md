# CT-OS V4.0 — 交易教练 项目规则

## 🔴 强制开发流程（所有开发必须遵守）

**本项目使用 gstack 工作流。任何功能开发、Bug修复、重构都必须经过以下流程，不得跳过。**

### 标准开发流程 (Think → Plan → Build → Review → Test → Ship)

```
Step 1: /office-hours        ← 新功能先做需求分析和头脑风暴
Step 2: /plan-ceo-review     ← CEO视角审查：范围、野心、产品价值
Step 3: /plan-eng-review     ← 工程审查：架构、数据流、边界、测试
Step 4: /plan-design-review  ← 设计审查：UI/UX、交互、视觉层次（如涉及UI）
Step 5: [实现代码]            ← 获得审批后才能开始编码
Step 6: /review              ← 代码审查：SQL安全、信任边界、结构问题
Step 7: /qa                  ← QA测试：用真实浏览器测试并修复Bug
Step 8: /ship                ← 发布：跑测试、生成PR、更新CHANGELOG
```

### 简化流程（Bug修复/小改动）

```
Step 1: /investigate          ← 先调查根因，禁止盲修
Step 2: [修复代码]
Step 3: /review               ← 代码审查
Step 4: /qa                   ← 验证修复
```

### 快速审查（一键全审）

```
/autoplan                     ← 自动跑 CEO + Design + Eng Review，一步到位
```

---

## 🔴 绝对禁止

1. **禁止跳过 /review 直接提交代码** — 每次提交必须经过代码审查
2. **禁止不调查就修Bug** — 遇到Bug必须先用 /investigate 找根因
3. **禁止盲目实现功能** — 新功能必须先经过至少 /plan-eng-review
4. **禁止手动推送代码** — 必须通过 /ship 走标准发布流程
5. **禁止使用 mcp__claude-in-chrome__* 工具** — 所有浏览器操作使用 /browse

---

## 产品定位

CT-OS V4.0 是**交易教练**，不是交易机器人。

- **记录** — 语音/手动录入交易，追踪持仓和资金
- **纠正** — 用历史数据暴露行为弱点（止损不执行、赢的卖太早、逆势建仓）
- **提醒** — 不看盘时推送止损预警、缠论信号、回补窗口

用户在券商 App 交易，CT-OS 不执行任何交易。

## 六把武器

1. **仓位透视镜** — 持仓集中度可视化 + 小票堆积预警
2. **止损看门狗** — ATR 止损监控 + 推送 + 历史惩罚数据
3. **持仓信心锚** — 缠论趋势确认 + ATR 追踪止损
4. **级别雷达** — 多级别方向检查（5分/30分/日线/周线）
5. **推演沙盘** — 缠论完全分类生成 + 分享
6. **趋势顺风** — 逆势建仓警告 + 回本陷阱警报

---

## 技术栈

| 层 | 技术 |
|---|---|
| **后端** | Python 3.11+ / FastAPI / Uvicorn |
| **数据库** | SQLite (WAL 模式, 多用户 Ready) |
| **前端** | React 19 + Vite |
| **移动端** | 微信小程序 (原生开发) |
| **行情数据** | 腾讯行情 HTTP API (qt.gtimg.cn) |
| **LLM** | DeepSeek V3 (语音文本提取) |
| **推送** | 微信小程序订阅消息 |
| **部署** | 腾讯云轻量 / Docker Compose |

### 项目结构

```
ct-os-v4/
├── server/                     # FastAPI 后端
│   ├── app.py                  #   应用入口 + lifespan
│   ├── config.py               #   配置管理 (.env)
│   ├── db/
│   │   └── database.py         #   SQLite 连接 + Schema
│   ├── api/                    #   路由层
│   │   ├── auth.py             #     微信 OAuth
│   │   ├── trades.py           #     交易 CRUD
│   │   ├── positions.py        #     持仓查询
│   │   ├── alerts.py           #     提醒管理
│   │   ├── analysis.py         #     行为分析
│   │   └── watchlist.py        #     自选股
│   ├── services/               #   业务逻辑层
│   │   ├── price_service.py    #     HTTP 行情查询 (腾讯/新浪)
│   │   ├── atr_service.py      #     ATR 计算 + 止损价
│   │   ├── position_calc.py    #     持仓计算 (加权平均成本)
│   │   ├── behavior.py         #     行为分析引擎
│   │   ├── push_service.py     #     小程序推送
│   │   ├── csv_importer.py     #     券商交割单导入
│   │   └── text_extractor.py   #     LLM 文本提取
│   ├── workers/                #   后台任务
│   │   ├── price_monitor.py    #     持仓价格监控 (30s)
│   │   ├── daily_report.py     #     日报生成
│   │   ├── ai_structure_snapshot_worker.py
│   │   └── ai_structure_context_worker.py
│   └── tests/                  #   测试
│
├── web/                        # React 19 + Vite 桌面端
│   └── src/
│       ├── pages/              #   Dashboard, AI Structure Workspace, Review
│       ├── components/         #   TradeForm, PositionOverview, AlertCard...
│       └── App.jsx
│
├── miniprogram/                # 微信小程序
│   └── pages/                  #   index, record, positions, analysis, profile
│
├── docker-compose.yml
├── Dockerfile
└── .env.example
```

### 数据模型

5 张核心表（全部带 user_id）：

- `users` — 微信 OAuth 用户
- `trades` — 交易记录（核心）
- `positions` — 实时持仓（trades 聚合）
- `alerts` — 提醒规则
- `behavior_stats` — 行为分析缓存

### 行情数据

不使用 PyTDX 或数据湖。使用 HTTP 行情 API，简单可靠：

```python
# 当前价: https://qt.gtimg.cn/q=sh600519
# 日线: 腾讯/新浪日线 API
```

---

## 运行命令

```bash
# 启动后端
cd /Users/markqu/.gemini/antigravity/scratch/ct-os-v4
source venv/bin/activate
uvicorn server.app:app --host 0.0.0.0 --port 8000 --reload

# 启动前端 (开发模式)
cd /Users/markqu/.gemini/antigravity/scratch/ct-os-v4/web
npm run dev

# 初始化数据库
python -m server.db.database

# 运行测试
pytest server/tests/ -v
```

---

## 开发规范

- **语言**: 中文交流，代码和技术术语用英文
- **注释**: 关键逻辑必须有中文注释
- **测试**: 每个功能必须有对应测试
- **原生 SQL**: 不使用 ORM，直接写 SQL + helper 函数
- **禁止重复**: 行情查询/持仓计算/ATR计算只允许存在唯一实现
- **风险提示**: 涉及交易建议必须附带"仅供参考"声明

---

## 设计系统

所有 UI 和视觉决策前必须先读 `DESIGN.md`。
字体、色彩、间距、布局、圆角、动效全部在 DESIGN.md 中定义。
不得未经用户同意偏离设计系统。
QA 模式下，标记任何不符合 DESIGN.md 的代码。

---

## gstack 技能路由

当用户请求匹配以下场景时，**必须调用对应的 gstack 技能**，不得直接回答：

| 用户意图 | 调用技能 |
|---------|---------| 
| 新想法、"这个值得做吗"、头脑风暴 | `/office-hours` |
| 战略审查、扩大范围、"想大一点" | `/plan-ceo-review` |
| 架构审查、锁定计划 | `/plan-eng-review` |
| 设计系统、品牌、视觉规范 | `/design-consultation` |
| 设计审查（计划阶段） | `/plan-design-review` |
| 全量审查自动化 | `/autoplan` |
| Bug、错误、"为什么坏了" | `/investigate` |
| 测试、QA、找Bug | `/qa` |
| 代码审查、提交前检查 | `/review` |
| 视觉审查、设计优化 | `/design-review` |
| 发布、部署、创建PR | `/ship` |
| 更新文档 | `/document-release` |
| 周回顾 | `/retro` |
| 第二意见、独立代码审查 | `/codex` |
| 安全审计 | `/cso` |
| 安全模式 | `/careful` 或 `/guard` |
| 限制编辑范围 | `/freeze` / `/unfreeze` |
| 升级gstack | `/gstack-upgrade` |
| 保存/恢复进度 | `/checkpoint` |
| 代码质量检查 | `/health` |

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health

## gstack

Use /browse from gstack for all web browsing. Never use mcp__claude-in-chrome__* tools.
