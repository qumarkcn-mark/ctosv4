# V5 盯盘面板工程化方案

## 概述

桌面 Web 端盯盘面板，卡片式布局，分持仓/自选/备选三组。每张卡片展示推演摘要 + 关键位 + 动作建议，盘中实时价格刷新，价格触发关键位时卡片状态自动切换。

---

## 一、数据来源

| 数据 | 来源 | 刷新频率 |
|---|---|---|
| 股票列表 | `watchlist_groups` + `watchlist_items` + `positions` | 页面加载时 |
| 推演摘要 | `ai_structure_reasoning_runs.summary_json` | 收盘后更新一次 |
| 实时价格 | `price_service.get_batch_prices()` → 腾讯行情 | 盘中每 10 秒 |
| 持仓数据 | `positions` 表 | 页面加载时 |
| 卡片状态 | 前端根据实时价格 vs 推演条件计算 | 每次价格刷新时 |

---

## 二、后端接口

### 2.1 盯盘面板数据聚合接口（新增）

一个接口返回面板所需的所有数据，避免前端多次请求：

```python
# server/api/ai_structure.py 新增

@router.get("/ai-structure/watchboard")
async def get_watchboard(
    user_id: int = Depends(get_current_user_id),
):
    """
    返回盯盘面板所需的完整数据：
    - 持仓列表 + 推演摘要
    - 自选列表 + 推演摘要
    - 备选列表 + 推演摘要
    - 每只票的实时价格
    """
    return {"ok": True, "data": {
        "groups": [
            {
                "name": "持仓",
                "type": "position",
                "items": [...]
            },
            {
                "name": "自选",
                "type": "watchlist",
                "items": [...]
            },
            {
                "name": "备选",
                "type": "candidate",
                "items": [...]
            }
        ],
        "updated_at": "2026-05-16T15:05:00"
    }}
```

### 2.2 单只卡片数据结构

```json
{
  "symbol": "sh.600790",
  "name": "轻纺城",
  "price": 4.38,
  "change_pct": -0.7,
  "position": {
    "shares": 20000,
    "cost": 4.22,
    "pnl_pct": 3.79
  },
  "reasoning_summary": {
    "one_liner": "日线三买构建中，4.37是多空分水岭",
    "action": "持有",
    "action_detail": "底仓不动，等底分型确认后加仓",
    "key_level_down": 4.37,
    "key_level_down_meaning": "30分中枢上沿",
    "key_level_up": 4.50,
    "key_level_up_meaning": "日线前高压力",
    "stop_loss": 4.13,
    "scenarios": [
      {"name": "强", "probability": "40%", "brief": "守4.37冲4.50"},
      {"name": "弱", "probability": "40%", "brief": "破4.37回4.13"},
      {"name": "废", "probability": "20%", "brief": "破4.13止损"}
    ],
    "generated_at": "2026-05-16T15:30:00"
  },
  "monitor_conditions": {
    "triggers": [
      {
        "id": "t1",
        "type": "price_below",
        "level": 4.37,
        "state": "normal",
        "message_on_trigger": "到承接位了，观察5分钟是否出底分型",
        "action_on_trigger": "关注",
        "next_level_down": 4.13
      },
      {
        "id": "t2",
        "type": "price_below",
        "level": 4.13,
        "state": "normal",
        "message_on_trigger": "三买失败，止损走人",
        "action_on_trigger": "止损"
      },
      {
        "id": "t3",
        "type": "price_above",
        "level": 4.50,
        "state": "normal",
        "message_on_trigger": "突破前高，趋势加速",
        "action_on_trigger": "加仓"
      }
    ]
  }
}
```

### 2.3 实时价格批量接口（已有，复用）

```python
# 已有: server/services/price_service.py
async def get_batch_prices(symbols: list[str]) -> dict[str, dict]
```

前端每 10 秒调一次，批量拉所有面板股票的实时价格。

### 2.4 添加股票接口（复用现有 watchlist）

现有 `watchlist` API 已支持分组管理。只需确保分组名对应：
- 默认创建三个分组：`持仓`（自动）、`自选`、`备选`
- 添加股票时指定 group_name

```python
# 已有: POST /watchlist/add
# body: { "symbol": "sh.601919", "name": "中远海控", "group_name": "备选" }
```

添加后自动触发 snapshot 计算 + 统一推演（已有 `sync_new_watchlist_symbol` background task，扩展它）。

### 2.5 监控条件提取（从推演结果中自动提取）

```python
# server/engines/ai_native/unified_reasoning_service.py 中新增

MONITOR_EXTRACT_PROMPT = """从以下推演全文中提取盯盘监控条件，返回 JSON：

{
  "triggers": [
    {
      "type": "price_below|price_above",
      "level": 数字,
      "message_on_trigger": "触发时显示的一句话",
      "action_on_trigger": "关注|加仓|减仓|止损|观望"
    }
  ]
}

规则：
- 最多提取 3-4 个最重要的触发条件
- level 必须是推演中明确提到的关键价格
- message 简洁，15字以内

只返回 JSON。"""
```

用 flash 模型提取，和摘要提取一起跑，成本极低。

---

## 三、前端组件

### 3.1 新增页面：`web/src/pages/WatchBoard.jsx`

盯盘面板主页面，包含：
- 顶部搜索栏（添加股票）
- 三组卡片区域（持仓/自选/备选）
- 实时价格轮询
- 卡片状态计算

### 3.2 卡片组件：`web/src/components/WatchCard.jsx`

```jsx
function WatchCard({ item, currentPrice, onClick }) {
  // 根据实时价格和 monitor_conditions 计算当前状态
  const cardState = computeCardState(item, currentPrice)
  // cardState: "normal" | "alert" | "danger" | "confirm"
  
  return (
    <div className={`watch-card state-${cardState}`} onClick={onClick}>
      {/* 头部：名称 + 价格 */}
      {/* 持仓信息（如有） */}
      {/* 推演摘要（一句话，随状态变化） */}
      {/* 底部：关键位 + 动作标签 */}
    </div>
  )
}
```

### 3.3 卡片状态计算逻辑（纯前端）

```javascript
function computeCardState(item, currentPrice) {
  const triggers = item.monitor_conditions?.triggers || []
  
  for (const trigger of triggers) {
    if (trigger.type === "price_below" && currentPrice <= trigger.level) {
      if (trigger.action_on_trigger === "止损") return "danger"
      return "alert"
    }
    if (trigger.type === "price_above" && currentPrice >= trigger.level) {
      return "confirm"
    }
  }
  
  return "normal"
}

function getDisplayMessage(item, cardState, currentPrice) {
  // 正常状态：显示推演的 one_liner
  if (cardState === "normal") return item.reasoning_summary.one_liner
  
  // 触发状态：显示 trigger 的 message_on_trigger
  const activeTrigger = findActiveTrigger(item, currentPrice)
  if (activeTrigger) return activeTrigger.message_on_trigger
  
  return item.reasoning_summary.one_liner
}

function getDisplayAction(item, cardState, currentPrice) {
  if (cardState === "normal") return item.reasoning_summary.action
  
  const activeTrigger = findActiveTrigger(item, currentPrice)
  if (activeTrigger) return activeTrigger.action_on_trigger
  
  return item.reasoning_summary.action
}
```

### 3.4 实时价格轮询

```javascript
// 盘中每 10 秒刷新一次
useEffect(() => {
  const symbols = allItems.map(item => item.symbol)
  
  const poll = async () => {
    const prices = await apiJson(
      `${API_BASE}/data/batch-prices?symbols=${symbols.join(',')}`
    )
    setPrices(prices.data)
  }
  
  poll()
  const interval = setInterval(poll, 10000)
  return () => clearInterval(interval)
}, [allItems])
```

### 3.5 添加股票搜索框

复用现有 `StockSearch.jsx` 组件，加分组选择：

```jsx
function AddStockBar({ onAdd }) {
  const [group, setGroup] = useState('自选')
  
  return (
    <div className="add-bar">
      <StockSearch onSelect={(symbol, name) => onAdd(symbol, name, group)} />
      <select value={group} onChange={e => setGroup(e.target.value)}>
        <option>自选</option>
        <option>备选</option>
      </select>
    </div>
  )
}
```

### 3.6 点击卡片展开

点击卡片后右侧展开面板（或弹出 drawer），显示：
- 完整推演全文（markdown 渲染）
- 问答输入框（复用现有 CoachPanel 的聊天逻辑）
- 场景概率条

可以直接复用现有 `AIStructureCoachPanel` 的问答部分。

---

## 四、路由和导航

### 4.1 新增路由

```jsx
// web/src/App.jsx
<Route path="/watchboard" element={<WatchBoard />} />
```

### 4.2 导航入口

在现有侧边栏或顶部导航加入"盯盘"入口，或者直接作为 Dashboard 的默认视图。

---

## 五、与现有系统的关系

| 现有功能 | 关系 |
|---|---|
| `WatchlistPanel.jsx` | 盯盘面板替代它的"列表展示"职责，但 watchlist 数据层继续使用 |
| `AIStructureCoachPanel.jsx` | 问答/聊天部分继续使用，作为卡片展开后的详情 |
| `PositionList.jsx` | 持仓数据继续使用，面板的"持仓"分组从这里取 |
| `price_monitor.py` | 已有的价格监控 worker，可以复用做后台状态判断和推送 |

---

## 六、实施步骤（给 Codex 的执行顺序）

```
Step 1: 后端 - 盯盘面板聚合接口
        - 新增 GET /ai-structure/watchboard
        - 从 positions + watchlist_groups + watchlist_items 组装列表
        - 从 ai_structure_reasoning_runs 取每只票的 summary_json
        - 调用 get_batch_prices 附加实时价格

Step 2: 后端 - 监控条件提取
        - 在 unified_reasoning_service.py 中
        - run_unified_reasoning 完成后额外调 flash 提取 monitor_conditions
        - 存入 summary_json 中的 monitor_conditions 字段

Step 3: 前端 - WatchBoard 页面骨架
        - 新建 web/src/pages/WatchBoard.jsx
        - 调用 watchboard API 获取数据
        - 三组卡片区域布局
        - 顶部添加搜索栏

Step 4: 前端 - WatchCard 组件
        - 新建 web/src/components/WatchCard.jsx
        - 四种状态样式（normal/alert/danger/confirm）
        - 状态计算逻辑 computeCardState

Step 5: 前端 - 实时价格轮询
        - 盘中 10 秒间隔 polling
        - 价格更新后重新计算所有卡片状态
        - 状态变化时卡片有过渡动画

Step 6: 前端 - 卡片展开详情
        - 点击卡片展开右侧 drawer
        - 展示完整推演全文 + 问答框
        - 复用 AIStructureCoachPanel 的聊天逻辑

Step 7: 路由 + 导航
        - App.jsx 加路由
        - 导航栏加入口

Step 8: 添加股票后自动触发推演
        - watchlist/add 接口中
        - sync_new_watchlist_symbol 完成后触发 run_unified_reasoning
```

---

## 七、关键设计决策

1. **卡片状态完全由前端计算** — 不需要后端实时判断。后端只提供监控条件（价格阈值），前端拿到实时价格后自己对比。简单、实时、无延迟。

2. **推演结果是静态的，价格是动态的** — 推演每天跑一次（收盘后），但卡片状态随盘中价格实时变化。推演告诉你"盯什么"，价格告诉你"到了没"。

3. **持仓组自动维护** — 有持仓记录就自动出现在"持仓"组，不需要手动添加。清仓后自动移到"自选"或消失（可配置）。

4. **搜索添加后 15 秒出结果** — 添加新股票后，后台自动走 snapshot 计算 + 统一推演，约 15 秒完成。期间卡片显示"推演中..."占位状态。

---

## 八、样式参考

参照之前的 mockup：
- 卡片圆角 12px，细边框
- 持仓卡片略大（多一行持仓信息）
- 状态通过左边框颜色区分：橙色=触发、红色=破位、绿色=确认
- 动作标签用色块 badge
- 间距宽松，一屏能看 6-9 张卡片
