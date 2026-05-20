# Codex 任务：实现 V5 盯盘面板

## 背景

CT-OS V4 是一个缠论交易教练系统。我们刚完成了"统一推演"功能（一次 LLM 调用生成完整的结构推演+操作建议），现在要实现配套的盯盘面板——一个桌面 Web 端的卡片式实时监控页面。

## 关键文档

请先阅读以下文件：
- `docs/v5_watchboard_plan.md` — 盯盘面板完整工程化方案（架构、接口、组件、步骤）
- `docs/v5_unified_reasoning_plan.md` — 统一推演系统方案（盯盘面板的数据来源）
- `CLAUDE.md` — 项目规范和技术栈
- `DESIGN.md` — 设计系统

## 现有代码参考

- `server/api/watchlist.py` — 自选股分组管理 API（复用其数据层）
- `server/api/ai_structure.py` — AI 结构相关 API（在这里添加新路由）
- `server/engines/ai_native/unified_reasoning_service.py` — 统一推演 service（如果已实现）
- `server/services/price_service.py` — 行情服务，`get_batch_prices()` 批量拉实时价
- `web/src/features/aiStructure/AIStructureCoachPanel.jsx` — 现有推演面板（问答部分可复用）
- `web/src/components/WatchlistPanel.jsx` — 现有自选面板
- `web/src/components/StockSearch.jsx` — 股票搜索组件（复用）
- `server/scripts/test_unified_batch.py` — 统一推演测试脚本（压力支撑计算逻辑可参考）

## 要实现的内容

按以下顺序实现：

### Step 1: 后端聚合接口

在 `server/api/ai_structure.py` 中新增 `GET /ai-structure/watchboard`：
- 从 `positions` 表取持仓列表
- 从 `watchlist_groups` + `watchlist_items` 取自选和备选列表（分组名为"自选"和"备选"）
- 从 `ai_structure_reasoning_runs` 取每只票最新的 `summary_json`（prompt_version = "unified_reasoning.v1"）
- 调用 `get_batch_prices` 附加实时价格
- 返回格式见 `docs/v5_watchboard_plan.md` 第二节

### Step 2: 监控条件提取

在 `server/engines/ai_native/unified_reasoning_service.py` 中：
- `run_unified_reasoning` 完成后，额外用 flash 模型从推演全文提取 `monitor_conditions`
- 提取 3-4 个关键价格触发条件（price_below / price_above）
- 每个条件包含：level（价格）、message_on_trigger（触发时显示的话）、action_on_trigger（动作标签）
- 存入 `summary_json` 的 `monitor_conditions` 字段

提取用的 prompt：
```
从以下推演全文中提取盯盘监控条件，返回 JSON：
{
  "triggers": [
    {
      "type": "price_below|price_above",
      "level": 数字,
      "message_on_trigger": "触发时显示的一句话（15字以内）",
      "action_on_trigger": "关注|加仓|减仓|止损|观望"
    }
  ]
}
最多 4 个条件，只取推演中明确提到的关键价格。只返回 JSON。
```

### Step 3: 前端 WatchBoard 页面

新建 `web/src/pages/WatchBoard.jsx`：
- 调用 `GET /ai-structure/watchboard` 获取所有数据
- 三组区域：持仓 / 自选 / 备选，每组一个标签 + 卡片网格
- 顶部添加搜索栏（复用 StockSearch 组件 + 分组下拉 + 添加按钮）
- 添加股票调用现有 `POST /watchlist/add`，添加后触发推演

### Step 4: WatchCard 组件

新建 `web/src/components/WatchCard.jsx`：

卡片内容（从上到下）：
1. 股票名 + 实时价 + 涨跌%
2. 持仓信息：股数 · 成本 · 浮盈%（无持仓不显示这行）
3. 推演摘要：一句话（随状态变化）
4. 底部：下方关键位 ▼ + 上方关键位 ▲ + 动作标签 badge

四种状态（通过左边框颜色区分）：
- `normal`：默认灰色边框
- `alert`：橙色左边框 — 价格触及关键位
- `danger`：红色左边框 — 关键位失守
- `confirm`：绿色左边框 — 确认信号出现

状态计算逻辑（纯前端）：
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
```

状态变化时：
- 摘要文字切换为 trigger 的 `message_on_trigger`
- 动作标签切换为 trigger 的 `action_on_trigger`
- 关键位数字更新为下一个关注点

### Step 5: 实时价格轮询

在 WatchBoard 中：
- 盘中（9:30-15:00）每 10 秒调用 `GET /data/batch-prices?symbols=...`
- 非交易时间不轮询
- 价格更新后重新计算所有卡片状态

### Step 6: 卡片展开详情

点击卡片后展开右侧 drawer 或底部展开区：
- 显示完整推演全文（markdown 渲染）
- 场景概率列表
- 问答输入框（调用现有 chat 接口 `POST /ai-structure/chat`）
- 复用 AIStructureCoachPanel 的问答逻辑

### Step 7: 路由

在 `web/src/App.jsx` 中添加 `/watchboard` 路由，导航栏加入口。

## 设计要求

- 卡片圆角 12px，0.5px 细边框
- 卡片网格：`grid-template-columns: repeat(auto-fit, minmax(220px, 1fr))`，gap 10-12px
- 动作标签用色块 badge：持有=灰色、加仓=绿色、减仓=橙色、止损=红色、观望=浅灰、关注=蓝色
- 状态切换用 CSS transition，左边框颜色 + 摘要文字同时变
- 一屏能看 6-9 张卡片
- 遵循 DESIGN.md 的字体、颜色、间距规范

## 注意事项

- 使用 `./venv/bin/python` 运行 Python（系统 Python 版本不够）
- 不使用 ORM，直接写 SQL
- 前端用 React 19 + Vite
- 现有 `watchlist_groups` 默认创建时有三个组，确保"自选"和"备选"在其中
- 如果 `unified_reasoning_service.py` 还不存在，先按 `docs/v5_unified_reasoning_plan.md` 实现它
