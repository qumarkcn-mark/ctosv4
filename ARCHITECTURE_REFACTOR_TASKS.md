# CT-OS V4.0 架构重构任务清单

> 创建日期：2026-04-24
> 来源：`ARCHITECTURE_REPLAN.md` 工程审查
> 目标：按三阶段产品路线图重建底层架构：先完成缠论分析工作台，再落地策略教练与提醒，最后隔离接入 QMT 日内 T 执行。

## 执行原则

- 先定 contract，再改实现。
- 先补 characterization tests，再拆大文件。
- 缠论基础结构以 `chan.py` 为唯一权威实现；CT-OS 不再重复实现 K 线包含、分型、笔、线段、中枢。
- CT-OS 自研逻辑只能建立在 `chan.py adapter` 输出之上，负责 contract、交易教练规则和产品表达。
- 数据源必须有单一权威口径：Scanner 用 TDX 日线，Radar/Chan 结构用 BaoStock 多级别，实时价用腾讯。
- 同一次 `chan.py` 结构分析不得混用不同数据源或复权口径。
- `chan_service.py` 不再承接新功能，只做过渡兼容。
- AI 只消费结构化 JSON，不参与结构判断、止损线、目标价、仓位规则生成。
- 空仓模式和持仓模式字段互斥：空仓只看入场计划，持仓只看持仓管理。
- 所有涉及交易动作的文案必须包含“仅供参考”。
- Phase 1/2 坚持交易教练定位，不执行交易；Phase 3 的 QMT 自动交易必须走独立 Execution Layer。
- 自动交易不得直接从 Structure 或 Decision Engine 下单，必须经过 Execution Intent、Risk Gate、QMT Adapter、Audit Log。
- 任何 execution intent 不得使用前复权价格作为委托价，委托价必须来自真实行情/QMT 行情。

## 产品路线图

### Phase 1：Chan Analysis Workbench

目标：能展示缠论分析 K 线和雷达，让用户看懂一只股票当前结构。

- [ ] K 线展示支持 `chan.py` 输出的笔、线段、中枢、买卖点。
- [ ] Radar 展示多级别结构、数据源、复权口径、freshness。
- [ ] 空仓/持仓模式在 UI 和 API contract 中严格分离。
- [ ] 不做自动交易，不做策略执行，只展示结构和解释。

### Phase 2：Strategy Coach & Alerts

目标：把已构思的策略系统化，展示候选股票、生成预案，并在条件触发时提醒用户。

- [ ] 策略以 `Strategy Contract` 表达，不散落在 scanner/radar/rotation/push。
- [ ] Scanner 输出候选股，Radar 展示单票预案，Rotation 展示横向比较。
- [ ] Push/Alert 告诉用户“何时检查什么条件”，不自动下单。
- [ ] Coach/Event Log 记录提醒依据、策略版本、用户响应和后续结果。

### Phase 3：QMT Intraday T Execution

目标：接入 QMT，在用户授权范围内对有底仓股票执行日内 T。

- [ ] QMT 自动执行作为独立 Execution Layer，不耦合 Structure/Decision。
- [ ] 所有自动交易先生成 `Execution Intent`。
- [ ] 所有 intent 必须通过 `Risk Gate`。
- [ ] 支持 dry-run、一键停止、最大日内交易次数、最大亏损、底仓保护。
- [ ] 所有下单、撤单、成交、失败都写入 Audit Log。

## Phase A：重构地基

- [x] 新增 `PRODUCT_ROADMAP.md`。
  - 明确 Phase 1/2/3 的目标、边界、非目标和验收标准。
  - 明确 Phase 1/2 不执行交易，Phase 3 才进入 QMT 执行系统。

- [x] 新增 `docs/ARCHITECTURE_FOUNDATION_DECISIONS.md`。
  - 记录底层不可逆决定：symbol 标准、复权口径、数据源权威、价格口径、worker 写权限、vendor 策略。
  - 作为后续 contract 和代码评审的判断基准。

- [x] 新增 `docs/DATA_SOURCE_CONTRACT.md`。
  - 定义 Scanner、Radar、Rotation、Behavior、Push 各自的数据源。
  - 定义每类数据的权威源、fallback 源、复权口径和 freshness 规则。
  - 明确 fallback 数据是否允许参与结构判断。
  - 明确 symbol 格式标准：内部统一 `sh.600519` / `sz.000001`。

- [x] 定义 symbol normalize 规则。
  - 内部统一 `sh.600519` / `sz.000001`。
  - API 入参兼容 `sh600519`、`sh.600519`、`sh-600519`。
  - 腾讯接口转换为 `sh600519` / `sz000001`。
  - TDX 文件名转换为 `sh600519.day` / `sz000001.day`。
  - 为 normalize 函数补单元测试。
  - 新增 `server/domain/symbols.py` 和 `tests/test_symbol_contract.py`，后续旧 helper 逐步迁移。

- [x] 定义复权口径和真实价格口径的协调规则。
  - Scanner 使用 TDX 不复权 `adjustflag=3`。
  - Radar/Chan 结构使用 BaoStock 前复权 `adjustflag=2`。
  - 交易记录、持仓成本、实时价、止损价、台阶止损价全部使用真实交易价格口径。
  - 结构分析可以判断“结构是否破坏”，但不能直接覆盖真实止损价。
  - 已补 `docs/SYMBOL_FRESHNESS_WORKER_CONTRACT.md`，明确结构价格和真实交易价格不能混用为执行价格。

- [x] 定义 freshness contract。
  - 统一输出 `source`、`last_bar_at`、`is_stale`、`stale_reason`。
  - stale 数据不得触发交易动作，只能触发数据过期提醒。
  - Scanner 必须知道 TDX 盘后同步是否完成。
  - Radar 必须知道 BaoStock 多级别数据是否完整。
  - 已补统一 freshness shape、`stale_reason` 枚举和 stale 阻断规则。

- [x] 定义 worker 写权限。
  - 明确哪个 worker 可以写 `positions`、`alerts`、`scan_results`、`radar_deductions`。
  - 行情湖 worker 不写用户事实。
  - LLM worker 不写结构判断字段。
  - 已补 worker/script 写入所有权表，明确 push worker 只负责 delivery，不判断策略真假。

- [x] 定义 `chan.py` vendor 策略。
  - `server/vendor/chan_py` 视为第三方 vendor，不直接改源码。
  - 所有适配写在 `server/engines/structure/chan_adapter.py`。
  - 升级 `chan.py` 前必须跑 adapter contract tests。
  - 已新增 `server/engines/structure/chan_adapter.py` 和 `tests/test_chan_adapter_contract.py`，第一版先收敛 CChan 调用边界。

- [x] 定义 API 版本迁移策略。
  - `/api/chan/matrix/v2` 保留兼容。
  - `/api/radar/{symbol}` 作为新 contract。
  - 前端按页面逐步迁移，不一次性切换。
  - 已新增 `docs/API_MIGRATION_STRATEGY.md`，锁定版本所有权、迁移阶段、legacy freeze 和 removal checklist。

- [x] 定义测试边界。
  - symbol normalize。
  - data source routing。
  - Radar 空仓/持仓字段互斥。
  - stale 数据不触发交易动作。
  - scanner 只读 TDX。
  - chan adapter 输出结构字段稳定。
  - 已补 symbol、Radar/matrix、chan adapter contract tests；scanner/data source 更细的 ownership tests 留在对应模块迁移时补。

- [x] 梳理并收敛现有两套缠论算法来源。
  - 确认基础结构统一来自 `server/vendor/chan_py`。
  - 标记现有自研/重复实现的 K 线包含、分型、笔、线段、中枢逻辑。
  - 重复实现不得继续作为权威判断，只能作为过渡兼容或测试对照。

- [x] 梳理 `/api/chan/matrix/v2` 当前输出字段。
  - 标记字段归属：Structure / Decision / AI Narrative / UI 兼容。
  - 明确哪些字段后续会进入 `/api/radar/{symbol}`。
  - Structure 字段必须能追溯到 `chan.py adapter` 输出。
  - 当前 `/api/radar/{symbol}` 已使用 `chan_adapter` 作为 `structure/data_source/freshness` 来源；strategy/plans 的组装已迁到 `radar_planner`，规则输入来自 adapter-derived levels。
  - 字段归属表已写入 `docs/API_MIGRATION_STRATEGY.md`。

- [x] 新增 `docs/RADAR_API_CONTRACT.md`。
  - 定义 `GET /api/radar/{symbol}?user_id=1` 返回结构。
  - 定义 `mode = EMPTY | HOLDING`。
  - 定义 `structure`、`strategy`、`entry_plan`、`holding_plan`、`plans`、`disclaimer`。
  - 明确 `structure` 字段全部来自 `chan.py adapter`。
  - 暴露 `data_source` 和 `freshness`，说明结构数据来源、复权口径、最后更新时间。
  - 写清楚空仓/持仓字段互斥规则。

- [x] 新增 `docs/STRATEGY_CONTRACT.md`。
  - 定义策略 ID、策略版本、适用范围、输入、条件、预案、提醒、风险字段。
  - 明确策略只输出 plans / alerts / execution intent 候选，不直接下单。
  - 覆盖战法一、战法二、扫描器、调仓、未来日内 T 策略。

- [x] 新增 `docs/COACH_EVENT_CONTRACT.md`。
  - 定义 `coach_events`、`strategy_triggers`、`alert_deliveries` 的最小字段。
  - 记录提醒依据、结构快照引用、策略版本、用户响应和结果。
  - 为 Phase 2 行为纠偏和 Phase 3 审计留下数据基础。
  - 明确 stale 数据只能记录 `DATA_STALE_BLOCKED`，不能触发交易动作提醒。
  - 明确涉及交易动作的事件和提醒必须包含“仅供参考”。

- [x] 新增 `docs/EXECUTION_INTENT_CONTRACT.md`。
  - 定义最小 execution intent：symbol、side、quantity、price_type、limit_price、reason、risk_checks。
  - 明确 intent 只能由 Phase 3 Execution Layer 消费。
  - 明确禁止使用前复权结构价作为委托价。

- [x] 给 `/api/chan/matrix/v2` 补 characterization tests。
  - 空仓：必须返回 `entry_checklist`、`strategy_classification`、`position_sizing` 等现有关键字段。
  - 持仓：必须返回 `holding_status`、`holding_stage_v2`。
  - 异常：引擎报错时必须返回稳定 error envelope。

- [x] 新增 `server/api/radar.py` 兼容壳。
  - 内部先复用现有 `server.api.chan.get_chan_matrix_v2` 或 `analyze_matrix_state`。
  - 不在第一版拆 `chan_service.py`。
  - 输出稳定 `Radar API contract`。

- [x] 注册 radar router。
  - 在 `server/app.py` 增加 `/api/radar`。
  - 前端暂不切换，先保证后端 contract 可测。

- [x] 给 `/api/radar/{symbol}` 补 contract tests。
  - 空仓返回 `entry_plan`，`holding_plan = null`。
  - 持仓返回 `holding_plan`，`entry_plan = null`。
  - `plans` 必须来自算法结构，不来自 LLM。
  - 返回 `disclaimer`。

## Phase B：边界拆分

- [x] 新建目标目录。
  - `server/domain/`
  - `server/data/`
  - `server/engines/structure/`
  - `server/engines/decision/`
  - `server/engines/coach/`
  - 已新增 `server/data/__init__.py` 和 `server/engines/coach/__init__.py`，明确 data/coach 边界。

- [x] 新增领域对象草案。
  - `server/domain/enums.py`
  - `server/domain/models.py`
  - `server/domain/contracts.py`
  - 先覆盖 `RadarContract`、`LevelStructure`、`EntryPlan`、`HoldingPlan`、`RiskPlan`。
  - 已补 `tests/test_domain_contracts.py`，确保 enum/dataclass/TypedDict contract 可导入并兼容 adapter shape。

- [x] 从 `chan_service.py` 先抽 Decision Engine。
  - [x] `server/engines/decision/entry_planner.py`
  - [x] `server/engines/decision/holding_manager.py`
  - [x] `server/engines/decision/risk_sizing.py`
  - [x] `server/engines/decision/target_planner.py`
  - 每拆一个模块，保持旧 API 输出不变。
  - 当前已新增 `server/engines/decision/radar_planner.py`，先承接 Radar contract 的 `strategy/entry_plan/holding_plan/plans` 组装。
  - 空仓入场五条件已迁入 `entry_planner.py`。
  - 持仓六阶段状态机已迁入 `holding_manager.py`。
  - 空仓 ATR 止损校验、固定风险仓位测算、空仓/持仓目标价、赔率检查已迁入 `risk_sizing.py` / `target_planner.py`，并接入 Radar entry_plan / holding manager。
  - `radar_planner.py` 不再导入 `server.api.chan` 或 `chan_service` helper；Radar API 的 decision 输入来自 adapter-derived levels。

- [x] 再抽 Structure Engine。
  - [x] `server/engines/structure/lifecycle.py`
  - [x] `server/engines/structure/nesting.py`
  - [x] `server/engines/structure/strategy_detector.py`
  - [x] `server/engines/structure/zhongshu.py`
  - [x] `server/engines/structure/divergence.py`
  - 每个模块补纯函数单元测试。
  - 当前已新增 `server/engines/structure/derived_facts.py`，在 `chan_adapter` 输出上派生 `price/zg/zd/state/zoushi_type/patterns/classifications/div_info/interval_nesting`，供 Radar decision planner 消费。
  - 背驰检测、背驰中继/转折分类、区间套检测、中枢走势分类、生命周期分类、买卖点 pattern 派生已拆到独立纯函数模块；`derived_facts.py` 只做聚合。

- [x] 把 `server/api/chan.py` 降级为兼容层。
  - 新前端消费 `/api/radar/{symbol}`。
  - 旧 `/api/chan/matrix/v2` 保留一段时间，只做适配。
  - 第一阶段已完成：Radar structure 优先来自 `server.engines.structure.chan_adapter`。
  - 第二阶段已完成：Radar `strategy/plans` 组装边界迁到 `server.engines.decision.radar_planner`。
  - 第三阶段已完成：Radar decision 输入已使用 `chan_adapter` derived levels，旧 matrix 兜底已移除。
  - `web/src/components/TRadarV2.jsx` 已切到 `/api/radar/{symbol}`，组件内部用 adapter 函数映射 Radar contract 到现有展示 shape。

## Phase C：产品闭环

- [ ] Strategy Contract 落地。
  - 将战法一、战法二、扫描器、调仓规则统一表达成 strategy definitions。
  - 策略输出 plans / alerts，不直接改 UI 文案或执行交易。
  - 策略版本写入触发记录，便于复盘。

- [ ] Scanner 闭环复核。
  - 确认 `server/api/scanner.py`、`web/src/pages/Scanner.jsx`、`web/src/components/ScanCard.jsx` 已满足当前 PRD。
  - 确认候选展示、加入观察库、删除候选、状态统计都可用。

- [ ] Scanner 测试补齐。
  - 候选只展示 `ready` 状态。
  - 加入观察库后删除对应 `scan_results`。
  - 删除候选后列表不再出现。
  - 手动触发扫描时 job 状态可轮询。

- [ ] 重做 `RotationCompass`。
  - 后端从 `rotation_scorer.py` 过渡到 `rotation_planner.py`。
  - 输出每只票的甲乙丙预案。
  - 分数只做排序，不做主视觉。
  - 前端不再以“建议砍出/建议加仓”为核心表达。

- [ ] 收敛 `BehaviorReport`。
  - 只保留交易行为分析。
  - 移除持仓防线扫描、行情结构判断入口。
  - 行情结构功能回到 Radar 或 RotationCompass。

- [x] `TRadarV2` 切换到 Radar contract。
  - 不再直接依赖 `chan_service.py` 内部字段。
  - 空仓/持仓 UI 严格互斥。
  - AI 叙事只展示 contract 中的结构化结论翻译。
  - 已新增前端 contract adapter：`normalizeRadarContract()`，保持视觉不变，仅切换数据来源。

- [ ] Coach/Event Log 落地。
  - 新增 coach event 写入路径。
  - 每次策略触发、提醒发送、用户操作都记录事件。
  - 行为报告从事件和交易结果中复盘，不从临时 UI 状态推断。

## Phase D：数据库与持仓假设

- [ ] 新增 `positions.entry_thesis_json`。
  - 保存入场战法。
  - 保存入场级别。
  - 保存入场中枢 ZG/ZD。
  - 保存原始止损。
  - 保存初始目标。
  - 保存入场触发条件。

- [ ] 买入记录写入持仓时生成入场假设。
  - 手动交易、语音交易、CSV 导入都要有合理降级。
  - 缺失结构信息时标记 `strategy_type = 未知`，不要伪造。

- [ ] 持仓模式只使用入场假设做管理。
  - 不再每天重新判断“现在能不能买”。
  - 不展示入场 checklist。
  - 只展示结构是否有效、什么时候减、什么时候出。

- [ ] 增加迁移测试。
  - 新库 schema 包含 `entry_thesis_json`。
  - 老库迁移后包含 `entry_thesis_json`。
  - 旧持仓缺失该字段时 API 不崩。

## Phase E：推送闭环

- [ ] 新增 `server/engines/decision/push_rules.py`。
  - 结构失效推送。
  - 台阶止损触发推送。
  - 30分转折顶背驰推送。
  - 日线顶背驰推送。
  - 扫描器重点候选推送。

- [ ] 增加推送去重策略。
  - 同一 symbol、同一规则、同一结构节点不重复推。
  - 明确冷却时间。
  - 记录触发历史。

- [ ] 接入 worker。
  - 持仓类推送接入 `price_monitor` 或独立 worker。
  - 扫描候选推送接入 scanner worker。

- [ ] 推送文案规范。
  - 必须包含触发条件。
  - 必须包含风险线。
  - 必须包含“仅供参考”。
  - 不写“立即买入/立即卖出”等交易机器人语气。

## Phase F：QMT Execution 预留

- [ ] 新增 `docs/QMT_EXECUTION_ARCHITECTURE.md`。
  - 定义 Execution Layer、Risk Gate、QMT Adapter、Audit Log 的职责边界。
  - 明确 Phase 3 才允许执行交易。

- [ ] 定义有底仓日内 T 的前置条件。
  - 用户显式授权 symbol。
  - 系统确认底仓数量。
  - 配置最大单笔金额、最大日内交易次数、最大日内亏损。
  - 配置可交易时间段和禁止交易时段。

- [ ] 定义 dry-run 模式。
  - 所有策略先跑模拟 intent。
  - dry-run 记录虚拟下单、虚拟成交、风险检查结果。
  - dry-run 通过前不得连接实盘 QMT。

- [ ] 定义 kill switch。
  - 支持全局停止自动交易。
  - 支持单 symbol 停止。
  - 支持连续失败/滑点异常/行情 stale 自动停止。

- [ ] 定义 QMT Adapter 边界。
  - Adapter 只负责账户查询、持仓查询、下单、撤单、成交回报。
  - Adapter 不做策略判断。
  - Adapter 不读取 `chan.py` 结构。

- [ ] 定义 Execution Audit Log。
  - 记录 intent、risk checks、order request、order response、fill、cancel、error。
  - 每条记录包含 strategy_id、strategy_version、data_source、price_source。

## 第一批开工任务

1. [x] 写 `PRODUCT_ROADMAP.md`，锁定三阶段目标、边界和验收标准。
2. [x] 写 `docs/ARCHITECTURE_FOUNDATION_DECISIONS.md`，锁定底层不可逆决定。
3. [x] 写 `docs/DATA_SOURCE_CONTRACT.md`，锁定数据源权威口径。
4. [x] 写 `docs/STRATEGY_CONTRACT.md`，定义策略如何表达和输出 plans / alerts。
5. [x] 写 `docs/EXECUTION_INTENT_CONTRACT.md`，为 Phase 3 QMT 执行预留隔离接口。
6. [x] 盘点现有基础结构算法来源，列出哪些来自 `chan.py`，哪些是 CT-OS 自研重复实现。
7. [x] 写 `docs/RADAR_API_CONTRACT.md`，明确 `structure` 全部来自 `chan.py adapter`，并暴露 `data_source`。
8. [x] 新增 `server/api/radar.py` 兼容壳。
9. [x] 给 `/api/chan/matrix/v2` 补 characterization tests。
10. [x] 给 `/api/radar/{symbol}` 补 contract tests。
11. [x] 更新 `ARCHITECTURE_REPLAN.md` 的当前状态，标记已完成和仍待做的事项。

## 当前已观察到的状态

- `server/api/scanner.py` 已存在。
- `web/src/pages/Scanner.jsx` 已存在。
- `web/src/components/ScanCard.jsx` 已存在。
- `scan_results.fundamental` 已在 schema 和 migration 中出现。
- `server/api/rotation.py` 当前已使用 `watchlist_groups/watchlist_items`，不是旧 `watchlist` 表。
- `server/services/chan_service.py` 仍是最大重构风险点，应先测试保护再拆。
- 当前已有三类数据源：TDX 日线湖、BaoStock 多级别缓存、腾讯实时行情。下一步要先锁定 contract，避免同一功能混用口径。
- 完整产品目标已分为 Phase 1 缠论分析工作台、Phase 2 策略教练与提醒、Phase 3 QMT 有底仓日内 T 执行。
