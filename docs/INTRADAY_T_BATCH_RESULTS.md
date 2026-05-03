# 日内 T 批量实验记录

> 仅供参考，不构成投资建议。

## 2026-04-29 sample6-smoke

### 目标

验证第一版批量回放链路是否能把不同股票的 NO_TRADE 原因拆开，而不是只看到模糊的不开仓。

### 样本池

文件：`data/intraday_t_sample_pool.txt`

- `sh.603893` 瑞芯微
- `sh.603986` 兆易创新
- `sz.300124` 汇川技术
- `sz.002176` 江特电机
- `sz.300724` 捷佳伟创
- `sz.300738` 奥飞数据

这些股票当前均已导入本地 TDX 1m 到 qmt lake。

### 命令

```bash
./venv/bin/python server/scripts/paper_replay_batch.py \
  --symbol sz.300724 sh.603893 sh.603986 sz.300124 sz.002176 sz.300738 \
  --window '2026-04-24 13:30:00' '2026-04-24 13:36:00' \
  --kline-source qmt --adjustflag 3 \
  --detail-source tdx_1m_replay \
  --auto-parent-context --parent-max-cycles 1 \
  --limit 60 \
  --run-label sample6-smoke \
  --report-limit 1000
```

### 结果摘要

| symbol | steps | fills | closed | pnl | 主要 blockers |
|---|---:|---:|---:|---:|---|
| `sh.603893` | 6 | 0 | 0 | 0.00 | `fresh_event=4`, `sell_first_trigger=2` |
| `sh.603986` | 6 | 0 | 0 | 0.00 | `fresh_event=6` |
| `sz.002176` | 6 | 0 | 0 | 0.00 | - |
| `sz.300124` | 6 | 0 | 0 | 0.00 | `fresh_event=6` |
| `sz.300724` | 6 | 0 | 0 | 0.00 | `sell_first_position_quality=6`, `sell_first_trigger=1` |
| `sz.300738` | 6 | 0 | 0 | 0.00 | `fresh_event=6` |

全局分布：

- `decisions`: `NO_TRADE=36`
- `reasons`: `event_not_fresh=28`, `no_baseline_t_trigger=8`
- `blockers`: `fresh_event=22`, `sell_first_position_quality=6`, `sell_first_trigger=3`
- `paths`: `NO_EDGE=24`, `DOWNWARD_DEFENSE=6`, `HIGH_VOLATILITY_OSCILLATION=4`, `UPWARD_MAJOR_WAVE=2`

### 初步结论

1. 第一版规则明显偏保守，短窗口内没有任何开仓。
2. 最大阻塞项是 `fresh_event`，说明信号新鲜度阈值很可能是调参第一目标。
3. `sz.300724` 的阻塞很集中：不是没有顶背驰，而是 `sell_first_position_quality` 不达标，位置距离中枢上沿太远。
4. `sz.002176` 有 6 个 NO_TRADE 但没有 signal blockers，说明还存在更上层的 `NO_EDGE` / 无可解释事件场景，后续报告需要补“无事件/无路径”的 blocker。

### 性能观察

尝试跑 6 票 x 2 天全日窗口时超过 90 秒未完成；6 票 x 2 天 13:30-14:10 窗口也超过 2 分钟未完成。当前批量内循环需要先使用短窗口 smoke，后续要做特征缓存预热或增量结构计算，否则不适合实时 dry-run。

## 2026-04-29 profile-strict/balanced/loose-1340

### 目标

验证 `strict / balanced / loose` 三档参数对开仓数量和 blocker 分布的影响。

### 参数档位

| profile | fresh bars | min divergence | sell-first ZG ATR | buy-first ZD ATR | timeout |
|---|---:|---:|---:|---:|---:|
| `strict` | 5 | 0.50 | -0.25 | 0.25 | 30 |
| `balanced` | 8 | 0.45 | -0.50 | 0.50 | 30 |
| `loose` | 12 | 0.40 | -0.80 | 0.80 | 45 |

### 命令

```bash
for p in strict balanced loose; do
  ./venv/bin/python server/scripts/paper_replay_batch.py \
    --symbol sh.603893 sz.300724 \
    --window '2026-04-24 13:30:00' '2026-04-24 13:40:00' \
    --kline-source qmt --adjustflag 3 \
    --detail-source tdx_1m_replay \
    --auto-parent-context --parent-max-cycles 1 \
    --limit 80 \
    --strategy-profile "$p" \
    --run-label "profile-$p-1340" \
    --report-limit 1000
done
```

### 结果摘要

| profile | decisions | fills | closed | `event_not_fresh` | `fresh_event` blocker | `sell_first_position_quality` blocker |
|---|---:|---:|---:|---:|---:|---:|
| `strict` | 20 | 1 | 0 | 7 | 7 | 8 |
| `balanced` | 20 | 1 | 0 | 2 | 2 | 10 |
| `loose` | 20 | 1 | 0 | 2 | 2 | 10 |

分票观察：

- `sh.603893` 三档都会在窗口内触发一次卖出第一腿，尚未在 13:40 前闭环。
- `sz.300724` 三档均不开仓，主要卡在 `sell_first_position_quality`。

### 初步结论

1. 放松 freshness 能显著减少 `event_not_fresh`，但没有在当前窗口增加有效开仓。
2. `balanced` 与 `loose` 在这个样本上结果一致，说明继续放松 freshness/背驰强度不是主矛盾。
3. 下一轮调参重点应从“是否新鲜”转向“位置质量是否应该按路径动态阈值”，尤其是 `DOWNWARD_DEFENSE` 下的卖第一腿是否必须更严格。
4. 瑞芯微第一腿能打开但 10 分钟窗口没有闭环，后续需要单独统计 `open_risk_duration` 和 `second_leg_timeout`。

## 2026-04-29 open-risk-metrics-smoke

### 目标

把“第一腿已打开但第二腿未闭环”的风险暴露从 pnl 里拆出来，避免把未完成 T 错读为已经完成的策略亏损。

### 命令

```bash
./venv/bin/python server/scripts/paper_replay_batch.py \
  --symbol sh.603893 sz.300724 \
  --window '2026-04-24 13:30:00' '2026-04-24 13:40:00' \
  --kline-source qmt --adjustflag 3 \
  --detail-source tdx_1m_replay \
  --auto-parent-context --parent-max-cycles 1 \
  --limit 80 \
  --strategy-profile strict \
  --run-label open-risk-metrics-smoke \
  --report-limit 1000
```

### 结果摘要

| symbol | fills | closed | open | maxRisk | pnl | 主要 blockers |
|---|---:|---:|---:|---:|---:|---|
| `sh.603893` | 1 | 0 | 1 | 1 | -42.4393 | `fresh_event=5`, `sell_first_trigger=2` |
| `sz.300724` | 0 | 0 | 0 | 0 | 0.0000 | `sell_first_position_quality=8`, `fresh_event=2`, `sell_first_trigger=1` |

### 初步结论

1. 瑞芯微在该窗口不是完整失败，而是“第一腿已成交，第二腿尚未闭环”：`open=1 maxRisk=1`。
2. 后续评价策略时必须同时看 `closed_t_count` 和 `open_t_count`，不能只看短窗口 pnl。
3. 下一步需要扩展窗口或专门做二腿回补实验，看第一腿后多久出现底背驰回补，以及超时强制闭环是否合理。

## 2026-04-29 rockchip-second-leg

### 目标

验证瑞芯微第一腿卖出后，第二腿是正常底背驰买回，还是只能依赖 timeout 强制闭环。

### 命令

```bash
./venv/bin/python server/scripts/paper_replay_batch.py \
  --symbol sh.603893 \
  --window '2026-04-24 13:30:00' '2026-04-24 13:50:00' \
  --kline-source qmt --adjustflag 3 \
  --detail-source tdx_1m_replay \
  --auto-parent-context --parent-max-cycles 1 \
  --limit 120 \
  --strategy-profile strict \
  --run-label rockchip-second-leg-1350 \
  --report-limit 1000

./venv/bin/python server/scripts/paper_replay_batch.py \
  --symbol sh.603893 \
  --window '2026-04-24 13:30:00' '2026-04-24 14:10:00' \
  --kline-source qmt --adjustflag 3 \
  --detail-source tdx_1m_replay \
  --auto-parent-context --parent-max-cycles 1 \
  --limit 120 \
  --strategy-profile strict \
  --run-label rockchip-second-leg-1410 \
  --report-limit 1000
```

### 结果摘要

| window | steps | fills | closed | open | closure | maxRisk | second leg |
|---|---:|---:|---:|---:|---:|---:|---|
| 13:30-13:50 | 20 | 1 | 0 | 1 | 0.00% | 11 | 等待中 |
| 13:30-14:10 | 40 | 2 | 1 | 0 | 100.00% | 12 | 正常底背驰买回 |

verbose 关键节点：

- `13:37` 出现 `SELL_THEN_BUY_BACK top_divergence_sell_first`
- `13:38` 第一腿 `SELL` 成交，成交价约 `178.8805`
- `13:51` 出现 `BUY_THEN_SELL_BACK buyback_triggered`
- `13:52` 第二腿 `BUY` 成交，成交价约 `178.7693`
- 最大二腿暴露：`12` 根 1m K

### 初步结论

1. 瑞芯微这笔 T 是正常二腿闭环，不是 timeout 闭环。
2. 第一腿到第二腿大约等待 13 根决策 bar，当前 `buyback_timeout_bars=30` 没有误伤这笔交易。
3. 这说明二腿逻辑至少能识别“顶背驰先卖、底背驰买回”的完整一轮。
4. 当前回测 pnl 为负主要来自费用/滑点模型和成交价差较小，后续应单独拆出 gross pnl、fees、slippage，避免把执行成本和结构判断混在一起。

## 2026-04-29 rockchip-cost-breakdown

### 目标

拆开瑞芯微完整一轮 T 的理论价差、滑点、费用和闭环净收益。

### 命令

```bash
./venv/bin/python server/scripts/paper_replay_batch.py \
  --symbol sh.603893 \
  --window '2026-04-24 13:30:00' '2026-04-24 14:10:00' \
  --kline-source qmt --adjustflag 3 \
  --detail-source tdx_1m_replay \
  --auto-parent-context --parent-max-cycles 1 \
  --limit 120 \
  --strategy-profile strict \
  --run-label rockchip-cost-breakdown \
  --report-limit 1000
```

### 结果摘要

| symbol | closed | gross | fees | slippage | netT | ledger pnl |
|---|---:|---:|---:|---:|---:|---:|
| `sh.603893` | 1 | 29.00 | 20.0312 | 17.88 | -8.9112 | -42.4393 |

### 初步结论

1. 这笔结构上完成了闭环，但理论价差只有约 `29` 元。
2. 费用约 `20.03` 元，滑点成本约 `17.88` 元，二者合计已经超过理论价差。
3. 闭环 T 口径 `netT=-8.9112`，比账本口径 `ledger pnl=-42.4393` 更适合评价这一轮 T 的执行质量。
4. 后续优化方向不是继续放松信号，而是增加“最小预期价差/成本覆盖”过滤：没有足够空间覆盖费用和滑点时，不开第一腿。

## 2026-04-29 min-second-leg-bars

### 目标

验证“操作时间间隔太短”的处理方式：第一腿成交后，要求等待至少 N 根 1m K 才允许正常二腿回补。

### 命令

```bash
for gap in 0 20; do
  ./venv/bin/python server/scripts/paper_replay_batch.py \
    --symbol sh.603893 \
    --window '2026-04-24 13:30:00' '2026-04-24 14:10:00' \
    --kline-source qmt --adjustflag 3 \
    --detail-source tdx_1m_replay \
    --auto-parent-context --parent-max-cycles 1 \
    --limit 120 \
    --strategy-profile strict \
    --min-second-leg-bars "$gap" \
    --run-label "min-gap-$gap" \
    --report-limit 1000
done
```

### 结果摘要

| min second leg bars | fills | closed | open | maxRisk | gross | fees | slippage | netT |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 2 | 1 | 0 | 12 | 29.00 | 20.0312 | 17.88 | -8.9112 |
| 20 | 2 | 1 | 0 | 19 | 23.00 | 20.0330 | 17.89 | -14.9230 |

verbose 关键节点：

- `min_second_leg_bars=0`: `13:51` 买回信号，`13:52` 成交。
- `min_second_leg_bars=20`: `13:51` 到 `13:57` 的买回被等待间隔过滤，`13:58` 才买回，`13:59` 成交。

### 初步结论

1. 单纯拉长二腿等待时间没有改善这笔交易，反而让买回价格变差。
2. “操作时间间隔太短”的本质不是时间本身，而是价差空间不足以覆盖费用与滑点。
3. 后续应保留 `--min-second-leg-bars` 作为实验参数，但核心过滤应转向 `expected_edge_after_cost > 0`。

## 2026-04-29 buy-sell-logic-audit

### 触发问题

用户质疑：当前买卖点逻辑是否真的没有问题。

### 审计发现

发现一个真实逻辑缺口：

- 第一腿开仓事件要求 `fresh_event`。
- 但正常二腿回补事件此前只要求 `buy_first_trigger` / `sell_first_trigger`，没有显式要求 `fresh_event`。
- 这意味着第一腿之后，理论上可能用残留的旧底背驰/顶背驰信号触发二腿回补。

### 修复

正常二腿事件现在增加 `fresh_event`：

- 卖出第一腿后的买回：`waiting_second_leg + first_leg_sell + second_leg_interval_ok + fresh_event + buy_first_trigger`
- 买入第一腿后的卖回：`waiting_second_leg + first_leg_buy + second_leg_interval_ok + fresh_event + sell_first_trigger`

timeout 强制闭环不受 `fresh_event` 限制，因为它是风险保护。

### 回归验证

新增测试：

- `test_second_leg_requires_fresh_event_before_normal_buyback`
- `test_replay_does_not_close_second_leg_on_stale_buyback_signal`

瑞芯微 13:30-14:10 样本在修复前后结果不变：

- `fills=2`
- `closed=1`
- `open=0`
- `maxRisk=12`
- `netT=-8.9112`

说明这笔 13:51 的买回本来就是新鲜 B1 信号，不是旧信号误触发。

### 仍需继续验证

1. 买卖点逻辑目前只能说“更严谨了”，不能说“完全正确”。
2. 下一步必须做原始缠论事件对齐：把每次 `position_event` 对应到当时 1m 图上的分型/笔/中枢/背驰。
3. 第一腿还缺 `expected_edge_after_cost` 过滤，避免结构正确但价差不够。
4. 父级方向目前来自 L0 `last_bi_dir`，属于简化推断，还需要和人工判图样本对照。

## 2026-04-29 paper-event-audit

### 目标

把纸面回放里的买卖事件映射回本地 1m K 线，审计信号 bar、下一根成交 bar 和 fill 记录是否一致。

### 命令

```bash
./venv/bin/python server/scripts/paper_event_audit.py \
  --run-id paper_run_1_sh603893_2026-04-24_13_30_00_2026-04-24_14_10_00_sample6-d2-pm_w1 \
  --symbol sh.603893 \
  --before 4 --after 4 \
  --kline-source qmt --adjustflag 3
```

### 审计结果

| event time | event | decision | event code | divergence | fill time | fill side | fill price | flags |
|---|---|---|---|---|---|---|---:|---|
| `2026-04-24 13:37:00` | 开第一腿卖出#顶背驰 | `SELL_THEN_BUY_BACK` | `S2S` | `top/0.86` | `13:38:00` | `SELL` | `178.8805` | `ok` |
| `2026-04-24 13:51:00` | 第二腿买回#底背驰 | `BUY_THEN_SELL_BACK` | `B1` | `bottom/0.736` | `13:52:00` | `BUY` | `178.7693` | `ok` |

1m K 线观察：

- `13:37` 信号 bar 收在 `179.0000`，下一根 `13:38` 开盘 `178.9700`，模拟卖出价 `178.8805`，成交对齐下一根 K。
- `13:51` 信号 bar 收在 `178.7000`，下一根 `13:52` 开盘 `178.6800`，模拟买回价 `178.7693`，成交对齐下一根 K。
- 买回后 `13:53-13:55` 仍继续下跌到 `178.2900`，说明“底背驰出现就立即买回”在这个样本上偏敏感。

### 新发现

审计时发现一处持久化问题：`paper_fills.fill_id` 和 `paper_intents.intent_id/idempotency_key` 原先是回放内局部 ID，多次保存不同 run 时会发生主键/唯一键冲突，后续 run 的 fill 会被 `INSERT OR IGNORE` 静默丢弃。

修复方式：

- 持久化到 SQLite 时，将 `intent_id`、`linked_intent_id`、`idempotency_key`、`fill_id` 加上 `run_id` 作用域。
- `paper_decisions.intent_id` 同步保存 run-scoped ID，保证可以和 `paper_fills` 稳定 join。

新增测试：

- `test_save_replay_result_scopes_intents_and_fills_per_run`
- `test_build_event_audit_marks_signal_and_fill_bars`
- `test_build_event_audit_flags_stale_event_and_missing_fill`

### 初步结论

1. 审计工具证明瑞芯微这条旧 run 的两次动作不是数据库错位：信号 bar、成交 bar、fill 均能对齐。
2. 买卖点方向在这笔样本里符合“顶背驰先卖、底背驰买回”的递归思路。
3. 但交易效果仍不理想：买回后继续下跌，且费用/滑点吞掉价差，说明策略缺少“二腿确认”和“成本覆盖”过滤。
4. 后续不应继续只放松背驰阈值，而应新增 `expected_edge_after_cost` 与 `second_leg_confirmation` 两类因子。

## 2026-04-29 edge-and-confirmation-gates

### 工程审查结论

不新建策略服务，不引入外部量化库。直接扩展现有 CZSC-style signal/event 层：

```text
IntradayTFeatures
  ├─ position_to_center.zg/zd/distance
  ├─ latest_event.bars_since_event
  └─ divergence.direction
        │
        ▼
build_intraday_t_signals()
  ├─ expected_edge_after_cost       -> 第一腿是否值得做
  └─ second_leg_confirmation_ok     -> 二腿是否不要立刻回补
        │
        ▼
IntradayTPositionPlan events
  ├─ first leg requires expected_edge_after_cost
  └─ normal second leg requires second_leg_confirmation_ok
```

### 新参数

| 参数 | 默认值 | 作用 |
|---|---:|---|
| `min_expected_edge_after_cost` | `0.0` | 默认关闭。大于 0 时，第一腿必须满足预期净价差 >= 阈值。 |
| `expected_edge_atr_multiple` | `2.0` | 成本覆盖使用 `min(结构空间, ATR * 倍数)` 做保守空间估算。 |
| `second_leg_confirmation_bars` | `0` | 默认关闭。大于 0 时，正常二腿必须等待背驰事件存活 N 根 bar。 |

CLI 已支持：

```bash
--min-expected-edge-after-cost 20 \
--expected-edge-atr-multiple 2 \
--second-leg-confirmation-bars 2
```

### 实现细节

`expected_edge_after_cost` 估算：

- 卖第一腿：用当前价到结构 `ZD` 作为预期回补空间。
- 买第一腿：用当前价到结构 `ZG` 作为预期卖回空间。
- 为避免过度乐观，最终空间取 `min(结构空间, ATR * expected_edge_atr_multiple)`。
- 成本估算包含两腿佣金、卖出印花税、过户费、双边滑点。
- 默认关闭，避免历史实验结果突然变化；批测时显式打开。

`second_leg_confirmation_ok` 估算：

- 正常二腿需要 `bars_since_event >= second_leg_confirmation_bars`。
- timeout 强制闭环不受影响，仍然优先保护敞口风险。
- 这是第一版“轻确认”，不是最终形态。后续更强版本应接入价格重新站回/跌破确认位。

### 新增测试

- `test_first_leg_requires_expected_edge_after_cost_when_enabled`
- `test_first_leg_expected_edge_gate_is_disabled_by_default`
- `test_second_leg_requires_confirmation_bars_before_normal_buyback`

### 初步结论

1. 这次改动把“能不能做”和“要不要立刻回补”从背驰信号里拆出来，策略解释性更好。
2. 默认参数不改变现有回放行为，适合先做 A/B 批测。
3. 下一步建议用瑞芯微窗口跑三组：baseline、`min_expected_edge_after_cost=20`、`second_leg_confirmation_bars=2`，比较 fill 数、闭环率、maxRisk、netT。

## 2026-04-29 rockchip-edge-ab

### 目标

验证成本覆盖和二腿确认在瑞芯微 `2026-04-24 13:30-14:10` 这段是否能改善日内 T。

### 结果摘要

| variant | 参数 | fills | closed | open | maxRisk | netT | 结论 |
|---|---|---:|---:|---:|---:|---:|---|
| baseline | 默认关闭 | 3 | 1 | 1 | 12 | -8.9112 | 完成一轮后又开第二轮，窗口内留 open risk。 |
| edge-20 old | 结构空间估算 | 3 | 1 | 1 | 12 | -8.9112 | 旧估算太乐观，没有过滤掉亏损交易。 |
| edge20+confirm2 old | 结构空间估算 + 二腿确认 2 | 3 | 1 | 1 | 19 | -14.9230 | 买回从 `13:51` 延后到 `13:58`，价格更差。 |
| edge-20 atr-cap | ATR cap 后 `min_expected_edge_after_cost=20` | 0 | 0 | 0 | 0 | 0.0000 | 全部挡掉，主要 blocker 为 `expected_edge_after_cost=32`。 |
| edge-5 atr-cap | ATR cap 后 `min_expected_edge_after_cost=5` | 0 | 0 | 0 | 0 | 0.0000 | 仍全部挡掉，说明该窗口日内波动空间不足以覆盖成本。 |

关键 evidence：

- `13:37` 卖出触发为 true，但 ATR cap 后 `expected_edge_after_cost=false`。
- 当时估算：`gross_edge=23.4`，`estimated_cost=37.6969`，`net_edge=-14.2969`。
- 因此这笔不该做。此前亏损不是背驰方向完全错，而是可吃空间小于交易成本。

### 修正

成本覆盖从“完整结构空间”改成“结构空间和短线 ATR 空间取小”：

```text
structure_edge = abs(current - target_boundary) * qty
atr_edge       = volatility.atr * expected_edge_atr_multiple * qty
gross_edge     = min(structure_edge, atr_edge)
net_edge       = gross_edge - estimated_round_trip_cost
```

### 结论

1. 对瑞芯微这段，正确策略更可能是“不做”，而不是调二腿。
2. 二腿确认不能用简单等待解决，等待会增加 open risk，还可能错过更好的回补价。
3. 下一步应在更多票上跑 `edge-5/10/20` 分层，找出“低波动高成本股票不做”的稳定边界。

## 2026-04-29 sample6-fast-edge-sweep

### 触发问题

第一次跑 6 票 x 4 档 edge 分层时，批量任务卡住。根因不是 replay 状态机慢，而是每个未缓存的 as_of 都会调用 `chan_detail_service`，服务内部强制用 `5000` 根 K 线跑 Chan.py。

实测：

| 场景 | 单个 as_of 耗时 |
|---|---:|
| 修复前，强制 5000 根 | 约 `3.7s` |
| 修复后，replay 默认 500 根 | 约 `0.24s` |
| 修复后，replay 180 根 | 约 `0.06s` |

修复：

- `get_chan_detail()` 增加可选 `max_compute_bars`。
- 普通页面/接口默认仍保留 5000 根，不影响展示质量。
- `paper_replay_source` 在 replay 构造特征时传 `max_compute_bars=count`。
- SQLite feature cache 读回时补上 `parent_context`，避免缓存命中后丢失父级方向/周期预算。

### 命令

```bash
for edge in 0 5 10 20; do
  ./venv/bin/python server/scripts/paper_replay_batch.py \
    --symbols-file data/intraday_t_sample_pool.txt \
    --window '2026-04-24 13:30:00' '2026-04-24 14:10:00' \
    --kline-source qmt --adjustflag 3 \
    --detail-source tdx_1m_replay \
    --strategy-profile strict \
    --min-expected-edge-after-cost "$edge" \
    --expected-edge-atr-multiple 2 \
    --run-label "sample6-fast-edge-$edge" \
    --report-limit 2000
done
```

`edge=0` 为默认关闭成本过滤，所以实际命令不传 `--min-expected-edge-after-cost`。

### 结果摘要

| edge | symbols | fills | closed | open | maxRisk | netT | 主要变化 |
|---:|---:|---:|---:|---:|---:|---:|---|
| 0 | 6 | 3 | 1 | 1 | 12 | -8.9112 | 只有瑞芯微交易，完成一轮后又开第二轮。 |
| 5 | 6 | 0 | 0 | 0 | 0 | 0.0000 | 瑞芯微和捷佳伟创主要被 `expected_edge_after_cost` 过滤。 |
| 10 | 6 | 0 | 0 | 0 | 0 | 0.0000 | 与 edge=5 相同。 |
| 20 | 6 | 0 | 0 | 0 | 0 | 0.0000 | 与 edge=5 相同。 |

分票观察：

- `sh.603893`：baseline 触发 3 次 fill，T 口径 `netT=-8.9112`；edge>=5 后 `fills=0`，`expected_edge_after_cost=32`。
- `sh.603986`：baseline 已经不开仓，主要是 `fresh_event` 和 `NO_EDGE`。
- `sz.002176`：全部 `NO_EDGE`，不进入成本判断。
- `sz.300124`：主要卡在 `fresh_event`、路径和位置质量。
- `sz.300724`：`sell_first_position_quality=28`，同时 edge>=5 后 `expected_edge_after_cost=28`。
- `sz.300738`：主要卡在 `fresh_event`。

### 结论

1. 在这段样本里，成本过滤不是“错过盈利机会”，而是过滤掉唯一一笔已知亏损交易。
2. `edge=5` 已经足够挡住低波动高成本场景，`edge=10/20` 在当前样本上没有额外差异。
3. 现在瓶颈从策略判断转向样本覆盖：需要更多日期、更多波动环境，才能判断 `edge=5` 是合理默认还是过度保守。
4. 性能修复后，6 票 x 40 bar 可以稳定跑完，后续可以扩大到多日期批测。

## 2026-04-29 sample6-2d-edge

### 目标

把 `edge=5` 放到两天样本里验证，避免只对 `2026-04-24` 瑞芯微单日过拟合。

### 样本

- 股票：`data/intraday_t_sample_pool.txt` 6 票
- 日期窗口：
  - `2026-04-24 13:30:00` 到 `2026-04-24 14:10:00`
  - `2026-04-27 13:30:00` 到 `2026-04-27 14:10:00`

### 结果摘要

| variant | runs | decisions | fills | closed | open | maxRisk | netT | 主要结论 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| baseline | 12 | 480 | 3 | 1 | 1 | 12 | -8.9112 | 只有 `2026-04-24` 瑞芯微交易，且净亏。 |
| edge=5 | 12 | 480 | 0 | 0 | 0 | 0 | 0.0000 | 两日全部不开仓，过滤掉唯一亏损交易。 |

baseline blocker：

- `fresh_event=295`
- `sell_first_position_quality=59`
- `no_actionable_event=52`
- `parent_allows_sell_first=43`
- `parent_allows_buy_first=37`

edge=5 blocker：

- `fresh_event=295`
- `expected_edge_after_cost=126`
- `sell_first_position_quality=59`
- `no_actionable_event=52`
- `parent_allows_sell_first=43`

分票观察：

- `sh.603893`：baseline 两日合计 `fills=3 closed=1 open=1 netT=-8.9112`；edge=5 后 `fills=0`。
- `sz.300724`：edge=5 后 `expected_edge_after_cost=54`，说明即使位置质量放松，也会被成本空间挡住。
- 其它票主要仍由 freshness、NO_EDGE、路径/父级方向过滤。

### 结论

1. `edge=5` 在两日样本里没有错过已知盈利交易，因为 baseline 本身没有盈利交易。
2. 当前样本说明：成本过滤是必要闸门，至少能避免低波动时间段的无效 T。
3. 仍不能把 `edge=5` 定为正式默认值。还需要覆盖更高波动日期，确认它不会过滤真正有空间的 T。
4. 下一轮建议跑 4 票 x 4 日期，先排除只有 4/24 和 4/27 数据的票，扩大日期维度。

## 2026-04-29 sample4-4d-edge-window

### 目标

把样本扩到 4 票 x 4 日期，并拆分两个风险闸门的贡献：

- `expected_edge_after_cost`：解决“价差不足以覆盖费用/滑点”的问题。
- `min_bars_before_window_end_for_first_leg`：解决“窗口尾部开第一腿，来不及闭环”的问题。

### 样本

- 股票：`sh.603893`、`sh.603986`、`sz.300124`、`sz.002176`
- 日期窗口：
  - `2026-04-24 13:30:00` 到 `2026-04-24 14:10:00`
  - `2026-04-27 13:30:00` 到 `2026-04-27 14:10:00`
  - `2026-04-28 13:30:00` 到 `2026-04-28 14:10:00`
  - `2026-04-29 13:30:00` 到 `2026-04-29 14:10:00`

备注：`2026-04-28` 本地 qmt lake 在该窗口返回 `0` 根 bar，本轮不解读这一天。

### 结果摘要

| variant | 参数 | decisions | fills | closed | open | maxRisk | netT | 主要结论 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| baseline | 默认 | 480 | 7 | 3 | 1 | 30 | -232.4999 | 有闭环亏损，也有尾部 open risk。 |
| window12 | `min_bars_before_window_end_for_first_leg=12` | 480 | 6 | 3 | 0 | 30 | -232.4999 | 挡掉尾部 open risk，但闭环亏损仍在。 |
| edge5 | `min_expected_edge_after_cost=5` | 480 | 1 | 0 | 1 | 8 | 0.0000 | 挡掉闭环亏损，但仍留下 14:00 第一腿 open risk。 |
| edge5+window12 | `edge=5 + window12` | 480 | 0 | 0 | 0 | 0 | 0.0000 | 两类风险都被挡掉。 |

### 关键审计

`edge5` 唯一留下的 open risk 来自瑞芯微 `2026-04-29 14:00:00`：

- 事件：开第一腿卖出#顶背驰
- event code：`S1P`
- divergence：`top/0.572`
- path：`HIGH_VOLATILITY_OSCILLATION`
- fill：`14:01` 卖出，价格约 `180.7796`
- 后续 K 线：`14:02` 收 `180.96`、`14:03` 收 `181.15`、`14:04` 收 `181.21`

这说明顶背驰后直接卖第一腿，在窗口尾部会留下明显悬空风险；价格后续继续上行时，短窗口内没有足够二腿修复时间。

### 新增闸门

新增参数：

```bash
--min-bars-before-window-end-for-first-leg 12
```

实现规则：

- 默认 `0`，不改变旧回放。
- 只阻止“未开第一腿时的新第一腿”。
- 如果已经在等待二腿，正常买回/卖回和 timeout 强制闭环不受影响。
- 被拦截时 reason 为 `insufficient_window_for_first_leg`，evidence 写入 `remaining_bars` 和原始被挡决策。

### 结论

1. `window12` 是风险管理闸门，不是收益闸门：它能消除尾部 open risk，但不能让低价差 T 变好。
2. `edge5` 是交易质量闸门：它能挡住当前样本里的闭环亏损，但如果信号出现在尾部，仍需要窗口闸门配合。
3. `edge5+window12` 当前最稳，但样本仍太小；下一轮应找高波动、有足够空间的日期，验证它是否过度保守。
4. 后续第一腿还需要加入价格确认，例如顶背驰后不再创新高、跌破短线确认位，避免 `2026-04-29 14:00` 这种高波动上冲中提前卖出。

## 2026-04-29 explore-vs-loose-observe

### 目标

验证“放宽日内 T 逻辑”应该放在哪一层：

- `explore`：放宽 freshness、背驰强度和位置质量，但保留 `edge=5` 与 `window12`，允许模拟成交。
- `loose_observe`：进一步放宽信号，只记录候选，不生成 intent，不成交。

### 新 profile

| profile | freshness | divergence | sell ZG ATR | buy ZD ATR | edge | window guard | 行为 |
|---|---:|---:|---:|---:|---:|---:|---|
| `explore` | 12 | 0.40 | -0.80 | 0.80 | 5 | 12 | 可成交，但保留执行闸门。 |
| `loose_observe` | 20 | 0.35 | -1.20 | 1.20 | 0 | 0 | 只观察候选，不生成 intent。 |

### 4 票 x 4 日期结果

| profile | decisions | candidate / trade signals | fills | closed | open | maxRisk | 主要结论 |
|---|---:|---:|---:|---:|---:|---:|---|
| `explore` | 480 | 5 | 0 | 0 | 0 | 0 | 候选被 `edge` 或 `window` 过滤，没有真实成交。 |
| `loose_observe` | 480 | 41 | 0 | 0 | 0 | 0 | 能捞出更多候选结构，但只观察，不进入模拟盘。 |

`explore` blockers：

- `fresh_event=223`
- `expected_edge_after_cost=162`
- `sell_first_trigger=99`
- `parent_allows_sell_first=82`
- `first_leg_path_allowed=78`
- `insufficient_window_for_first_leg=5`

`loose_observe` 观察结果：

- `SELL_THEN_BUY_BACK=41`
- `observe_only_top_divergence_sell_first=41`
- `开第一腿卖出#顶背驰=41`
- `fills=0`，符合 observe-only 预期。

### 结论

1. 机会候选不是没有，`loose_observe` 可以从同一批数据里捞出 41 个候选顶背驰卖第一腿。
2. `explore` 仍然 0 成交，说明真正卡住的是“候选到执行”的质量门，尤其是成本覆盖和窗口尾部保护。
3. 下一步不该继续盲目放开执行，而应对 41 个候选做事后评分：未来 3/5/8 根 K 是否真的回落、最大有利空间、最大不利波动、是否覆盖成本。
4. 如果候选里存在足够多“事后确实可 T”的样本，再把确认因子变成正式执行条件。

## 2026-04-29 loose-observe-candidate-score

### 目标

对 `loose_observe` 捞出的 41 个候选做事后评分：

- 卖第一腿候选：用下一根 1m 开盘作为理论 entry，未来 N 根最低价作为最大有利回补空间，最高价作为最大不利波动。
- 买第一腿候选：方向反过来，用未来最高价作为最大有利卖回空间，最低价作为最大不利波动。
- 成本按 `PaperRiskConfig` 默认费用估算，包含双边佣金、卖出印花税、过户费、滑点。

### 新工具

```bash
./venv/bin/python server/scripts/paper_candidate_score.py \
  --run-label sample4-4d-loose-observe \
  --horizon 3 5 8 \
  --min-net-edge 5 \
  --quantity 100 \
  --kline-source qmt --adjustflag 3
```

### 评分结果

| min net edge | H3 pass | H5 pass | H8 pass | median H8 net | best H8 net |
|---:|---:|---:|---:|---:|---:|
| 5 | 5/41 = 12.20% | 9/41 = 21.95% | 13/41 = 31.71% | -6.3821 | 48.0899 |
| 20 | 2/41 = 4.88% | 4/41 = 9.76% | 8/41 = 19.51% | -6.3821 | 48.0899 |
| 40 | 0/41 = 0.00% | 0/41 = 0.00% | 2/41 = 4.88% | -6.3821 | 48.0899 |

### 关键观察

1. 41 个候选不是都差，未来 8 根 K 有 `13` 个候选能达到 `net_edge >= 5`，但未来 3 根只有 `5` 个。
2. 大部分候选的中位净空间仍为负，说明“看到顶背驰立刻卖第一腿”整体不够。
3. 能过的候选集中在更晚的确认段，例如瑞芯微 `2026-04-24 14:07-14:09`、`2026-04-29 14:05-14:06`。
4. 这支持一个新方向：不要直接放宽执行，而是把 loose 候选转成“候选池”，再等价格确认后进入 `explore` 执行。

### 下一步

候选到执行之间需要新增确认因子：

```text
top divergence candidate
  -> next bars no new high / price falls below confirm level
  -> expected edge after cost remains positive
  -> window has enough bars
  -> first leg intent
```

第一版确认可以先做很朴素的规则：

- 卖第一腿：候选后等待 1-3 根，要求价格没有再创新高，并且当前价低于候选 entry。
- 买第一腿：候选后等待 1-3 根，要求价格没有再创新低，并且当前价高于候选 entry。
- 通过后再跑 `expected_edge_after_cost` 和 `window12`。

## 2026-04-29 stateful-first-leg-confirmation

### 目标

把“候选后确认”从事后评分工具落到 replay 状态机里：

```text
top divergence candidate
  -> 存入 pending first leg
  -> 下一根以后价格低于候选事件价
  -> 再检查成本覆盖和窗口余量
  -> 才允许第一腿 intent
```

### 实现

- `IntradayTFeatures.current_price` 新增当前 1m 收盘价，来自 `trigger_klines[-1].close`。
- `paper_feature_cache` 版本升到 `intraday_t_features:v2`，避免旧缓存缺少 `current_price`。
- `IntradayTState` 增加 pending first-leg 字段，记录候选方向、事件价、事件 key、等待 bar 数。
- `explore` profile 默认开启 `first_leg_confirmation_bars=1`。
- 默认 profile 不变，确认闸门默认关闭。

### 4 票 x 4 日期结果

| variant | 参数 | fills | closed | open | maxRisk | netT | 主要结论 |
|---|---|---:|---:|---:|---:|---:|---|
| `explore-confirm1-v2` | `edge=5 + window12 + confirm1` | 0 | 0 | 0 | 0 | 0.0000 | 仍不成交，最大 blocker 是成本覆盖。 |
| `explore-confirm1-edge0` | `edge=0 + window12 + confirm1` | 3 | 1 | 1 | 37 | -7.9120 | 一放开成本过滤，又出现亏损和 open risk。 |

`explore-confirm1-v2` blocker：

- `fresh_event=223`
- `expected_edge_after_cost=160`
- `first_leg_confirmation_ok=132`
- `sell_first_trigger=99`
- `parent_allows_sell_first=82`

`explore-confirm1-edge0` 交易观察：

- `2026-04-24 sh.603893`：`fills=2 closed=1 maxRisk=11 netT=-7.9120`
- `2026-04-29 sh.603893`：`fills=1 closed=0 open=1 maxRisk=37`

### 结论

1. stateful 确认能把“事件当根”变成“候选后再确认”，工程方向正确。
2. 但本样本里只放宽确认不够，`edge=5` 仍挡住所有真实成交。
3. 如果把 `edge` 降到 `0`，交易会回来，但仍是亏损和 open risk，说明成本覆盖不是多余闸门。
4. 下一步不是取消 `edge`，而是把 `expected_edge_after_cost` 从 ATR cap 静态估算，升级成“候选评分式”的动态空间估算：用确认后最近 N 根的真实振幅/回落速度，判断是否有足够 T 空间。

## 2026-04-29 sample6-2d-confirmation

### 目标

把样本扩到 6 票 x 2 日期，验证 `explore` 和 `loose_observe` 在更多股票上的表现。

### 样本

- 股票：`data/intraday_t_sample_pool.txt` 6 票
- 日期：
  - `2026-04-24 13:30:00` 到 `2026-04-24 14:10:00`
  - `2026-04-27 13:30:00` 到 `2026-04-27 14:10:00`

数据检查：

- 6 票在 `2026-04-24`、`2026-04-27` 均有 41 根 1m bar。
- `2026-04-28` 该窗口全空，未纳入本轮。
- `2026-04-29` 只有前 4 票有数据，未纳入本轮。

### `explore` 结果

| symbols | windows | decisions | fills | closed | open | maxRisk | 主要 blocker |
|---:|---:|---:|---:|---:|---:|---:|---|
| 6 | 2 | 480 | 0 | 0 | 0 | 0 | `fresh_event=258`, `expected_edge_after_cost=154`, `first_leg_confirmation_ok=99` |

分票 blocker：

- `sh.603893`: `expected_edge_after_cost=38`, `first_leg_confirmation_ok=25`
- `sh.603986`: `first_leg_path_allowed=31`, `parent_allows_sell_first=31`
- `sz.002176`: `no_actionable_event=40`, `expected_edge_after_cost=22`
- `sz.300124`: `fresh_event=75`
- `sz.300724`: `expected_edge_after_cost=63`, `sell_first_position_quality=37`
- `sz.300738`: `fresh_event=69`

### `loose_observe` 结果

| symbols | windows | decisions | candidates | fills | 主要候选 |
|---:|---:|---:|---:|---:|---|
| 6 | 2 | 480 | 17 | 0 | 全部来自 `sh.603893 2026-04-24` |

候选评分，`min_net_edge=5`：

| horizon | pass | pass rate | median net | best net |
|---:|---:|---:|---:|---:|
| H3 | 3/17 | 17.65% | -22.9825 | 38.1199 |
| H5 | 7/17 | 41.18% | -6.9462 | 38.1199 |
| H8 | 11/17 | 64.71% | 19.1448 | 48.0899 |

### 结论

1. 多票样本进一步确认：当前可做候选很集中，不是所有票都适合日内 T。
2. `explore` 继续 0 成交，说明安全闸门并未误放交易。
3. `loose_observe` 能捞出瑞芯微候选，且 H8 评分较好，说明“等候选成熟”有价值。
4. 下一步应做“动态空间估算”：不是用固定 ATR cap 判断 edge，而是把 pending 候选经过确认后的真实回落/反弹速度纳入 `expected_edge_after_cost`。
