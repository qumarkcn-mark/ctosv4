# 缠论结构算法来源盘点

> 创建日期：2026-04-24
> 目的：明确 CT-OS V4.0 中缠论基础结构的唯一权威来源，避免 `chan.py` 与自研算法并行造成判断混乱。

## 总结决定

CT-OS 的 K 线包含、分型、笔、线段、中枢、买卖点等缠论基础结构，统一以 `server/vendor/chan_py` 为唯一权威实现。

CT-OS 自研代码只做三类事情：

- Adapter：把数据源 K 线转换成 `chan.py` 输入，并把 `chan.py` 输出序列化为稳定 contract。
- Decision：基于结构结果生成战法、入场计划、持仓计划、提醒和风险提示。
- Presentation：为前端、AI 叙事、雷达视图组织展示字段。

任何自研 K 线包含、分型、笔、线段、中枢识别逻辑，都不能继续作为生产权威判断。

## 现有来源分类

| 位置 | 当前角色 | 后续处置 |
|---|---|---|
| `server/vendor/chan_py` | 第三方 `chan.py` vendor，实现基础结构 | 保留为唯一权威；不直接改源码 |
| `server/services/chan_detail_service.py` | 当前最接近 `chan.py adapter` 的实现，负责调用 `CChan` 并序列化 `bis`、`segs`、`zhongshus`、`bsps` | 保留；后续迁移/收敛到 `server/engines/structure/chan_adapter.py` |
| `server/services/chan_scanner.py` | Scanner 里调用 `chan.py` 做战法一结构判断，同时包含战法二价格摆动规则 | 保留策略能力；把 `chan.py` 调用收敛到 adapter，战法规则归入 Strategy/Decision |
| `chan_engine/parser.py` | 自研 K 线包含、分型、笔、线段构造 | 标记为 legacy/reference；不得作为生产结构权威 |
| `chan_engine/fsm.py` | 自研中枢识别与状态推演 | 标记为 legacy/reference；不得作为生产结构权威 |
| `chan_engine/models.py` | 自研结构模型 | 仅作为 legacy tests/reference；新 contract 使用 domain models |
| `chan_engine/kinematics.py` | 动能、背驰等派生计算 | 可作为参考；生产实现必须基于 `chan.py adapter` 输出 |
| `chan_engine/phantom.py` | 推演沙盘/模拟 K 线几何 | 可保留为训练和推演工具，但不能反向成为结构权威 |
| `server/services/chan_service.py` | 大型过渡服务：混合结构派生、策略判断、计划生成、UI 兼容字段 | 不再承接新功能；后续拆到 Structure Derived Facts、Decision Engine、API Adapter |
| `server/api/chan.py` | 旧 API 兼容层，并包含部分 checklist/holding 规则 | 保留兼容；新规则迁移到 Decision Engine，API 不做权威判断 |
| `web/src/*` Chan/Radar 相关页面 | 消费结构字段并展示 | 只展示后端 contract，不做结构计算 |

## 权威基础结构

以下字段必须能追溯到 `server/vendor/chan_py` 的输出，不能由 CT-OS 自研算法重新计算：

- K 线包含处理后的结构关系
- 分型
- 笔
- 线段
- 笔中枢
- 段中枢
- 买卖点
- 多级别联立结构

第一阶段允许 `server/services/chan_detail_service.py` 继续作为过渡入口，但它的职责必须限定为 adapter/serializer。

## 允许的派生结构事实

这些逻辑可以由 CT-OS 自研，但必须基于 `chan.py adapter` 输出，不能重新识别基础结构：

- 趋势状态：上升、下降、震荡、背驰观察等。
- 结构生命周期：一买、二买、三买、离开段、回抽段、破坏段等产品定义。
- 多级别嵌套关系：日线中枢内的 30 分钟结构、30 分钟中枢内的 5 分钟触发等。
- freshness、data_source、adjustment、level 完整性判断。
- Radar 展示用的结构摘要。

这类逻辑后续归入 `server/engines/structure/`，命名为 derived facts，而不是基础结构 authority。

## 策略与计划逻辑

以下逻辑属于 Strategy/Decision，不属于 Structure：

- 战法一、战法二、持仓阶段管理。
- 空仓入场 checklist。
- 持仓 holding plan。
- 仓位建议、风险提示、目标价、止损线。
- scanner 候选筛选和排序。
- rotation 横向比较。
- push/alert 触发规则。
- Phase 3 execution intent 候选生成。

这类逻辑后续归入 `server/engines/decision/` 和 `docs/STRATEGY_CONTRACT.md`。

## 需要收敛的重复点

### `chan_engine/parser.py` 与 `chan_engine/fsm.py`

这套自研引擎实现了 K 线合并、分型、笔、线段和中枢识别，和 `chan.py` 的基础结构职责重叠。

处理原则：

- 不立即删除，避免影响历史测试、推演沙盘和已有引用。
- 不再作为生产路径的权威结构来源。
- 后续测试从验证自研结构正确性，转为验证 `chan.py adapter` contract 稳定性。

### `server/services/chan_service.py`

该文件当前承担过多职责：结构消费、趋势归类、战法判断、入场计划、持仓管理、风险计算和 UI 兼容字段。

处理原则：

- 短期保留，作为 `/api/chan/matrix/v2` 兼容层。
- 新功能不继续加在这里。
- 拆分前先补 characterization tests，确保旧 API 输出不漂移。
- 基础结构字段只能来自 `get_chan_detail()` / `chan.py adapter`。

### `_detect_fractal_confirmed` 等局部判断

这类函数如果直接从原始 K 线判断分型，需要重新归类：

- 若用于正式结构判断：必须移除或改为消费 `chan.py` 输出。
- 若用于实时预览/风险提示：必须标注为 preview/guard，不得写入权威结构字段。

## 第一阶段落地动作

- 新增 `server/engines/structure/chan_adapter.py`，统一封装 `chan.py` 输入输出。
- `chan_detail_service.py` 逐步降级为兼容壳或直接迁移到 adapter。
- `/api/radar/{symbol}` 的 `structure` 字段全部来自 adapter。
- `/api/chan/matrix/v2` 先保持旧输出，通过 characterization tests 锁住行为。
- `chan_engine` 保留但标记 legacy，不进入新 API contract。
- Scanner 的战法逻辑继续可用，但基础结构必须通过 adapter 获取。

## 禁止事项

- 禁止在 CT-OS 中新增第三套 K 线包含/分型/笔/线段/中枢算法。
- 禁止让 AI 生成或覆盖结构判断。
- 禁止同一次结构分析混用 TDX、BaoStock、腾讯实时数据。
- 禁止把腾讯实时拼出的 K 线写入正式 `chan.py` 结构判断结果。
- 禁止使用前复权结构价作为 Phase 3 委托价。
