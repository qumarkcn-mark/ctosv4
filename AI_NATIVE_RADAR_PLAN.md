# AI Native Radar Plan

> 目标：把 CT-OS Radar 从“规则枚举系统”升级为“结构事实驱动的 AI 推理闭环”。
> 产品名可叫“禅师推演”，工程名使用 `AI Radar Reasoning Loop`。

## 1. 核心判断

现在 Radar 的主要瓶颈不是 case 不够多，而是方向错了。

股票走势组合无限，继续维护硬编码案例库，会让 `radar_algorithm_v2.py` 和
`level_chain_deduction.py` 越来越像一个无法穷尽的走势字典。真正需要的是：

```text
结构事实 → 相似记忆 → AI 假设推理 → 机器门禁 → 用户教练输出 → 后验复盘 → 记忆更新
```

规则系统负责“不许胡说”，AI 系统负责“像人一样推理”。

CT-OS 仍然是交易教练，不是交易机器人。任何输出都必须带“仅供参考”，不得输出确定性买卖指令。

## 2. 第一阶段范围

第一阶段只做后端推理内核，不做大 UI 改造，不替换现有 Radar。

### In Scope

- 生成 `Structure Transcript`：把 Radar/CChan 输出压缩成 AI 可读的走势剧本。
- 定义 `AI Hypothesis JSON`：AI 必须输出结构化 A/B/C/D 假设。
- 实现 `Gate Score`：机器检查 AI 是否编造价格、缺失失效条件、喊单、遗漏空仓/持仓视角。
- 保存推理运行记录：为后续记忆系统和 replay score 留数据。
- 增加单元测试和 prompt eval 基线。

### Out of Scope

- 不训练模型。
- 不直接让 AI 读取完整 K 线图。
- 不重写 CChan、`chan_adapter`、`radar_algorithm_v2`。
- 不做复杂向量数据库，第一版记忆可先用 SQLite JSON。
- 不把输出包装成买卖建议。

## 3. 总体数据流

```text
K线 / 持仓 / 市场语境
        │
        ▼
┌─────────────────────┐
│ 结构事实层            │
│ CChan + Radar v1/v2  │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Transcript Compiler │
│ 走势剧本压缩          │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Case Memory         │
│ 相似结构检索          │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ AI Hypothesis Engine│
│ 生成 A/B/C/D 推理     │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Gate Verifier       │
│ 机器打分与拒绝展示     │
└─────────┬───────────┘
          │
    PASS / REWRITE / FALLBACK
          │
          ▼
┌─────────────────────┐
│ Coach Output        │
│ 禅师推演              │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Replay Evaluator    │
│ 后验复盘              │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Memory Update       │
│ 记录成功/失败样本      │
└─────────────────────┘
```

## 4. 模块设计

建议新增目录：

```text
server/engines/ai_native/
  __init__.py
  schemas.py
  transcript_compiler.py
  case_memory.py
  hypothesis_reasoner.py
  verifier.py
  reasoning_orchestrator.py
  replay_evaluator.py
server/prompts/
  ai_native_radar_prompt.py
```

### 4.1 `schemas.py`

定义所有 AI native 合同。第一阶段重点是：

```python
class StructureTranscript(BaseModel):
    symbol: str
    mode: Literal["EMPTY", "HOLDING"]
    generated_at: str
    levels: list[LevelTranscript]
    reasoning_boundaries: ReasoningBoundaries
    position_context: PositionContext | None
    market_context: str | None
    allowed_prices: list[AllowedPrice]
    disclaimer: str = "仅供参考，不构成投资建议"


class Hypothesis(BaseModel):
    id: Literal["A", "B", "C", "D"]
    name: str
    current_applicability: Literal["CURRENT", "WAITING", "INVALID", "UNKNOWN"]
    evidence: list[str]
    trigger: str
    invalidation: str
    next_focus: str
    empty_position_view: str
    holding_position_view: str


class AIReasoningOutput(BaseModel):
    diagnosis: str
    current_hypothesis: str
    reasoning_boundary: str
    hypotheses: list[Hypothesis]
    operator_mistake: str
    coach_talk: str
    disclaimer: str
```

### 4.2 `transcript_compiler.py`

输入现有 Radar contract，输出 `StructureTranscript`。

这一层不调用 AI，不查行情，不写数据库，只压缩结构事实。

必须包含：

- 大级别：日线/周线是否支持。
- 中级别：30 分钟/60 分钟是否确认、回踩、拉回中枢。
- 执行级别：5 分钟/15 分钟是否出现买点、失败、震荡。
- 关键边界：上方确认、观察区间、短线失效、大级别失效。
- 允许价格表：所有 AI 可以引用的价格来源。

禁止：

- 生成交易指令。
- 编造没有来源的价格。
- 直接输出“买入”“卖出”。

### 4.3 `case_memory.py`

第一版使用 SQLite，不上向量库。

用结构 fingerprint 检索相似案例：

```text
L0=UPWARD_LEAVING
L1=PULLBACK_VERIFYING
L2=NO_TRIGGER
price_zone=near_previous_high
mode=EMPTY
```

返回给 AI 的不是完整历史，而是压缩后的记忆摘要：

```json
{
  "similar_case_count": 12,
  "common_outcomes": [
    {"path": "B_OSCILLATION", "count": 8},
    {"path": "A_CONFIRM", "count": 3},
    {"path": "C_INVALID", "count": 1}
  ],
  "common_failure_reasons": [
    "5分突破后未站稳",
    "30分跌回中枢后反抽拉不回"
  ]
}
```

### 4.4 `hypothesis_reasoner.py`

负责调用 DeepSeek，输入：

- `StructureTranscript`
- `similar_cases`
- prompt version

输出：

- `AIReasoningOutput`

temperature 建议 `0.2` 到 `0.4`。这是推理任务，不是创作任务。

### 4.5 `verifier.py`

这是第一阶段最重要的安全层。

实时门禁分：

```text
结构忠实度        0-40
分类覆盖度        0-20
触发/失效清晰度   0-15
风险纪律          0-15
空仓/持仓适配     0-10
```

硬拒绝条件：

```text
编造价格                         → FALLBACK
出现“必涨/必跌/稳赚”等确定性语言     → FALLBACK
出现直接交易指令                   → FALLBACK
缺少失效条件                       → REWRITE
只有看涨路径                       → REWRITE
未带“仅供参考”                     → REWRITE
```

结果：

```text
PASS     展示 AI 推演
REWRITE  让 AI 按错误原因重写一次
FALLBACK 降级到规则 Radar 输出
```

### 4.6 `replay_evaluator.py`

第一阶段只定义接口，不做完整自动回放。

后续用 5 根、20 根、60 根 K 线复盘：

- 后续走势落入哪个假设。
- AI 是否提前下结论。
- 失效条件是否及时触发。
- 是否错把震荡说成趋势。
- 用户按“观察纪律”执行是否能避免冲动。

### 4.7 `reasoning_orchestrator.py`

这是 API 和各个模块之间的编排层。不要把流程直接写进 `server/api/agent.py`。

职责：

```text
1. 调用现有 Radar contract。
2. 调用 transcript compiler。
3. 调用 case memory 检索相似结构。
4. 调用 hypothesis reasoner。
5. 调用 verifier。
6. 处理一次 REWRITE。
7. 处理 FALLBACK。
8. 写入 ai_reasoning_runs。
9. 返回 API 可展示结果。
```

`server/api/agent.py` 只做 request/response，保持薄路由。

推荐流程：

```text
build_ai_native_reasoning(symbol, user_id, mode)
    ├── radar_api.get_radar()
    ├── compile_structure_transcript()
    ├── find_similar_cases()
    ├── infer_ai_hypotheses()
    ├── verify_ai_reasoning()
    ├── if REWRITE: infer_ai_hypotheses(rewrite_context)
    ├── if FALLBACK: build_fallback_from_radar()
    ├── save_reasoning_run()
    └── return AI native response
```

失败策略：

```text
Radar contract 失败      → 返回 500 或沿用现有 Radar 错误语义
LLM 失败                → FALLBACK，不 500
Verifier FALLBACK       → FALLBACK，不展示 AI 文本
DB 写入失败             → 记录日志，但不阻断用户输出
Case memory 失败        → 记忆上下文为空，继续推理
```

## 5. 数据表

新增表建议：

```sql
CREATE TABLE IF NOT EXISTS ai_reasoning_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    prompt_version TEXT NOT NULL,
    model_name TEXT NOT NULL,
    structure_fingerprint TEXT NOT NULL,
    transcript_json TEXT NOT NULL,
    memory_context_json TEXT,
    ai_output_json TEXT,
    gate_result_json TEXT NOT NULL,
    gate_status TEXT NOT NULL,
    replay_status TEXT NOT NULL DEFAULT 'PENDING',
    replay_score REAL,
    outcome_json TEXT,
    disclaimer TEXT NOT NULL DEFAULT '仅供参考，不构成投资建议'
);
```

索引：

```sql
CREATE INDEX IF NOT EXISTS idx_ai_reasoning_runs_symbol_created
ON ai_reasoning_runs(symbol, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_reasoning_runs_fingerprint
ON ai_reasoning_runs(structure_fingerprint);
```

实现位置：

- `server/db/database.py` 的 `SCHEMA` 增加建表 SQL。
- `run_migrations()` 增加同一段 `CREATE TABLE IF NOT EXISTS`，保证存量库自动补表。
- `tests/test_database_migrations.py` 增加迁移测试，断言 `ai_reasoning_runs` 表和索引存在。

注意：不要只改 `SCHEMA`。本项目已有存量 SQLite，单改初始化脚本不会升级现有库。

## 6. API 形态

第一阶段新增接口，不破坏现有 `/api/agent/radar_deduce`：

```text
POST /api/agent/ai-native-radar
```

默认用 feature flag 保护：

```text
AI_NATIVE_RADAR_ENABLED=false
```

未开启时返回：

```json
{
  "status": "disabled",
  "message": "AI Native Radar is disabled"
}
```

配置建议放在 `server/config.py`，避免开发环境之外意外暴露新接口。

输入：

```json
{
  "symbol": "sh600519",
  "mode": "EMPTY",
  "user_id": 1
}
```

输出：

```json
{
  "status": "success",
  "data": {
    "gate_status": "PASS",
    "gate_score": 92,
    "diagnosis": "日线离开后，30分回踩验证中",
    "current_hypothesis": "B",
    "reasoning_boundary": "11.90-12.80 内不下结论",
    "hypotheses": [],
    "coach_talk": "当前不是预测涨跌，而是等待分类完成...",
    "disclaimer": "仅供参考，不构成投资建议"
  }
}
```

如果失败：

```json
{
  "status": "success",
  "data": {
    "gate_status": "FALLBACK",
    "fallback_reason": "AI 输出引用了不存在的价格 13.27",
    "fallback_data": {}
  }
}
```

## 7. Prompt 规则

`server/prompts/ai_native_radar_prompt.py` 必须强调：

1. 你是交易教练，不是交易机器人。
2. 你只能使用 `allowed_prices` 里的价格。
3. 你必须输出 A/B/C/D 四类。
4. A 是向上确认，不是买入命令。
5. B 是区间观察，不允许强行给方向。
6. C 是失效路径，必须清楚说原假设作废。
7. D 是数据不足或停止推演。
8. 必须区分空仓和持仓。
9. 不得使用“必涨、必跌、稳赚、抄底、梭哈”等词。
10. 必须带“仅供参考，不构成投资建议”。

## 8. 测试计划

### Unit Tests

新增：

```text
tests/test_ai_native_transcript.py
tests/test_ai_native_verifier.py
tests/test_ai_native_case_memory.py
tests/test_ai_native_agent_api.py
```

覆盖：

```text
Transcript Compiler
  ├── 空仓模式生成 allowed_prices
  ├── 持仓模式包含成本/盈亏上下文
  ├── 缺失中枢时输出 UNKNOWN，不崩溃
  └── stale freshness 时标记不可推理

Verifier
  ├── 引用 allowed_prices 之外价格 → FALLBACK
  ├── 缺少 C 失效路径 → REWRITE
  ├── 只有看涨路径 → REWRITE
  ├── 出现交易指令 → FALLBACK
  ├── 缺少免责声明 → REWRITE
  └── 完整 A/B/C/D → PASS

Case Memory
  ├── 相同 fingerprint 能检索历史案例
  ├── 无历史案例返回空摘要
  └── replay score 能写回

API
  ├── feature flag 关闭时返回 disabled
  ├── LLM PASS 时返回 AI 输出
  ├── LLM REWRITE 后成功返回
  ├── LLM FALLBACK 时返回规则雷达
  └── LLM 异常时不 500，降级

Database
  ├── init_db 新库创建 ai_reasoning_runs
  ├── run_migrations 存量库补建 ai_reasoning_runs
  └── symbol/fingerprint 索引存在
```

### Eval Tests

需要建立 10 个固定样本：

- 3 个向上确认
- 3 个区间震荡
- 3 个失效路径
- 1 个数据不足

每个样本检查：

- 是否覆盖 A/B/C/D。
- 是否引用正确价格。
- 是否没有喊单。
- 是否说清楚“下一步只盯什么”。

## 9. 迁移策略

第一阶段采用 strangler fig，不做 big bang。

```text
现有 Radar
   │
   ├── 旧 /radar_deduce 继续存在
   │
   └── 新 /ai-native-radar 并行试运行
```

## 9.1 隔离策略

AI Native Radar 第一阶段必须作为影子系统运行，不能影响老版本 Radar。

隔离目标：

```text
1. 老 Radar 的 API、数据结构、UI 展示不变。
2. AI Native Radar 不写入老表的业务字段。
3. AI Native Radar 不参与提醒、仓位、playbook、rotation 的正式决策。
4. AI Native Radar 的失败不能导致老 Radar 失败。
5. AI Native Radar 的 prompt、schema、测试、日志全部独立版本化。
```

### API 隔离

保留旧接口：

```text
POST /api/agent/radar_deduce
GET  /api/radar/{symbol}
```

新增影子接口：

```text
POST /api/agent/ai-native-radar
```

旧接口不得 import `server.engines.ai_native.*`。

新接口可以调用旧 Radar contract 作为输入，但只能读，不能改变旧 Radar 输出。

### 数据隔离

AI Native Radar 只写新表：

```text
ai_reasoning_runs
```

第一阶段禁止写入：

```text
radar_deductions
positions
alerts
daily_playbooks
scan_results
coach_events
```

如果后续要把 AI 推理接入提醒或 playbook，必须单独开计划和审查。

### 文件存储隔离

AI Native Radar 的本地文件、样本、评估产物必须独立存放，不能混入老 Radar 的 fixtures、docs、scratch 输出。

建议目录：

```text
data/ai_native_radar/
  runs/                 # 单次推理输入/输出快照，可按日期分区
  eval_cases/           # 固定 prompt eval 样本
  replay_cases/         # 后验复盘样本
  human_reviews/        # 人工审核记录
  exports/              # 临时导出，不作为线上依赖
```

路径配置：

```text
AI_NATIVE_RADAR_DATA_DIR=data/ai_native_radar
```

配置建议放在 `server/config.py`，默认相对项目根目录。生产部署时可改成持久化挂载目录。

文件命名建议：

```text
runs/YYYY-MM-DD/{symbol}_{run_id}.json
eval_cases/{case_id}.json
replay_cases/{symbol}_{as_of}_{horizon}.json
human_reviews/{run_id}.md
```

文件内容规则：

```text
1. 文件只存 AI Native Radar 自己的 transcript、memory_context、ai_output、gate_result、review。
2. 不覆盖、不移动、不复用老 Radar fixture。
3. 不把文件当线上唯一数据源，线上主记录仍然是 ai_reasoning_runs。
4. 文件写入失败不影响 API 输出，只记录日志。
5. 文件路径必须通过 config 读取，不在代码里硬编码。
```

第一阶段禁止写入：

```text
data/radar/
docs/
scratch/
tests/fixtures/
server/engines/decision/
web/
```

例外：`tests/test_ai_native_*.py` 可以使用内联 fixture，或读取 `data/ai_native_radar/eval_cases/` 中明确标记为 AI Native 的样本。

隔离测试增加：

```text
1. 默认数据目录为 data/ai_native_radar。
2. run snapshot 写入只发生在 AI Native 目录下。
3. 文件写入失败时 API 仍返回 PASS/FALLBACK。
4. 测试中禁止向 docs、scratch、tests/fixtures 写入 AI Native 运行产物。
```

### 代码隔离

新增代码只放在：

```text
server/engines/ai_native/
server/prompts/ai_native_radar_prompt.py
tests/test_ai_native_*.py
```

允许最小改动：

```text
server/api/agent.py       # 只增加新 route
server/services/llm_service.py  # 只增加 infer_ai_native_radar()
server/db/database.py     # 只增加新表和迁移
server/config.py          # 只增加 feature flag
```

禁止第一阶段修改：

```text
server/api/radar.py
server/engines/decision/radar_algorithm_v2.py
server/engines/decision/level_chain_deduction.py
web/src/features/radar/*
```

这些文件是老 Radar 主路径。除非测试证明必须加只读 adapter，否则不碰。

### 运行隔离

默认关闭：

```text
AI_NATIVE_RADAR_ENABLED=false
```

开启后仍然只影响新接口。老页面不自动调用新接口。

灰度顺序：

```text
1. 本地手动调用新接口。
2. 开发环境隐藏入口。
3. 只展示“禅师推演 Beta”，不替换原雷达。
4. 收集 20-50 条 ai_reasoning_runs。
5. 人工确认后再考虑 UI 合并。
```

### 回滚策略

回滚必须简单：

```text
AI_NATIVE_RADAR_ENABLED=false
```

即可停止所有 AI Native Radar 运行。

因为第一阶段不改老 Radar 表、不改老 Radar API、不改老 Radar UI，所以不需要数据回滚。

### 隔离测试

必须增加测试：

```text
tests/test_ai_native_isolation.py
```

覆盖：

```text
1. feature flag 关闭时，新接口返回 disabled。
2. 新接口 LLM 异常时不影响 /radar_deduce。
3. 新接口只写 ai_reasoning_runs，不写 radar_deductions。
4. 导入 server.api.radar 不依赖 server.engines.ai_native。
5. 老 tests/test_agent_radar_deduce.py 全部继续通过。
```

上线顺序：

1. 后端模块和测试。
2. API 灰度，只在开发环境或 feature flag 下展示。
3. 记录 `ai_reasoning_runs`。
4. 人工看 20-50 个输出样本。
5. 再接 UI“禅师推演”入口。

## 10. 成功标准

第一阶段完成后，系统应该做到：

- AI 不再只是叙事层，而能输出可验证的结构假设。
- 每条 AI 推理都有边界、失效条件、下一观察事件。
- AI 输出不能绕过机器门禁。
- 每次推理都能沉淀成未来记忆。
- 旧 Radar 不受影响。

这不是让 AI 神化。

这是让 CT-OS 开始形成一个会复盘、会收敛、越来越像禅师的推理闭环。

## 11. Implementation Readiness Checklist

开工前必须确认以下准备项。未完成则不进入编码。

### 11.1 第一期边界冻结

第一期只做影子系统：

```text
[ ] 不替换老 Radar UI。
[ ] 不改老 Radar 主路径。
[ ] 不接入提醒、playbook、rotation、scanner 的正式决策。
[ ] 只新增 /api/agent/ai-native-radar。
[ ] 默认 AI_NATIVE_RADAR_ENABLED=false。
[ ] 所有 AI Native 运行记录只写 ai_reasoning_runs。
[ ] 所有本地文件只写 AI_NATIVE_RADAR_DATA_DIR。
```

### 11.2 最小 Eval 样本

第一期必须准备 10 个固定样本：

```text
[ ] 3 个向上确认样本。
[ ] 3 个区间震荡样本。
[ ] 3 个失效路径样本。
[ ] 1 个数据不足样本。
```

每个样本包含：

```text
[ ] radar_contract。
[ ] allowed_prices。
[ ] 期望 A/B/C/D。
[ ] 期望 current_hypothesis。
[ ] 禁止出现词。
[ ] 期望 gate_status。
[ ] 人工说明：为什么这个样本代表该走势。
```

建议文件：

```text
data/ai_native_radar/eval_cases/{case_id}.json
```

### 11.3 Structure Fingerprint v1

第一版 fingerprint 不做向量检索，使用稳定结构字段拼接。

建议字段：

```text
mode
L0.path
L0.phase
L1.raw_state
L1.position_state
L2.raw_state
L2.position_state
current_scenario_id
price_zone
freshness_bucket
holding_bucket
pnl_bucket
```

示例：

```text
EMPTY|UPWARD_MAJOR_WAVE|PULLBACK_VERIFYING|UPWARD_LEAVING|ABOVE_CENTER|NO_TRIGGER|NEAR_PREVIOUS_HIGH|FRESH|NO_HOLDING
```

准备项：

```text
[ ] 定义字段缺失时的 UNKNOWN 规则。
[ ] 定义 price_zone 枚举。
[ ] 定义 holding_bucket / pnl_bucket 枚举。
[ ] 定义 fingerprint 版本号，例如 fingerprint.v1。
```

### 11.4 Verifier 红线

第一版红线词：

```text
必涨
必跌
稳赚
抄底
梭哈
满仓
清仓
买入
卖出
建仓
加仓
减仓
止盈
止损执行
```

注意：CT-OS 可以说“失效条件”“防线”“风险参考”，但不能输出直接交易命令。

价格校验规则：

```text
[ ] AI 输出中的所有数字价格必须能匹配 allowed_prices。
[ ] 允许格式差异：12.30 和 12.3 视为相同。
[ ] 默认价格容差：0.01 元。
[ ] 百分比、根数、日期不走 allowed_prices 校验。
[ ] 不能识别的价格数字进入人工可读 violation list。
```

### 11.5 Fallback 合同

AI 未通过门禁时，永远不展示失败 AI 文本。

Fallback 输出：

```json
{
  "gate_status": "FALLBACK",
  "fallback_reason": "AI 输出引用了不存在的价格",
  "fallback_data": {
    "source": "radar_contract",
    "diagnosis": "沿用规则雷达推演",
    "plans": []
  }
}
```

准备项：

```text
[ ] FALLBACK 使用现有 Radar contract 生成。
[ ] FALLBACK 不调用 LLM。
[ ] FALLBACK 必须包含 disclaimer。
[ ] FALLBACK 写入 ai_reasoning_runs。
```

### 11.6 配置项

第一期新增配置：

```text
AI_NATIVE_RADAR_ENABLED=false
AI_NATIVE_RADAR_DATA_DIR=data/ai_native_radar
AI_NATIVE_RADAR_WRITE_SNAPSHOTS=false
AI_NATIVE_RADAR_MAX_REWRITE=1
AI_NATIVE_RADAR_MODEL=deepseek-chat
AI_NATIVE_RADAR_PROMPT_VERSION=ai_native_radar.v1
AI_NATIVE_RADAR_FINGERPRINT_VERSION=fingerprint.v1
```

准备项：

```text
[ ] `server/config.py` 读取这些配置。
[ ] 测试覆盖默认值。
[ ] 测试覆盖 feature flag 关闭。
```

### 11.7 验收测试命令

第一期实现完成后至少运行：

```bash
pytest tests/test_ai_native_transcript.py \
       tests/test_ai_native_verifier.py \
       tests/test_ai_native_case_memory.py \
       tests/test_ai_native_agent_api.py \
       tests/test_ai_native_isolation.py \
       tests/test_database_migrations.py \
       tests/test_agent_radar_deduce.py -v
```

如果改到 `server/services/llm_service.py`，还要运行：

```bash
pytest tests/test_agent_radar_deduce.py tests/test_multiverse_radar_contract.py -v
```

如果改到数据库初始化，还要运行：

```bash
python -m server.db.database
```

### 11.8 人工审核标准

第一阶段上线前，人工抽查 20-50 条 `ai_reasoning_runs`。

每条按以下标准打分：

```text
[ ] 是否一眼看出走势阶段。
[ ] 是否有清楚推理边界。
[ ] 是否覆盖向上 / 震荡 / 失效 / 数据不足。
[ ] 是否没有喊单。
[ ] 是否没有编造价格。
[ ] 是否区分空仓和持仓。
[ ] 是否比老 Radar 更像教练。
[ ] 是否带“仅供参考，不构成投资建议”。
```

通过标准：

```text
Gate PASS 样本中，人工审核 90% 以上可展示。
FALLBACK 样本中，100% 不展示失败 AI 文本。
旧 Radar 回归测试 100% 通过。
```
