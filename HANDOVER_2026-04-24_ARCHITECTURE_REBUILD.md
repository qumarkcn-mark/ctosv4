# 交接文件：CT-OS V4 架构重建第一批收口

> 日期：2026-04-24
> 交接给：下一位 agent
> 当前分支：`main`
> 当前状态：第一批架构 contract、Radar 兼容壳、关键 contract tests 已完成。工作区仍有大量未提交改动，禁止随意 revert。

## 1. 今日核心结论

CT-OS 后续按三阶段推进：

1. Phase 1：缠论分析工作台，展示 K 线、笔、线段、中枢、雷达。
2. Phase 2：策略教练与提醒，展示候选股票、策略预案、提醒用户检查条件。
3. Phase 3：私有 QMT 日内 T 执行，只服务用户自己的 Windows QMT 环境，不进入公开产品。

公开产品只开放 Phase 1/2，不开放 QMT。

缠论基础结构只认 `chan.py`：

- `server/vendor/chan_py` 是 K 线包含、分型、笔、线段、中枢、买卖点的唯一权威。
- `chan_engine/parser.py`、`chan_engine/fsm.py` 等自研结构算法只能作为 legacy/reference。
- CT-OS 自研逻辑只能基于 `chan.py adapter` 输出做策略、计划、提醒和展示。

数据源权威边界：

- Scanner：TDX 本地日线湖，不复权，用于全市场候选发现。
- Radar/Chan 正式结构：BaoStock 多级别湖，前复权，喂给 `chan.py`。
- 腾讯行情：当前价展示、实时 preview K 线、普通价格提醒；不能用于正式结构判断。
- QMT/XtQuant：Phase 3 私有执行行情、账户、订单、成交；CT-OS Core 不直接连 QMT。

## 2. 今日新增/更新文件

新增/更新的架构文档：

- `PRODUCT_ROADMAP.md`
- `ARCHITECTURE_REFACTOR_TASKS.md`
- `ARCHITECTURE_REPLAN.md`
- `docs/ARCHITECTURE_FOUNDATION_DECISIONS.md`
- `docs/DATA_SOURCE_CONTRACT.md`
- `docs/STRUCTURE_ALGORITHM_INVENTORY.md`
- `docs/STRATEGY_CONTRACT.md`
- `docs/EXECUTION_INTENT_CONTRACT.md`
- `docs/RADAR_API_CONTRACT.md`

新增后端与测试：

- `server/api/radar.py`
- `tests/test_radar_api.py`
- `tests/test_chan_matrix_v2_contract.py`

更新：

- `server/app.py` 注册了 `/api/radar`。

注意：`server/app.py` 在本轮之前已经有 scanner 注册等未提交变更，本轮只在 scanner 后追加 radar router，不要回退用户已有改动。

## 3. 已完成任务状态

`ARCHITECTURE_REFACTOR_TASKS.md` 的“第一批开工任务”已经全部完成：

1. `[x]` 写 `PRODUCT_ROADMAP.md`
2. `[x]` 写 `docs/ARCHITECTURE_FOUNDATION_DECISIONS.md`
3. `[x]` 写 `docs/DATA_SOURCE_CONTRACT.md`
4. `[x]` 写 `docs/STRATEGY_CONTRACT.md`
5. `[x]` 写 `docs/EXECUTION_INTENT_CONTRACT.md`
6. `[x]` 盘点现有基础结构算法来源
7. `[x]` 写 `docs/RADAR_API_CONTRACT.md`
8. `[x]` 新增 `server/api/radar.py` 兼容壳
9. `[x]` 给 `/api/chan/matrix/v2` 补 characterization tests
10. `[x]` 给 `/api/radar/{symbol}` 补 contract tests
11. `[x]` 更新 `ARCHITECTURE_REPLAN.md` 当前状态

## 4. 测试结果

已运行并通过：

```bash
./venv/bin/pytest tests/test_chan_matrix_v2_contract.py tests/test_radar_api.py -v
```

结果：

```text
6 passed
```

也跑过语法检查：

```bash
./venv/bin/python -m py_compile server/api/radar.py tests/test_radar_api.py tests/test_chan_matrix_v2_contract.py
```

结果通过。

注意：

- 系统 `python3` 没有 `pytest`。
- 项目 venv 可用：`./venv/bin/pytest`。
- 当前 venv 是 Python 3.9.6，因此新代码不要使用 `dict | None` 这类 Python 3.10+ 类型语法。

## 5. Radar 兼容壳现状

`server/api/radar.py` 现在是第一版兼容壳：

- 内部调用 `analyze_matrix_state()`。
- 输出 `radar.v1` contract 形状。
- 支持 `EMPTY` / `HOLDING` 模式互斥：
  - 空仓：`entry_plan != null`，`holding_plan == null`
  - 持仓：`entry_plan == null`，`holding_plan != null`
- 返回 `data_source`、`freshness`、`structure`、`strategy`、`plans`、`disclaimer`。
- `legacy_refs.compatibility_mode = true`，说明它还没有真正拆开旧 matrix 引擎。

这一步的目标只是让新 contract 先站起来，后续不要直接在 `chan_service.py` 继续塞新功能。

## 6. chan.py 对比结果

已对比本地 `server/vendor/chan_py` 与 GitHub 公开仓库：

公开仓库：

```text
https://github.com/Vespa314/chan.py
HEAD = 616b15f606d345d8a6a3dc521236b0f9d75199c7
```

结论：

- 本地和 GitHub 公共版不是完全相同。
- 本地基本来自 GitHub 当前公开版，但做过少量本地补丁。

统计：

```text
本地文件数: 67
GitHub 文件数: 107
共同文件: 66
完全相同: 58
内容不同: 8
本地独有: 1
GitHub 独有: 41
```

差异说明：

- 本地增加 `DataAPI/FutuAPI.py`，并在 `Common/CEnum.py`、`Chan.py` 加了 `FUTU` 数据源入口。
- 本地对部分文件加了 `from __future__ import annotations`，用于 Python 3.9 兼容。
- GitHub 独有文件主要是 `Image/*` 和 `.gitignore`，对运行逻辑影响不大。

建议后续标记 vendor 来源为：

```text
Vespa314/chan.py public main @ 616b15f606d345d8a6a3dc521236b0f9d75199c7
local patches:
- Python 3.9 annotation compatibility
- FUTU data source extension
```

## 7. 当前工作区状态提醒

工作区有大量未提交修改和未跟踪文件，其中不少不是本轮改的。下一位 agent 必须遵守：

- 不要 `git reset --hard`。
- 不要 `git checkout --` 回退文件。
- 不要清理未跟踪文件。
- 如果要改已有文件，先看 `git diff`，确认不会覆盖用户工作。

本轮直接相关文件包括：

- `ARCHITECTURE_REFACTOR_TASKS.md`
- `ARCHITECTURE_REPLAN.md`
- `PRODUCT_ROADMAP.md`
- `docs/*CONTRACT.md`
- `docs/STRUCTURE_ALGORITHM_INVENTORY.md`
- `server/api/radar.py`
- `server/app.py`
- `tests/test_radar_api.py`
- `tests/test_chan_matrix_v2_contract.py`

## 8. 明天建议第一步

建议从 `docs/COACH_EVENT_CONTRACT.md` 开始。

原因：

- Phase 2 的提醒、策略触发、用户响应、行为纠偏都需要事件模型。
- 未来 QMT 审计也需要同一套事件/审计思想。
- 先定义事件 contract，再写 push/alert/behavior 代码，不容易散。

建议文档覆盖：

- `coach_events`
- `strategy_triggers`
- `alert_deliveries`
- 事件类型
- 结构快照引用
- 策略版本
- 用户响应
- 后续结果
- 去重键
- stale 数据处理
- “仅供参考”文案要求

写完后更新：

- `ARCHITECTURE_REFACTOR_TASKS.md`
- `ARCHITECTURE_REPLAN.md`

## 9. 明天第二步候选

如果 `COACH_EVENT_CONTRACT` 完成，下一步建议二选一：

1. 定义 symbol normalize / freshness / worker 写权限的代码 contract。
2. 新增 `server/engines/structure/chan_adapter.py`，把 `chan_detail_service.py` 的 `chan.py` 调用逐步收敛。

优先级建议：

- 如果继续打地基：先补 symbol/freshness/worker contract。
- 如果开始代码拆分：先建 `chan_adapter.py`，但必须保持旧 API 输出不变。

## 10. 快速恢复命令

```bash
cd /Users/markqu/Desktop/ct-os-v4
git status --short
sed -n '1,220p' ARCHITECTURE_REFACTOR_TASKS.md
sed -n '1,220p' ARCHITECTURE_REPLAN.md
./venv/bin/pytest tests/test_chan_matrix_v2_contract.py tests/test_radar_api.py -v
```
