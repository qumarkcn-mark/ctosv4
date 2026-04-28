# Radar Historical Slices

> Created: 2026-04-27
> Related: `docs/RADAR_ALGORITHM_WHITEPAPER_V0_1.md`, `docs/RADAR_API_CONTRACT.md`

本文档记录雷达算法的历史切片样本。它不是行情预测，也不是交易建议；它用于验证 `algorithm_v2` 的 A/B/C 完全分类是否能被后续真实走势检验。

核心方法：

```text
as_of 正式结构
  -> radar_algorithm_v2 输出 path / phase / action_bias / A/B/C 边界
  -> 后续行情检查触发了 A、维持 B，还是进入 C
  -> 固化为回归样本
```

所有市场相关描述仅供参考，不构成投资建议。

## 1. 切片字段

| Field | Meaning |
|---|---|
| `symbol` | 股票代码，使用 BaoStock canonical symbol |
| `name` | 股票名称 |
| `as_of` | 正式结构切片时间 |
| `structure_source` | 切片使用的数据源 |
| `path` | 当时雷达主路径 |
| `phase` | 当时雷达阶段 |
| `action_bias` | 当时看盘姿态 |
| `confirm_boundaries` | A 路径触发边界 |
| `maintain_boundaries` | B 路径维持边界 |
| `invalidate_boundaries` | C 路径失效边界 |
| `future_check` | 后续行情检查点 |
| `future_result` | 后续验证结果 |
| `notes` | 人工解释和风险提醒 |

## 2. 结果枚举

| Result | Meaning |
|---|---|
| `A_TRIGGERED` | A 路径明确触发 |
| `A_PARTIAL_TRIGGERED` | A 的第一层触发，但更高确认未完成 |
| `B_MAINTAINED` | 当前路径继续维持 |
| `C_TRIGGERED` | 失效或防守路径触发 |
| `UNVERIFIED` | 后续数据不足，暂不可验证 |

## 3. 当前切片矩阵

| Symbol | Name | As Of | Path | Phase | Bias | Future Check | Future Result |
|---|---|---|---|---|---|---|---|
| `sh.603893` | 瑞芯微 | `2026-04-24 15:00:00` | `PULLBACK_IN_UPTREND` | `MICRO_CONVERSION` | `WAIT_BREAKOUT` | `2026-04-27` 腾讯实时价 `185.49`，最高 `187.99` | `A_TRIGGERED` |
| `sh.603986` | 兆易创新 | `2026-04-24 15:00:00` | `PULLBACK_IN_UPTREND` | `STANDARD` | `WAIT_RECLAIM` | `2026-04-27` 腾讯实时价 `303.42`，最高 `309.58` | `A_PARTIAL_TRIGGERED` |
| `sh.688008` | 澜起科技 | `2026-02-02 -> 2026-03-05` | `HIGH_VOLATILITY_OSCILLATION` | `STANDARD` | `REDUCE_CHASING` | 30 分钟跌破中枢后反抽三卖 | `C_TRIGGERED` |
| `sz.300124` | 汇川技术 | `2026-04-07 -> 2026-04-24` | `DOWNWARD_DEFENSE` | `STANDARD` | `DEFENSIVE` | 30 分钟一买反弹失败后再创新低 | `A_TRIGGERED` |
| `sz.301517` | 陕西华达 | `2026-03-24 -> 2026-04-14` | `UPWARD_MAJOR_WAVE` | `STANDARD` | `HOLD_OR_TRAIL` | 30 分钟一买/类二买后突破下跌中枢并三买确认 | `B_MAINTAINED` |
| `sz.300502` | 新易盛 | `2026-03-31 -> 2026-04-23` | `UPWARD_MAJOR_WAVE` | `STANDARD` | `HOLD_OR_TRAIL` | 大中枢上方小中枢震荡蓄势，随后向上离开走出强势一笔 | `B_MAINTAINED` |
| `sz.301183` | 东田微 | `2026-03-23 -> 2026-04-13` | `CENTER_REBOUND -> UPWARD_MAJOR_WAVE` | `STANDARD` | `WATCH_REBOUND -> HOLD_OR_TRAIL` | 小转大后二买/类二买快速修复，随后三买确认 | `A_TRIGGERED -> B_MAINTAINED` |
| `sz.300118` | 东方日升 | `2026-02-02 -> 2026-02-09` | `PULLBACK_IN_UPTREND` | `STANDARD` | `WAIT_RECLAIM` | 三买后快速冲高，一卖后未过前高形成二卖 | `C_TRIGGERED` |
| `sz.002460` | 赣锋锂业 | `2026-04-15 -> 2026-04-27` | `HIGH_VOLATILITY_OSCILLATION` | `STANDARD` | `REDUCE_CHASING` | 买点后卖压未化解，跌回原中枢后小转大拉回 | `C_TRIGGERED -> A_PARTIAL_TRIGGERED` |

## 4. 切片 001：瑞芯微 MICRO_CONVERSION 向上触发

### 4.1 Snapshot

| Field | Value |
|---|---|
| `symbol` | `sh.603893` |
| `name` | 瑞芯微 |
| `as_of` | `2026-04-24 15:00:00` |
| `structure_source` | BaoStock 前复权正式结构，`adjustflag=2` |
| `freshness.last_bar_at` | `2026-04-24 15:00:00` |

### 4.2 Radar Output At Slice

| Field | Value |
|---|---|
| `path` | `PULLBACK_IN_UPTREND` |
| `phase` | `MICRO_CONVERSION` |
| `action_bias` | `WAIT_BREAKOUT` |
| `risk_level` | `MEDIUM` |
| `summary` | `5分钟多空转换点，5ZG 178.66转强，5ZD 178.29转弱。` |

### 4.3 Boundaries

| Scenario | Boundary | Meaning |
|---|---|---|
| A confirm | `5ZG 178.66 break_above` | 突破 5 分钟中枢上沿，多空转换转强 |
| A confirm | `5S3A 177.30 break_above` | 突破最近 5 分钟卖点压力 |
| B maintain | `5ZD 178.29 hold_above` | 守住 5 分钟震荡下沿，转换点继续有效 |
| C invalidate | `5ZD 178.29 break_below` | 跌破 5 分钟震荡下沿，转换尝试失败 |
| C invalidate | `30ZD 168.20 break_below` | 跌破 30 分钟结构下沿，转入防守路径 |

### 4.4 Future Check

| Field | Value |
|---|---|
| `check_date` | `2026-04-27` |
| `price_source` | Tencent quote API |
| `quote_time` | `2026-04-27 16:14:01` |
| `last_price` | `185.49` |
| `high` | `187.99` |
| `low` | `178.09` |
| `pct_chg` | `+3.83%` |

### 4.5 Result

`future_result = A_TRIGGERED`

Reason:

```text
last_price 185.49 > 5ZG 178.66
high 187.99 > 5ZG 178.66
last_price 185.49 > 5S3A 177.30
```

该样本验证 `PULLBACK_IN_UPTREND + MICRO_CONVERSION -> A`：

```text
多空转换点等待突破
  -> 突破 L2 中枢上沿与最近卖点压力
  -> 转换点向上确认
```

Follow-up:

```text
后续正式 BaoStock 5 分钟结构刷新后，应检查新 L2 中枢或回踩边界。
若后续回踩不破突破后的 5 分钟结构，可升级为更强的上升延续样本。
```

## 5. 切片 002：兆易创新 PULLBACK_IN_UPTREND 第一层转强

### 5.1 Snapshot

| Field | Value |
|---|---|
| `symbol` | `sh.603986` |
| `name` | 兆易创新 |
| `as_of` | `2026-04-24 15:00:00` |
| `structure_source` | BaoStock 前复权正式结构，`adjustflag=2` |
| `freshness.last_bar_at` | `2026-04-24 15:00:00` |

### 5.2 Radar Output At Slice

| Field | Value |
|---|---|
| `path` | `PULLBACK_IN_UPTREND` |
| `phase` | `STANDARD` |
| `action_bias` | `WAIT_RECLAIM` |
| `risk_level` | `MEDIUM` |
| `summary` | `上升趋势中的回落验证，5ZD 302.57转强，30ZD 287.13转弱。` |

### 5.3 Boundaries

| Scenario | Boundary | Meaning |
|---|---|---|
| A confirm | `5ZD 302.57 break_above` | 重新站回 5 分钟中枢，回落验证转强 |
| A confirm | `5ZG 307.62 break_above` | 突破 5 分钟中枢上沿，回到上涨延续 |
| B maintain | `30ZD 287.13 hold_above` | 守住 30 分钟中枢下沿，上升回落仍有效 |
| C invalidate | `30ZD 287.13 break_below` | 跌破 30 分钟结构下沿，回落验证失败 |
| C invalidate | `30B3A 268.59 break_below` | 跌破 30 分钟最近买点，趋势回落失败 |

### 5.4 Future Check

| Field | Value |
|---|---|
| `check_date` | `2026-04-27` |
| `price_source` | Tencent quote API |
| `quote_time` | `2026-04-27 16:14:14` |
| `last_price` | `303.42` |
| `high` | `309.58` |
| `low` | `297.75` |
| `pct_chg` | `+1.92%` |

### 5.5 Result

`future_result = A_PARTIAL_TRIGGERED`

Reason:

```text
last_price 303.42 > 5ZD 302.57
high 309.58 > 5ZG 307.62
last_price 303.42 < 5ZG 307.62
```

该样本验证 `PULLBACK_IN_UPTREND -> A` 的第一层：

```text
上升回落等待收复
  -> 收盘后报价已重新站回 L2 ZD
  -> 回落验证转强
  -> 但未站稳 L2 ZG，因此不能直接升级为完整主升延续确认
```

Follow-up:

```text
后续正式 BaoStock 5 分钟结构刷新后，应检查是否形成新的 5 分钟中枢、二买或三买。
如果再次站上并守住 5ZG 307.62，可升级为 A_TRIGGERED。
如果跌回 5ZD 302.57 下方，则这次收复失败，回到 B 或重新观察 C 风险。
```

## 6. 切片 003：澜起科技高位剧震后的 30 分钟三卖确认

### 6.1 Snapshot

| Field | Value |
|---|---|
| `symbol` | `sh.688008` |
| `name` | 澜起科技 |
| `as_of` | `2026-02-02 -> 2026-03-05` |
| `structure_source` | BaoStock 前复权正式结构，`adjustflag=2` |
| `slice_type` | 高位风险失效链条 |

### 6.2 Structure Chain

| Date | Event | Evidence |
|---|---|---|
| `2026-02-02` | 创新高后出现 30 分钟一卖 | 日线最高 `188.88`；CChan 30 分钟标记 `1卖`，价格 `188.38` |
| `2026-02-02` | 30 分钟顶背驰证据 | 创新高笔价格高于前主升笔，但 MACD 面积和 DIF 极值显著下降 |
| `2026-02-04 -> 2026-03-03` | 构建 30 分钟下跌中枢 | CChan 30 分钟中枢 `ZD 165.00 / ZG 171.00` |
| `2026-02-27 10:00:00` | 跌破 30 分钟中枢下沿 | 30 分钟低点 `161.58`，收盘 `162.27`，低于 `ZD 165.00` |
| `2026-03-03 14:30:00` | 下跌段低点，出现一买线索 | CChan 30 分钟标记 `1买`，价格 `147.69` |
| `2026-03-05 10:00:00` | 反抽不过中枢下沿，30 分钟三卖确认 | CChan 30 分钟标记 `3a卖`，反抽高点 `156.60`，低于 `ZD 165.00` |

### 6.3 Divergence Context

当前代码里的 `detect_recent_divergence` 只比较最近两段同方向笔，因此在 `2026-02-02` 切片上没有自动标出顶背驰。但人工对比主升段力度，可以看到明确的顶背驰证据：

| Compare | Up Bi | Price High | MACD Area | DIF Extreme |
|---|---|---:|---:|---:|
| Previous major up leg | `2026-01-13 15:00 -> 2026-01-22 10:00` | `181.43` | `44.4819` | `6.1160` |
| New-high up leg | `2026-01-30 10:00 -> 2026-02-02 10:00` | `188.38` | `13.6219` | `3.8543` |

Interpretation:

```text
price_high: 188.38 > 181.43
macd_area: 13.6219 < 44.4819
dif_extreme: 3.8543 < 6.1160
```

这说明价格创新高，但力度没有创新高。该切片应标记为：

```text
divergence_context = {
  level: "30",
  direction: "top",
  status: "manual_confirmed",
  related_bsp: "S1",
  effect: "increase_high_volatility_risk; C requires boundary break"
}
```

算法纪律：

```text
顶背驰 + 一卖 = 高位风险增强，但不单独触发卖出。
顶背驰 + 跌破 L1 中枢下沿 = C 失效触发。
顶背驰 + 跌破中枢后 L1 三卖 = DOWNWARD_DEFENSE_CONFIRMED。
```

### 6.4 Radar Interpretation

| Stage | Radar Meaning |
|---|---|
| `2026-02-02` | 创新高后顶背驰 + 一卖，主升进入高位剧震或风险观察 |
| `2026-02-27` | 跌破 30 分钟中枢下沿，上一轮高位推演进入 C 失效路径 |
| `2026-03-05` | 30 分钟三卖确认，防守路径得到结构确认 |

Expected radar state transition:

```text
HIGH_VOLATILITY_OSCILLATION
  -> C_TRIGGERED by break_below L1 ZD
  -> DOWNWARD_DEFENSE_CONFIRMED by L1 third sell
```

### 6.5 Boundaries

| Scenario | Boundary | Meaning |
|---|---|---|
| C invalidate | `30ZD 165.00 break_below` | 跌破 30 分钟中枢下沿，高位推演失败 |
| Defense confirm | `30S3A 156.60 fail_below_30ZD` | 反抽不回中枢下沿，30 分钟三卖确认 |
| Risk pressure | `30ZD 165.00 watch` | 跌破后的第一反压，不应再当作普通回落支撑 |

### 6.6 Result

`future_result = C_TRIGGERED`

Reason:

```text
2026-02-27 low 161.58 < 30ZD 165.00
2026-02-27 close 162.27 < 30ZD 165.00
2026-03-05 third sell price 156.60 < 30ZD 165.00
```

该样本验证：

```text
高位一卖不是必须卖出信号，但必须进入风险观察。
跌破 L1 中枢下沿后，上一轮主升或高位震荡推演失效。
反抽不回 L1 中枢并形成 L1 三卖后，应进入防守路径确认。
```

### 6.7 Regression Target

后续测试应锁定以下事实：

```python
{
    "symbol": "sh.688008",
    "slice": "lanqi-2026-02-02-to-2026-03-05",
    "expected": {
        "initial_path": "HIGH_VOLATILITY_OSCILLATION",
        "divergence_context": "TOP_DIVERGENCE_MANUAL_CONFIRMED",
        "future_result": "C_TRIGGERED",
        "confirmed_path": "DOWNWARD_DEFENSE_CONFIRMED",
    },
    "trigger_checks": [
        {"field": "30_TOP_DIVERGENCE", "operator": "price_new_high_momentum_lower"},
        {"field": "30ZD", "value": 165.00, "operator": "break_below"},
        {"field": "30S3A", "value": 156.60, "operator": "third_sell_below_30zd"},
    ],
}
```

UI 文案要求：

```text
30分钟三卖已确认，上一轮主升/高位震荡推演失败。
当前优先级切换为防守，不再按回落买点管理。
```

该文案仍然只是持仓复核提示，不是系统交易命令。

## 7. 切片 004：汇川技术下跌中一买修复失败

### 7.1 Snapshot

| Field | Value |
|---|---|
| `symbol` | `sz.300124` |
| `name` | 汇川技术 |
| `as_of` | `2026-04-07 -> 2026-04-24` |
| `structure_source` | BaoStock 前复权正式结构，`adjustflag=2` |
| `slice_type` | 下跌防守中的一买误判样本 |

### 7.2 Structure Chain

| Date | Event | Evidence |
|---|---|---|
| `2026-03-05 -> 2026-03-17` | 前一 30 分钟中枢 | CChan 30 分钟中枢 `ZD 69.96 / ZG 71.40` |
| `2026-04-07 15:00:00` | 下跌中出现 30 分钟一买 | CChan 30 分钟标记 `1买`，价格 `64.55` |
| `2026-04-15 10:00:00` | 一买后反弹高点 | 30 分钟高点 `69.90`，低于前中枢 `ZD 69.96` |
| `2026-04-21` | 回踩低点，看似 2B 观察 | 日线低点 `65.10`，但 CChan 未给出 30 分钟二买确认 |
| `2026-04-24` | 反弹失败后再创新低 | 日线低点 `63.63`；CChan 30 分钟一买更新到 `63.65` |

### 7.3 Divergence Context

汇川在下跌途中确实出现过底背驰线索，但它没有完成路径切换。

| Checkpoint | Divergence Evidence | Meaning |
|---|---|---|
| `2026-04-15` | 结构底背驰 `combined_score 0.431`，对应 `2026-04-07` 低点 `64.55` | 一买后的修复线索 |
| `2026-04-21` | 结构底背驰降为 `combined_score 0.329` | 修复质量不足 |
| `2026-04-24` | 再次出新低 `63.65/63.63`，结构底背驰 `combined_score 0.401` | 仍只是止跌线索，不是反转确认 |

算法纪律：

```text
底背驰 + 一买 = 修复预览，不等于转多。
一买反弹不过前 30 分钟中枢 ZD = 仍是防守路径。
后续再创新低 = 一买修复失败，不能升级为 BOTTOM_REPAIR。
```

### 7.4 Radar Interpretation

| Stage | Radar Meaning |
|---|---|
| `2026-04-07` | 下跌中出现一买和底背驰线索，但 L1/L0 没有转强 |
| `2026-04-15` | 反弹高点 `69.90` 仍低于前 30 分钟中枢下沿 `69.96`，修复没有站回结构 |
| `2026-04-24` | 跌出 `63.63` 新低，防守路径继续确认 |

Expected radar state transition:

```text
DOWNWARD_DEFENSE
  -> BOTTOM_REPAIR_PREVIEW by bottom divergence + B1
  -> repair rejected below prior L1 ZD
  -> DOWNWARD_DEFENSE_CONTINUED by new low
```

### 7.5 Boundaries

| Scenario | Boundary | Meaning |
|---|---|---|
| Repair watch | `30B1 64.55 watch` | 下跌中一买，只能作为修复线索 |
| Repair confirm | `30ZD 69.96 break_above` | 站回前 30 分钟中枢下沿，才允许修复升级 |
| Defense confirm | `30B1 64.55 break_below` | 跌破一买低点，修复失败 |
| New low | `day low 63.63` | 再创新低，防守路径继续 |

### 7.6 Result

`future_result = A_TRIGGERED`

Reason:

```text
2026-04-15 high 69.90 < prior 30ZD 69.96
2026-04-24 low 63.63 < 2026-04-07 B1 64.55
2026-04-24 close 64.75 remains far below prior 30ZD 69.96
```

这里的 `A_TRIGGERED` 是相对于 `DOWNWARD_DEFENSE` 路径而言：

```text
A = 防守中继确认
B = 防守状态维持
C = 站回关键中枢，防守解除转修复
```

该样本验证：

```text
下跌中的一买不能直接转多。
反弹不能站回前 30 分钟中枢时，仍按防守管理。
再创新低后，应确认一买修复失败。
```

### 7.7 Regression Target

```python
{
    "symbol": "sz.300124",
    "slice": "huichuan-2026-04-07-to-2026-04-24",
    "expected": {
        "initial_path": "DOWNWARD_DEFENSE",
        "divergence_context": "BOTTOM_DIVERGENCE_PREVIEW",
        "future_result": "A_TRIGGERED",
        "confirmed_path": "DOWNWARD_DEFENSE_CONTINUED",
    },
    "trigger_checks": [
        {"field": "30B1", "value": 64.55, "operator": "break_below"},
        {"field": "30ZD", "value": 69.96, "operator": "not_reclaimed"},
        {"field": "day_low", "value": 63.63, "operator": "new_low_after_b1"},
    ],
}
```

## 8. 切片 005：陕西华达下跌中枢后修复成功

### 8.1 Snapshot

| Field | Value |
|---|---|
| `symbol` | `sz.301517` |
| `name` | 陕西华达 |
| `as_of` | `2026-03-24 -> 2026-04-14` |
| `structure_source` | BaoStock 前复权正式结构，`adjustflag=2` |
| `slice_type` | 底部修复成功样本 |

### 8.2 Structure Chain

| Date | Event | Evidence |
|---|---|---|
| `2026-01-14` | 高点后进入下跌结构 | 日线最高 `103.99` |
| `2026-01-14 -> 2026-02-02` | 第一个大级别 30 分钟中枢 | CChan 30 分钟中枢 `ZD 72.76 / ZG 81.27 / GG 103.52 / DD 64.17` |
| `2026-02-04 -> 2026-03-04` | 第二个下跌中枢 | CChan 30 分钟中枢 `ZD 65.08 / ZG 67.36` |
| `2026-03-25 -> 2026-04-13` | 最近下跌中枢 | CChan 30 分钟中枢 `ZD 52.45 / ZG 54.69 / GG 57.19 / DD 52.30` |
| `2026-03-24 13:30:00` | 30 分钟一买 | CChan 标记 `1买`，价格 `51.57` |
| `2026-04-07 13:30:00` | 30 分钟类二买 | CChan 标记 `2s 类二买`，价格 `52.30`；日线低点 `52.06` |
| `2026-04-10 11:30:00` | 一笔向上突破最近下跌中枢 | 30 分钟高点 `68.62`，明显突破最近中枢 `ZG 54.69` |
| `2026-04-14 10:00:00` | 回踩后形成三买 | CChan 标记 `3a买`，价格 `65.21` |

### 8.3 Radar Interpretation

| Stage | Radar Meaning |
|---|---|
| `2026-03-24` | 下跌防守中出现一买，仍只是修复预览 |
| `2026-04-07` | 类二买形成，底部修复质量提高 |
| `2026-04-10` | 一笔强势突破最近下跌中枢上沿，修复转强 |
| `2026-04-14` | 回踩不破并形成三买A，修复路径确认 |

Expected radar state transition:

```text
DOWNWARD_DEFENSE
  -> BOTTOM_REPAIR_PREVIEW by B1
  -> BOTTOM_REPAIR by B2S
  -> CENTER_REBOUND / UPWARD_REPAIR by break_above recent L1 ZG
  -> UPWARD_MAJOR_WAVE by B3A
```

### 8.4 Boundaries

| Scenario | Boundary | Meaning |
|---|---|---|
| Repair preview | `30B1 51.57 watch` | 下跌中一买，只是修复线索 |
| Repair strengthen | `30B2S 52.30 hold_above` | 类二买后不再创新低，修复质量提高 |
| A confirm | `30ZG 54.69 break_above` | 突破最近下跌中枢上沿，底部修复转强 |
| A confirm | `30B3A 65.21 hold_above` | 回踩不破并形成三买A，修复确认 |
| C invalidate | `30B2S 52.30 break_below` | 跌破类二买低点，修复失败 |

### 8.5 Result

`future_result = B_MAINTAINED`

Reason:

```text
2026-04-10 high 68.62 > recent 30ZG 54.69
2026-04-14 B3A 65.21 > recent 30ZG 54.69
2026-04-14 low 65.21 remains above recent 30ZG 54.69
```

这里的 `B_MAINTAINED` 是相对于三买确认后的 `UPWARD_MAJOR_WAVE` 路径而言。真正的 A 触发发生在 `52.30` 一笔上攻突破最近下跌中枢 `ZG 54.69` 的过程中；到 `2026-04-14` 三买确认后，雷达视角已经进入主升路径维持。

该样本验证：

```text
一买只是修复预览。
类二买后不创新低，修复质量提高。
突破下跌中枢上沿后，修复转强。
突破后回踩不破并形成三买，底部修复确认。
```

### 8.6 Follow-up Risk

陕西华达后续在 `2026-04-20` 上冲到 `75.60` 后，结构背驰检测出现高危顶背驰：

```text
前上升笔：52.30 -> 68.70
后创新高笔：67.45 -> 75.60
价格创新高，但 MACD 面积和 DIF 明显衰减。
```

因此这个切片只用于验证底部修复成功，不用于追高确认。后续可另建一个高位风险切片。

### 8.7 Regression Target

```python
{
    "symbol": "sz.301517",
    "slice": "shanxi-huada-2026-03-24-to-2026-04-14",
    "expected": {
        "initial_path": "UPWARD_MAJOR_WAVE",
        "future_result": "B_MAINTAINED",
        "confirmed_path": "UPWARD_MAJOR_WAVE_AFTER_REPAIR",
    },
    "trigger_checks": [
        {"field": "30B1", "value": 51.57, "operator": "preview_only"},
        {"field": "30B2S", "value": 52.30, "operator": "hold_above"},
        {"field": "30ZG", "value": 54.69, "operator": "break_above"},
        {"field": "30B3A", "value": 65.21, "operator": "third_buy_above_30zg"},
    ],
}
```

## 9. 切片 006：新易盛大中枢上小中枢震荡后强势离开

### 9.1 Snapshot

| Field | Value |
|---|---|
| `symbol` | `sz.300502` |
| `name` | 新易盛 |
| `as_of` | `2026-03-31 -> 2026-04-23` |
| `structure_source` | BaoStock 前复权正式结构，`adjustflag=2` |
| `slice_type` | 重点关注类型：大中枢上方小中枢蓄势后强势离开 |

### 9.2 Structure Chain

| Date | Event | Evidence |
|---|---|---|
| `2025-10-29 -> 2026-03-25` | 日线中枢 | CChan 日线中枢 `ZD 372.54 / ZG 433.33 / GG 472.00 / DD 287.31` |
| `2026-03-31` | 日线三买A | CChan 日线标记 `3a买`，价格 `434.70`，略高于日线中枢 `ZG 433.33` |
| `2026-04-01 -> 2026-04-08` | 三买后窄幅震荡 | CChan 30 分钟中枢 `ZD 442.86 / ZG 462.87` |
| `2026-04-08 -> 2026-04-20` | 向上离开后形成更高 30 分钟中枢 | CChan 30 分钟中枢 `ZD 490.02 / ZG 502.00 / GG 538.88 / DD 485.59` |
| `2026-04-23` | 三买后创新高 | 日内最高 `627.80`，CChan 日线出现 `1p卖` 风险标记 |

### 9.3 Radar Interpretation

| Stage | Radar Meaning |
|---|---|
| `2026-03-31` | 日线三买确认，价格没有跌回前日线中枢内部 |
| `2026-04-01 -> 2026-04-08` | 30 分钟窄幅震荡，属于三买后的蓄势观察，不应过早判定失败 |
| `2026-04-08` | 向上突破 30 分钟窄幅中枢上沿，主升路径继续 |
| `2026-04-23` | 创出 `627.80` 新高，三买后延伸得到验证；同时出现卖点风险，应进入高位风险观察 |

Expected radar state transition:

```text
DAILY_THIRD_BUY_CONFIRMED
  -> UPWARD_MAJOR_WAVE by day B3A above day ZG
  -> B_MAINTAINED while 30m narrow center holds
  -> UPWARD_EXTENSION by break_above 30ZG
  -> HIGH_VOLATILITY_RISK_WATCH after new high + sell signal
```

### 9.4 Key Pattern

这是雷达后续需要重点识别的结构类型：

```text
大级别中枢上沿三买
  -> 小级别在大中枢上方构造小中枢
  -> 小中枢不跌回大中枢内部
  -> 小级别向上离开小中枢
  -> 强势一笔展开
```

算法意义：

```text
大级别三买提供方向合法性。
小级别中枢震荡提供蓄势结构。
小级别向上离开提供执行确认。
```

雷达要求：

```text
不能把大中枢上方的小中枢震荡误判为走弱。
只要小中枢不跌回大中枢内部，应优先按主升蓄势管理。
突破小中枢上沿后，应提示主升延伸确认。
跌回大中枢内部后，才考虑三买失败或主升降级。
```

这是实盘中需要重点关注的“可吃到大波段”的结构类型。

### 9.5 Boundaries

| Scenario | Boundary | Meaning |
|---|---|---|
| Major-wave maintain | `dayZG 433.33 hold_above` | 日线三买不跌回前中枢内部，主升路径维持 |
| Short consolidation maintain | `30ZD 442.86 hold_above` | 三买后 30 分钟窄幅震荡没有破坏 |
| Extension confirm | `30ZG 462.87 break_above` | 突破三买后窄幅中枢上沿，向上离开确认 |
| Strong extension confirm | `30ZG 502.00 break_above` | 站上更高 30 分钟中枢上沿，主升延伸加强 |
| Major-wave downgrade | `dayZG 433.33 break_below` | 跌回日线中枢内部，日线三买失败或降级 |

### 9.6 Result

`future_result = B_MAINTAINED`

Reason:

```text
2026-03-31 daily B3A 434.70 > day ZG 433.33
2026-04-01 -> 2026-04-08 narrow 30m center held above day ZG
2026-04-23 high 627.80 > narrow 30m ZG 462.87
```

这里的 `B_MAINTAINED` 是相对于 `UPWARD_MAJOR_WAVE` 路径而言。三买后的 30 分钟震荡没有破坏大级别三买，后续突破并创新高，说明当时不应该把窄幅震荡误判成失败。

该样本验证：

```text
大级别三买后，短期横盘不是失败。
只要不跌回大级别中枢内部，下级别震荡可以按蓄势管理。
突破下级别震荡中枢上沿后，应维持或加强主升路径。
创新高后出现卖点标记时，另行进入高位风险观察，不反向否定三买成功。
```

### 9.7 Regression Target

```python
{
    "symbol": "sz.300502",
    "slice": "eoptolink-2026-03-31-to-2026-04-23",
    "expected": {
        "initial_path": "UPWARD_MAJOR_WAVE",
        "future_result": "B_MAINTAINED",
        "confirmed_path": "UPWARD_MAJOR_WAVE_EXTENSION",
    },
    "trigger_checks": [
        {"field": "dayB3A", "value": 434.70, "operator": "third_buy_above_day_zg"},
        {"field": "dayZG", "value": 433.33, "operator": "hold_above"},
        {"field": "30ZG", "value": 462.87, "operator": "break_above"},
        {"field": "future_high", "value": 627.80, "operator": "new_high_after_b3a"},
    ],
}
```

## 10. 切片 007：东田微小转大后二买三买快速合并确认

### 10.1 Snapshot

| Field | Value |
|---|---|
| `symbol` | `sz.301183` |
| `name` | 东田微 |
| `as_of` | `2026-03-23 -> 2026-04-13` |
| `structure_source` | BaoStock 前复权正式结构，`adjustflag=2` |
| `slice_type` | 小转大后二买/类二买快速修复并升级三买样本 |

### 10.2 Structure Chain

| Date | Event | Evidence |
|---|---|---|
| `2026-03-23 15:00:00` | 30 分钟类一买低点 | CChan 30 分钟标记 `1p买`，价格 `134.00` |
| `2026-03-24 14:30:00` | 小转大启动 | 30 分钟 K 线从 `136.28` 快速拉到最高 `162.80` |
| `2026-03-25 11:30:00` | 30 分钟二买 | CChan 标记 `2买`，价格 `153.81` |
| `2026-03-31 10:00:00` | 30 分钟类二买 | CChan 标记 `2s类二买`，价格 `153.57` |
| `2026-03-24 -> 2026-04-09` | 30 分钟修复中枢 | CChan 30 分钟中枢 `ZD 153.81 / ZG 162.80 / GG 174.49 / DD 153.57` |
| `2026-04-13 10:00:00` | 30 分钟三买A确认 | CChan 标记 `3a买`，价格 `182.97` |
| `2026-04-22` | 后续新高 | 最高 `217.75`，随后进入高位风险观察 |

### 10.3 Radar Interpretation

| Stage | Radar Meaning |
|---|---|
| `2026-03-23` | 下跌末端出现类一买，属于修复预览 |
| `2026-03-24` | 一根强势 30 分钟 K 线直接拉回中枢上沿，出现小转大特征 |
| `2026-03-25 -> 2026-03-31` | 二买和类二买靠得很近，修复没有给出充分低吸节奏 |
| `2026-04-03 -> 2026-04-09` | 价格突破并站上 `30ZG 162.80`，修复升级为进攻观察 |
| `2026-04-13` | 三买A在 `182.97` 确认，路径升级为主升维持 |

Expected radar state transition:

```text
DOWNWARD_DEFENSE / REPAIR_PREVIEW
  -> CENTER_REBOUND by B1P + B2/B2S
  -> A_TRIGGERED by break_above 30ZG 162.80
  -> UPWARD_MAJOR_WAVE by 30B3A 182.97
  -> B_MAINTAINED while higher center holds
```

### 10.4 Key Pattern

这是另一类需要雷达重点识别的结构：

```text
下跌末端类一买
  -> 快速强拉形成小转大
  -> 二买/类二买非常接近
  -> 很快突破修复中枢上沿
  -> 三买确认时已经离低点较远
```

算法意义：

```text
不能要求所有底部修复都走出标准慢二买。
小转大结构里，二买和三买可能时间间隔很短，甚至在实盘感受上接近合并。
雷达应把 B1P/B2/B2S 视为修复链条，把突破 30ZG 作为修复升级，把 B3A 作为主升确认。
```

### 10.5 Boundaries

| Scenario | Boundary | Meaning |
|---|---|---|
| Repair preview | `30B1P 134.00 watch` | 类一买只是修复预览 |
| Repair maintain | `30B2/B2S 153.81/153.57 hold_above` | 二买/类二买不破，修复链条维持 |
| Repair upgrade | `30ZG 162.80 break_above` | 突破修复中枢上沿，小转大升级 |
| Major-wave confirm | `30B3A 182.97 hold_above` | 三买确认后，路径转主升维持 |
| Repair failure | `30ZD 153.81 break_below` | 跌回修复中枢下沿，修复失败 |

### 10.6 Result

`future_result = A_TRIGGERED -> B_MAINTAINED`

Reason:

```text
2026-03-23 B1P 134.00
2026-03-25 B2 153.81
2026-03-31 B2S 153.57
2026-04-03 high 174.49 > 30ZG 162.80
2026-04-13 B3A 182.97 > 30ZG 162.80
2026-04-22 high 217.75 > B3A 182.97
```

该样本验证：

```text
小转大不一定给出舒适的二买回踩。
二买/类二买密集出现后，只要不破修复中枢下沿，应保持修复观察。
突破修复中枢上沿后，A 路径触发。
后续三买确认后，雷达应升级为 UPWARD_MAJOR_WAVE，而不是继续停留在底部修复。
```

### 10.7 Regression Target

```python
{
    "symbol": "sz.301183",
    "slice": "doti-micro-2026-03-23-to-2026-04-13",
    "expected": {
        "preview_path": "CENTER_REBOUND",
        "preview_future_result": "A_TRIGGERED",
        "confirmed_path": "UPWARD_MAJOR_WAVE",
        "confirmed_future_result": "B_MAINTAINED",
    },
    "trigger_checks": [
        {"field": "30B1P", "value": 134.00, "operator": "preview_only"},
        {"field": "30B2", "value": 153.81, "operator": "hold_above"},
        {"field": "30B2S", "value": 153.57, "operator": "hold_above"},
        {"field": "30ZG", "value": 162.80, "operator": "break_above"},
        {"field": "30B3A", "value": 182.97, "operator": "third_buy_above_30zg"},
    ],
}
```

## 11. 切片 008：东方日升三买后快速一卖二卖转风险

### 11.1 Snapshot

| Field | Value |
|---|---|
| `symbol` | `sz.300118` |
| `name` | 东方日升 |
| `as_of` | `2026-02-02 -> 2026-02-09` |
| `structure_source` | BaoStock 前复权正式结构，`adjustflag=2` |
| `slice_type` | 三买成功后快速卖点风险样本 |

### 11.2 Structure Chain

| Date | Event | Evidence |
|---|---|---|
| `2026-01-26 -> 2026-02-04` | 30 分钟中枢 | CChan 30 分钟中枢 `ZD 20.88 / ZG 22.28 / GG 24.91 / DD 18.92` |
| `2026-02-02` | 回踩中枢下沿形成三买观察 | 日线最低 `20.87`，30 分钟结构低点 `20.88`，贴近中枢 `ZD 20.88` |
| `2026-02-04` | 快速冲高后一卖 | CChan 日线 `1p卖 25.99`，30 分钟 `1卖 25.98` |
| `2026-02-09` | 未过前高后二卖 | CChan 30 分钟 `2卖 24.60`，低于 `25.98` 前高 |
| `2026-02-10 -> 2026-02-13` | 卖点后回落 | 日线收盘从 `22.87` 回落到 `19.95` |

### 11.3 Divergence / Momentum Context

这是一个“不是三买失败，而是三买成功后快速走完一笔并转入卖点风险”的样本。

30 分钟上升笔力度对比：

| Up Leg | Price | MACD Area | Meaning |
|---|---:|---:|---|
| `2026-02-02 15:00 -> 2026-02-04 13:30` | `20.88 -> 25.98` | `4.1426` | 三买后第一段强势上攻 |
| `2026-02-06 10:00 -> 2026-02-09 11:00` | `21.48 -> 24.60` | `0.0635` | 未过前高且力度明显衰减 |

算法口径：

```text
2/9 的风险不是“创新高顶背驰”，而是“反抽未过前高 + 力度显著衰减 + CChan 二卖”。
雷达应该把它作为三买后一卖/二卖风险切片，而不是把 2/2 三买本身判为失败。
```

### 11.4 Radar Interpretation

| Stage | Radar Meaning |
|---|---|
| `2026-02-02` | 回踩 30 分钟中枢下沿，三买观察成立 |
| `2026-02-04` | 快速上攻到 `25.98/25.99`，三买后的强势一笔已经完成，并出现一卖 |
| `2026-02-09` | 反抽未过前高，力度大幅衰减，30 分钟二卖确认 |
| `2026-02-10` | 二卖后回落，主升/回落买点推演应切到风险路径 |

Expected radar state transition:

```text
THIRD_BUY_RETEST
  -> A_TRIGGERED by fast break_above 5m/30m pressure
  -> FIRST_SELL_RISK by 30S1 25.98
  -> C_TRIGGERED by 30S2 24.60 below prior high + momentum decay
```

### 11.5 Key Pattern

这是雷达需要重点区分的另一类结构：

```text
三买不是失败
  -> 三买后确实快速上攻
  -> 上攻后立刻出现一卖
  -> 二卖未过前高且力度衰减
  -> 后续应按风险管理，不再按三买持有逻辑硬扛
```

算法意义：

```text
三买成功不等于可以永久持有。
三买后的第一笔若快速走完，并且出现一卖/二卖，雷达需要从“吃主升”切到“保护利润”。
二卖未过前高且力度显著衰减，应作为 C 路径触发事件。
```

### 11.6 Boundaries

| Scenario | Boundary | Meaning |
|---|---|---|
| Third-buy maintain | `30ZD 20.88 hold_above` | 回踩中枢下沿不破，三买观察有效 |
| Fast extension | `30ZG 22.28 break_above` | 三买后快速向上离开中枢 |
| First sell risk | `30S1 25.98 watch` | 快速一笔后出现第一卖点 |
| Second sell risk | `30S2 24.60 below 25.98` | 二卖未过前高，风险确认 |
| Invalidate / risk | `momentum area 0.0635 << 4.1426` | 第二段上攻力度显著衰减 |

### 11.7 Result

`future_result = C_TRIGGERED`

Reason:

```text
2026-02-02 low 20.87 / 30m low 20.88 near 30ZD 20.88
2026-02-04 30S1 25.98
2026-02-09 30S2 24.60 < prior high 25.98
second up area 0.0635 is far below first up area 4.1426
```

该样本验证：

```text
三买后快速上攻是 A 的兑现，不是失败。
一卖出现后，雷达必须进入高位风险观察。
二卖未过前高且力度衰减时，本轮三买持有推演进入 C 风险路径。
```

### 11.8 Regression Target

```python
{
    "symbol": "sz.300118",
    "slice": "risen-energy-2026-02-02-to-2026-02-09",
    "expected": {
        "initial_path": "PULLBACK_IN_UPTREND",
        "risk_event": "SECOND_SELL_BELOW_PRIOR_HIGH",
        "future_result": "C_TRIGGERED",
    },
    "trigger_checks": [
        {"field": "30ZD", "value": 20.88, "operator": "hold_above"},
        {"field": "30S1", "value": 25.98, "operator": "first_sell"},
        {"field": "30S2", "value": 24.60, "operator": "second_sell_below_prior_high"},
        {"field": "momentum_area", "value": 0.0635, "operator": "decays_vs_4.1426"},
    ],
}
```

## 12. 切片 009：赣锋锂业卖压后跌回原中枢再小转大拉回

### 12.1 Snapshot

| Field | Value |
|---|---|
| `symbol` | `sz.002460` |
| `name` | 赣锋锂业 |
| `as_of` | `2026-04-15 -> 2026-04-27` |
| `structure_source` | BaoStock 前复权正式结构，`adjustflag=2` |
| `slice_type` | 卖点压力后跌回原中枢，再小转大修复样本 |

### 12.2 Structure Chain

| Date | Event | Evidence |
|---|---|---|
| `2026-03-30 -> 2026-04-14` | 原 30 分钟中枢 | CChan 30 分钟中枢 `ZD 77.51 / ZG 81.44 / GG 83.75 / DD 76.50` |
| `2026-04-14` | 高位一卖压力 | CChan 日线 `1p卖 88.48`，30 分钟 `1卖 88.39` |
| `2026-04-15` | 买点观察低点 | 日线低点 `81.84`，30 分钟低点 `81.85`，贴近原 30 分钟中枢上沿 `ZG 81.44` |
| `2026-04-15 -> 2026-04-16` | 5 分钟二卖/类二卖压力 | CChan 5 分钟 `2卖 83.79`，`2s卖 86.63` |
| `2026-04-23` | 跌回原中枢并打出低点 | 日线最低 `79.00`，跌回原 30 分钟中枢内部；CChan 5 分钟 `1买 79.00 / 2买 79.50 / 2s买 79.78` |
| `2026-04-24` | 小转大拉回并形成三买 | CChan 5 分钟 `3a买 82.00`，随后 `1p卖 86.25` |
| `2026-04-27` | 拉回后仍未完全化解卖压 | 收盘 `84.11`，高点 `86.46`，接近但未稳定突破 `86.25` 卖点压力 |

### 12.3 Radar Interpretation

| Stage | Radar Meaning |
|---|---|
| `2026-04-15` | 回踩原 30 分钟中枢上沿附近，可作为买点观察，但上方一卖压力未消失 |
| `2026-04-17` | 反弹未能化解 5 分钟二卖/类二卖压力，仍属于高波动震荡 |
| `2026-04-23` | 跌回原 30 分钟中枢内部，前一轮买点观察进入 C 风险路径 |
| `2026-04-23 -> 2026-04-24` | 短级别 `1买/2买/2s/3a` 密集出现，小转大拉回 |
| `2026-04-27` | 已站回小中枢上方，但卖点压力 `86.25` 仍需继续确认 |

Expected radar state transition:

```text
HIGH_VOLATILITY_OSCILLATION
  -> C_TRIGGERED by falling back below 5ZD 81.53 and 30ZG 81.44
  -> SMALL_TURN_BIG_REPAIR by 5B1/B2/B2S/B3A
  -> A_PARTIAL_TRIGGERED by reclaiming 5ZG 80.49
  -> A_FULL only after breaking 5S1P 86.25 / prior sell pressure
```

### 12.4 Key Pattern

这是雷达需要补的一类“往返结构”：

```text
买点观察
  -> 上方卖点压力没有化解
  -> 跌回原中枢
  -> 小级别连续买点小转大
  -> 拉回中枢上方
  -> 仍需确认是否突破前卖点压力
```

算法意义：

```text
跌回原中枢说明前一轮买点观察失败，必须先切 C。
但跌回后如果小级别迅速出现 1买/2买/2s/3a，并重新站回中枢上方，不能继续机械看空。
拉回只算 A_PARTIAL，必须突破前卖点压力后，才算完整转强。
```

### 12.5 Comparison With Slice 006

这个样本要和新易盛切片放在一起看：

| Dimension | 新易盛 | 赣锋锂业 |
|---|---|---|
| 大级别背景 | 大中枢上沿三买后进入主升尝试 | 3 月 27 日附近离开日线中枢后进入主升尝试 |
| 小级别结构 | 大中枢上方形成 30 分钟小中枢 | 离开日线中枢后形成 30 分钟小中枢 |
| 离开结果 | 小中枢向上离开并走出强势一笔 | 离开后被卖点拉回小中枢内部 |
| 路径判断 | `UPWARD_MAJOR_WAVE` 维持 | `HIGH_VOLATILITY_OSCILLATION`，跌回后重新修复 |
| 雷达意义 | 大中枢上小中枢成功样本 | 大中枢上小中枢尝试失败/未完成样本 |

关键规则：

```text
大中枢上方形成小中枢以后，
只有向上离开并不跌回小中枢，才算强势主浪；
如果离开后被拉回小中枢内部，
只能降级为高波动震荡或重新修复，
不能继续按主升持有。
```

### 12.6 Boundaries

| Scenario | Boundary | Meaning |
|---|---|---|
| Risk trigger | `5ZD 81.53 break_below` | 跌破 5 分钟中枢下沿，短线转弱 |
| Risk trigger | `30ZG 81.44 break_below` | 跌回原 30 分钟中枢内部，前买点观察失败 |
| Repair preview | `5B1 79.00 / 5B2 79.50 / 5B2S 79.78` | 跌回后短级别修复链条形成 |
| Repair partial | `5ZG 80.49 break_above` | 重新站回小中枢上沿，小转大拉回成立 |
| Full confirm | `5S1P 86.25 break_above` | 化解前卖点压力，修复转强才完整确认 |

### 12.7 Result

`future_result = C_TRIGGERED -> A_PARTIAL_TRIGGERED`

Reason:

```text
2026-04-23 close 79.88 < 5ZD 81.53 and < 30ZG 81.44
2026-04-23 low 79.00 produced 5B1
2026-04-24 5B3A 82.00 > 30ZG 81.44
2026-04-27 close 84.11 > 5ZG 80.49
2026-04-27 close 84.11 is still below 5S1P 86.25
```

该样本验证：

```text
买点后出现二卖压力，不能忽略。
跌回原中枢时，前买点推演必须先判 C。
小转大拉回后可以重新观察，但在突破前卖点压力前，只能算 A_PARTIAL。
```

### 12.8 Regression Target

```python
{
    "symbol": "sz.002460",
    "slice": "ganfeng-lithium-2026-04-15-to-2026-04-27",
    "expected": {
        "risk_path": "HIGH_VOLATILITY_OSCILLATION",
        "risk_result": "C_TRIGGERED",
        "repair_path": "HIGH_VOLATILITY_OSCILLATION",
        "repair_result": "A_PARTIAL_TRIGGERED",
    },
    "trigger_checks": [
        {"field": "5ZD", "value": 81.53, "operator": "break_below"},
        {"field": "30ZG", "value": 81.44, "operator": "break_below"},
        {"field": "5B3A", "value": 82.00, "operator": "third_buy_after_return_to_center"},
        {"field": "5ZG", "value": 80.49, "operator": "break_above"},
        {"field": "5S1P", "value": 86.25, "operator": "not_fully_reclaimed"},
    ],
}
```

## 13. Data Availability Note

在 `2026-04-27 16:40` 左右检查时：

```text
BaoStock sh.603986 day / 30 / 5 查询 2026-04-27 均返回 0 行。
BaoStock sh.603893 本地正式结构仍停在 2026-04-24。
Tencent quote API 已返回 2026-04-27 收盘后报价。
```

因此本文档中的 `future_check` 使用 Tencent quote API 做触发验证，而不是替代 BaoStock 正式结构。正式结构刷新后，应再次生成 `algorithm_v2` 输出并补充切片复核结果。

## 14. Regression Test Plan

当前历史切片已经有 pytest 覆盖：

```text
tests/test_radar_historical_slices.py
```

后续新增切片时，继续按本文档格式补充 fixture：

```python
{
    "symbol": "sh.603893",
    "as_of": "2026-04-24 15:00:00",
    "expected": {
        "path": "PULLBACK_IN_UPTREND",
        "phase": "MICRO_CONVERSION",
        "action_bias": "WAIT_BREAKOUT",
        "future_result": "A_TRIGGERED",
    },
    "trigger_checks": [
        {"field": "5ZG", "value": 178.66, "operator": "last_price_gt"},
        {"field": "5S3A", "value": 177.30, "operator": "last_price_gt"},
    ],
}
```

最小测试要求：

| Requirement | Meaning |
|---|---|
| Lock slice output | 固定切片当时的 `path / phase / action_bias` |
| Lock A/B/C boundary roles | 固定每个边界的角色，不允许误把 confirm 当 support |
| Check future trigger | 用后续行情验证 A/B/C 是否触发 |
| Prevent overclaiming | 兆易创新这种只触发第一层 A 的样本，不能被算法直接升级为完整主升 |
