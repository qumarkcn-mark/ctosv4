# V5 统一推演系统工程化方案

## 概述

替换现有三步推演链路（Stage1 Full Reasoning → Stage2 Summary → Chat），改为一步到位的统一推演。

**核心变化：**
- Prompt：从多段复杂指令 → 四行极简
- 数据：从纯结构快照 → 结构 + 压力支撑 + 持仓
- 流程：从三次 LLM 调用 → 一次
- 输出：完整推演存储，前端只显示摘要，问答引用全文

---

## 一、后端 Service 层

### 1.1 新增文件：`server/engines/ai_native/unified_reasoning_service.py`

职责：组装输入数据 + 调用 LLM + 存储结果

```python
"""V5 统一推演 Service — 一步到位的结构推演 + 操作建议"""

# 主函数签名
async def run_unified_reasoning(
    *,
    user_id: int,
    symbol: str,
    force: bool = False,
) -> dict:
    """
    执行统一推演。流程：
    1. 拉取四级别 snapshot
    2. 提取关键结构信息
    3. 计算压力支撑簇（带 status）
    4. 获取用户持仓
    5. 组装输入 JSON
    6. 调用 DeepSeek V4 Pro
    7. 存储完整推演结果
    8. 提取前端摘要
    9. 返回结果
    """
    pass


def get_latest_unified_reasoning(
    *,
    user_id: int,
    symbol: str,
) -> dict | None:
    """获取最新的统一推演结果"""
    pass


def extract_frontend_summary(full_reasoning_text: str) -> dict:
    """
    从完整推演文本中提取前端摘要。
    返回格式：
    {
        "one_liner": "日线三买构建中，盯4.37承接",
        "current_status": "回踩确认",  
        "action": "持有",  # 持有/加仓/减仓/观望/止损
        "key_level": 4.37,
        "key_level_meaning": "30分中枢上沿，多空分水岭",
        "scenarios": [
            {"name": "强", "probability": "40%", "brief": "守住4.37，冲4.50"},
            {"name": "弱", "probability": "40%", "brief": "破4.37，回4.13震荡"},
            {"name": "废", "probability": "20%", "brief": "破4.13，止损"},
        ]
    }
    注意：这个提取用 flash 模型做，不需要 pro。
    """
    pass
```

### 1.2 数据组装逻辑（从测试脚本提炼）

```python
def build_unified_input(
    *,
    symbol: str,
    snapshots: dict,  # {level: snapshot_data}
    position: dict | None,
) -> dict:
    """
    组装统一推演输入。
    
    结构来自：get_latest_snapshot() x 4 levels
    压力支撑来自：compute_pressure_support(snapshots)
    持仓来自：positions 表
    """
    return {
        "symbol": symbol,
        "current_price": ...,
        "data_as_of": ...,
        "structure": {
            "周线": extract_structure_for_llm(snapshots["week"]),
            "日线": extract_structure_for_llm(snapshots["day"]),
            "30分钟": extract_structure_for_llm(snapshots["30"]),
            "5分钟": extract_structure_for_llm(snapshots["5"]),
        },
        "pressure_support": compute_pressure_support(snapshots),
        "my_position": get_user_position(symbol),
    }
```

### 1.3 结构提取（每级别）

从完整 snapshot（96-172KB）中只提取 LLM 需要的信息：
- `current_price`, `last_bi_direction`, `state_hint`
- `active_zhongshu`: zg, zd, gg, dd, bi_count, begin_date, end_date
- `price_vs_center`: position, distance_to_zg_pct, distance_to_zd_pct
- `recent_bis`: 最后 6 笔（direction, start_price, end_price, high, low, bar_count, is_sure）
- `recent_zhongshus`: 最近 2 个中枢
- `recent_fxs`: 最近 4 个分型

### 1.4 压力支撑计算（带 status）

```python
def compute_pressure_support(snapshots: dict) -> list:
    """
    从多级别笔端点聚类，输出格式：
    {
        "zone": [4.39, 4.45],
        "type": "pressure" | "support",
        "status": "holding" | "testing" | "just_broken_below" | "just_broken_above" | "confirmed",
        "source_levels": ["5min", "day"],
        "hit_count": 3,
        "distance_pct": 0.9,
    }
    
    status 判断规则：
    - holding: 远离当前价，还没被碰到
    - testing: 当前价在区间内或距离 <1%
    - just_broken_below: 当前价在区间下方，且最近曾在区间上方（支撑转压力）
    - just_broken_above: 当前价在区间上方，且最近曾在区间下方（压力转支撑）
    - confirmed: 多次测试有效
    
    聚类半径：1.5%
    输出数量：最多 6 个（按距离当前价排序）
    """
    pass
```

### 1.5 System Prompt（固定，不需要配置）

```python
UNIFIED_REASONING_SYSTEM_PROMPT = """你是缠中说禅，用户的盯盘搭档。

输入包含：多级别结构快照、历史压力支撑位、用户持仓。

看完数据，说清楚当下是什么、接下来怎么走、用户该怎么做。

仅供参考，不构成投资建议。"""
```

### 1.6 LLM 调用参数

```python
LLM_CONFIG = {
    "model": config.AI_NATIVE_MODEL,  # deepseek-v4-pro
    "max_tokens": 4096,
    "temperature": 0.7,
    "timeout": 150,
}
```

---

## 二、数据存储

### 2.1 复用现有表 `ai_structure_reasoning_runs`

不新建表，复用现有 `ai_structure_reasoning_runs`：

| 字段 | 用途 |
|---|---|
| `full_reasoning_text` | 存完整推演全文（~2000字） |
| `summary_json` | 存前端摘要 JSON |
| `prompt_version` | 改为 `"unified_reasoning.v1"` |
| `think_model` | 填 `"deepseek-v4-pro"` |
| `summary_model` | 填 `"deepseek-v4-flash"`（用于摘要提取） |

### 2.2 前端摘要存储格式

```json
{
  "version": "unified_reasoning.v1",
  "one_liner": "日线三买构建中，盯4.37承接",
  "current_status": "回踩确认",
  "action": "持有",
  "action_detail": "底仓不动，等5分钟底分型确认后加仓",
  "key_level": 4.37,
  "key_level_meaning": "30分中枢上沿，多空分水岭",
  "stop_loss": 4.13,
  "scenarios": [
    {"name": "强", "probability": "40%", "brief": "守住4.37，冲4.50"},
    {"name": "弱", "probability": "40%", "brief": "破4.37，回4.13震荡"},
    {"name": "废", "probability": "20%", "brief": "破4.13，止损"}
  ],
  "generated_at": "2026-05-16T15:30:00"
}
```

### 2.3 摘要提取 Prompt（用 flash 模型，成本低）

```python
SUMMARY_EXTRACT_PROMPT = """从以下推演全文中提取结构化摘要，返回 JSON：

{
  "one_liner": "一句话概括当前状态和核心动作（15字以内）",
  "current_status": "当前走势阶段（5字以内）",
  "action": "持有|加仓|减仓|观望|止损",
  "action_detail": "具体怎么做（30字以内）",
  "key_level": 最关键的价格数字,
  "key_level_meaning": "这个价格为什么重要（15字以内）",
  "stop_loss": 止损价格数字或null,
  "scenarios": [
    {"name": "强|弱|废", "probability": "百分比", "brief": "10字以内描述"}
  ]
}

只返回 JSON，不要其他内容。"""
```

---

## 三、API 接口

### 3.1 新增路由（在 `server/api/ai_structure.py` 中添加）

```python
# ─── 统一推演 ───

@router.post("/ai-structure/unified-reasoning/{symbol}")
async def trigger_unified_reasoning(
    symbol: str,
    user_id: int = Depends(get_current_user_id),
    force: bool = Query(False),
):
    """触发统一推演（手动或定时）"""
    result = await run_unified_reasoning(
        user_id=user_id,
        symbol=normalize_symbol(symbol),
        force=force,
    )
    return {"ok": True, "data": result}


@router.get("/ai-structure/unified-reasoning/{symbol}")
async def get_unified_reasoning(
    symbol: str,
    user_id: int = Depends(get_current_user_id),
):
    """获取最新推演结果（摘要 + 全文）"""
    result = get_latest_unified_reasoning(
        user_id=user_id,
        symbol=normalize_symbol(symbol),
    )
    if not result:
        raise HTTPException(404, "暂无推演结果")
    return {"ok": True, "data": result}


@router.get("/ai-structure/unified-reasoning/{symbol}/summary")
async def get_unified_reasoning_summary(
    symbol: str,
    user_id: int = Depends(get_current_user_id),
):
    """只获取前端摘要（轻量，用于列表展示）"""
    result = get_latest_unified_reasoning(
        user_id=user_id,
        symbol=normalize_symbol(symbol),
    )
    if not result:
        raise HTTPException(404, "暂无推演结果")
    return {"ok": True, "data": result.get("summary")}
```

### 3.2 问答接口改造

现有 `structure_chat_service.answer_structure_question` 改为：
- 从 `ai_structure_reasoning_runs` 读取最新 `full_reasoning_text`
- 把推演全文作为 context 传给 flash 模型
- flash 模型针对用户问题回答（不再需要 reasoning_json 和 scenario_branches）

```python
CHAT_SYSTEM_PROMPT = """你是用户的盯盘搭档。
下面是之前对这只票的完整推演，用户现在有问题，请基于推演内容直接回答。
简洁、直接、不废话。

仅供参考，不构成投资建议。"""

# 用户消息格式：
# "推演内容：\n{full_reasoning_text}\n\n用户问题：{question}"
```

---

## 四、前端展示

### 4.1 摘要卡片（替换现有 CoachPanel 顶部状态）

在 `AIStructureCoachPanel.jsx` 中，现有顶部状态区改为显示 summary：

```jsx
// 摘要卡片组件
function ReasoningSummaryCard({ summary, onExpand }) {
  if (!summary) return null
  
  return (
    <div className="reasoning-summary-card">
      {/* 第一行：一句话 + 动作标签 */}
      <div className="summary-header">
        <span className="one-liner">{summary.one_liner}</span>
        <span className={`action-badge action-${summary.action}`}>
          {summary.action}
        </span>
      </div>
      
      {/* 第二行：关键位 + 止损 */}
      <div className="summary-levels">
        <span>盯 {summary.key_level}（{summary.key_level_meaning}）</span>
        {summary.stop_loss && <span className="stop-loss">止损 {summary.stop_loss}</span>}
      </div>
      
      {/* 第三行：场景概率条 */}
      <div className="summary-scenarios">
        {summary.scenarios?.map((s, i) => (
          <span key={i} className={`scenario scenario-${s.name}`}>
            {s.name} {s.probability}
          </span>
        ))}
      </div>
      
      {/* 展开全文按钮 */}
      <button className="expand-btn" onClick={onExpand}>
        查看完整推演
      </button>
    </div>
  )
}
```

### 4.2 持仓列表视图（Dashboard 改造）

在 Dashboard 或 PositionList 中，每只持仓票旁边显示一行摘要：

```
轻纺城  4.38  +3.79%  │ 日线三买构建中 │ 盯4.37 │ 持有
澜起科技 247.98 +66.5% │ 5分三卖风险  │ 盯253.49 │ 减仓1/3
浦发银行  9.07  空仓   │ 弱势等买点   │ 盯8.92  │ 观望
```

前端调用 `/ai-structure/unified-reasoning/{symbol}/summary` 批量获取。

### 4.3 全文展示（点击展开）

点击"查看完整推演"后，展示 `full_reasoning_text`，用 markdown 渲染。已有的 CoachPanel 聊天区域可以直接复用。

### 4.4 问答交互（保持现有 UI）

现有的 QUICK_QUESTIONS 和输入框保留：
```javascript
const QUICK_QUESTIONS = [
  '现在能买吗？',
  '该不该减仓？',
  '跌到哪里止损？',
  '什么时候加仓？',
  '帮我总结一下',
]
```

问答请求发送到改造后的 chat 接口，后端用 flash 模型 + 推演全文作 context 回答。

---

## 五、定时任务

### 5.1 触发时机

| 触发条件 | 说明 |
|---|---|
| 每日收盘后 15:05 | 自动对所有持仓 + 自选股跑一次统一推演 |
| 用户手动点击"刷新推演" | 前端按钮触发 POST 接口 |
| Snapshot 更新后 | 当 CZSC snapshot 刷新时，标记旧推演为 stale |

### 5.2 与现有 worker 的关系

现有 `ai_structure_context_worker.py` 和 `ai_structure_snapshot_worker.py` 负责计算 snapshot。
统一推演 service 是 snapshot 的下游消费者——snapshot 刷新后触发推演。

建议在现有 worker 中加一个 hook：
```python
# 在 snapshot worker 完成后
if all_levels_fresh(symbol):
    await run_unified_reasoning(user_id=user_id, symbol=symbol)
```

---

## 六、与现有代码的关系（迁移策略）

### 6.1 保留不动的

| 文件 | 原因 |
|---|---|
| `czsc_snapshot_service.py` | 上游，统一推演依赖它 |
| `structure_evidence_service.py` | K线图 focus 标注，独立功能 |
| `structure_reminder_service.py` | 提醒功能，独立 |
| `scenario_branch_service.py` | 暂时保留，后续可废弃 |

### 6.2 替换/改造的

| 文件 | 改造方式 |
|---|---|
| `structure_context_service.py` | 新推演不经过这里，但暂时保留向后兼容 |
| `structure_chat_service.py` | 改为从 unified reasoning 全文做 context 回答 |
| `ai_structure_reasoning_prompt.py` | 不再使用，统一推演的 prompt 写在新 service 里 |

### 6.3 新增的

| 文件 | 职责 |
|---|---|
| `server/engines/ai_native/unified_reasoning_service.py` | 核心 service |
| 无新前端文件 | 改造现有 `AIStructureCoachPanel.jsx` |

---

## 七、实施步骤（给 Codex 的执行顺序）

```
Step 1: 创建 unified_reasoning_service.py
        - 从测试脚本 test_unified_real_data.py 提炼
        - 实现 build_unified_input / compute_pressure_support / run_unified_reasoning
        - 实现 extract_frontend_summary（用 flash 模型）
        - 实现 get_latest_unified_reasoning

Step 2: 添加 API 路由
        - 在 ai_structure.py 中添加三个新接口
        - POST trigger / GET full / GET summary

Step 3: 改造 structure_chat_service.py
        - answer_structure_question 改为从 unified reasoning 全文做 context
        - 用 flash 模型回答问题

Step 4: 前端摘要卡片
        - 在 AIStructureCoachPanel.jsx 顶部添加 ReasoningSummaryCard
        - 调用 summary API 展示
        - "查看完整推演"展开全文

Step 5: 持仓列表摘要
        - 在 PositionList 或 Dashboard 中批量展示每只票的 one_liner
        - 调用 summary API

Step 6: 定时触发
        - 在现有 snapshot worker 完成后触发统一推演
        - 或单独 cron job 收盘后跑
```

---

## 八、验证标准

推演完成后，检查以下几点：

- [ ] 输出是否识别了正确的主级别结构事件
- [ ] 是否结合了持仓数据给操作建议
- [ ] 是否使用了压力支撑数据
- [ ] 是否有明确的当下判断（不全是条件句）
- [ ] 是否有止损/失效条件
- [ ] 前端摘要是否 ≤ 2 行能看完
- [ ] 问答是否能基于全文准确回答

---

## 九、配置项

在 `.env` 中新增或复用：

```env
# 统一推演（复用现有配置）
AI_NATIVE_MODEL=deepseek-v4-pro          # 推演用 pro
LLM_MODEL=deepseek-v4-flash              # 摘要提取和问答用 flash
AI_NATIVE_LLM_TIMEOUT=150                # 推演超时
AI_NATIVE_MAX_TOKENS=4096                # 推演最大输出
```

---

## 十、参考文件

测试脚本（已验证通过，可直接复用数据提取逻辑）：
- `server/scripts/test_unified_real_data.py` — 单只真实数据测试
- `server/scripts/test_unified_batch.py` — 批量测试（含 status 字段的压力支撑）
- `server/scripts/test_unified_prompt.py` — 模拟数据测试
