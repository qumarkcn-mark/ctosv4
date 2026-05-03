# Radar Pattern Taxonomy

> Created: 2026-04-27
> Related: `docs/RADAR_ALGORITHM_WHITEPAPER_V0_1.md`, `docs/RADAR_HISTORICAL_SLICES.md`

本文档把历史切片抽象成雷达可识别的走势模板。它不是交易建议，也不是 UI 文案；它是 `radar_algorithm_v2` 后续升级的工程规则表。

核心目标：

```text
CChan 事实
  -> LevelAtom
  -> Pattern
  -> Path
  -> A/B/C 边界
  -> 历史切片回归
```

所有市场相关描述仅供参考，不构成投资建议。

## 1. Design Decision

雷达下一阶段不应该只做七类 `path` 分类。七类路径只能回答“现在大概是哪种状态”，但不能解释为什么新易盛和赣锋锂业同样有“大中枢上小中枢”，一个能走主升，一个只是高波动修复。

因此新增一层：

```text
Pattern Layer
```

路径和模板的分工：

| Layer | Question | Example |
|---|---|---|
| `path` | 当前主状态是什么 | `UPWARD_MAJOR_WAVE` |
| `pattern` | 当前结构是哪种缠论模板 | `BIG_CENTER_SMALL_CENTER_UP_BREAK` |
| `boundary` | 接下来什么事件改变推演 | `break_above small_center.ZG` |
| `scenario` | A/B/C 分别代表什么 | A 延伸、B 维持、C 降级 |

工程原则：

```text
先判 path，再识别 pattern。
pattern 可以修正 action_bias、risk_level、summary 和 A/B/C 边界。
pattern 不直接覆盖底层 CChan 事实。
```

## 2. Required Facts

当前 `LevelAtom` 已有：

| Fact | Status |
|---|---|
| `price` | 已有 |
| `center` | 已有 |
| `previous_center` | 已有 |
| `center_relation` | 已有 |
| `historical_centers` | 已有 |
| `center_nesting` | 已有 |
| `buy_events / sell_events` | 已有 |
| `divergence` | 已有基础字段 |
| `position_state` | 已有 |
| `tags` | 已有 |

下一步需要补强：

| Fact | Why |
|---|---|
| `center_binding` | 买卖点必须绑定到哪个中枢 |
| `leave_return_status` | 判断离开后是否跌回原中枢 |
| `momentum_compare` | 识别二卖未过前高、力度衰减 |
| `pattern_tags` | 输出模板，如 `SMALL_TURN_BIG` |

推荐 contract：

```json
{
  "pattern_tags": [
    "BIG_CENTER_SMALL_CENTER",
    "SMALL_TURN_BIG",
    "SELL_PRESSURE_UNRESOLVED"
  ],
  "event_sequence": [
    {"code": "B1P", "level": "30", "price": 134.0, "time": "..."},
    {"code": "B2", "level": "30", "price": 153.81, "time": "..."},
    {"code": "B3A", "level": "30", "price": 182.97, "time": "..."}
  ],
  "center_binding": {
    "B3A": {"level": "30", "center_zg": 162.8, "status": "above_zg"}
  },
  "momentum_compare": {
    "prev_area": 4.1426,
    "curr_area": 0.0635,
    "area_ratio": 0.015
  }
}
```

## 3. Pattern Matrix

| Pattern | Meaning | Primary Path | Slices |
|---|---|---|---|
| `MICRO_CONVERSION_BREAKOUT` | 5 分钟转换点突破 | `PULLBACK_IN_UPTREND` | 瑞芯微 |
| `PULLBACK_PARTIAL_RECLAIM` | 回落后只站回第一层边界 | `PULLBACK_IN_UPTREND` | 兆易创新 |
| `TOP_DIVERGENCE_THIRD_SELL` | 高位顶背驰后跌破中枢并三卖 | `HIGH_VOLATILITY_OSCILLATION` | 澜起科技 |
| `B1_REPAIR_FAILURE` | 下跌中一买反弹不过前中枢并创新低 | `DOWNWARD_DEFENSE` | 汇川技术 |
| `BOTTOM_REPAIR_TO_MAJOR_WAVE` | 一买/二买后突破下跌中枢并三买确认 | `CENTER_REBOUND -> UPWARD_MAJOR_WAVE` | 陕西华达 |
| `BIG_CENTER_SMALL_CENTER_UP_BREAK` | 大中枢上方小中枢蓄势后强势离开 | `UPWARD_MAJOR_WAVE` | 新易盛 |
| `HISTORICAL_HIGH_PRESSURE` | 接近或突破历史前高，只作为压力观察 | 当前 path 不变 | 澜起科技 |
| `SMALL_TURN_BIG_FAST_B2_B3` | 小转大后二买/三买快速合并确认 | `CENTER_REBOUND -> UPWARD_MAJOR_WAVE` | 东田微 |
| `THIRD_BUY_FAST_SELL_RISK` | 三买后快速一卖二卖，二卖未过前高 | `PULLBACK_IN_UPTREND -> HIGH_VOLATILITY` | 东方日升 |
| `BIG_CENTER_SMALL_CENTER_PULLBACK_REPAIR` | 大中枢上小中枢离开失败，被拉回后修复 | `HIGH_VOLATILITY_OSCILLATION` | 赣锋锂业 |
| `FAILED_PULLBACK_SECOND_BUY_REPAIR` | 回落验证失效后，低位新5分钟中枢形成并守住30分钟二买防线 | `PULLBACK_IN_UPTREND C_TRIGGERED -> BOTTOM_REPAIR WATCH` | 天孚通信待补切片 |

## 4. Pattern Rules

### 4.1 MICRO_CONVERSION_BREAKOUT

对应切片：瑞芯微。

识别条件：

```text
L0/L1 向上或支撑
L2 在窄中枢内
L2 同时存在近期买点和卖点压力
价格接近 L2 中枢边界
```

A/B/C：

| Scenario | Boundary |
|---|---|
| A | 突破 `L2.ZG` 且突破最近 L2 卖点压力 |
| B | 守住 `L2.ZD` |
| C | 跌破 `L2.ZD` 或跌破 `L1.ZD` |

Algorithm gap:

```text
当前已有 MICRO_CONVERSION phase，但需要把窄幅、多空同场、卖点压力绑定成显式 pattern。
```

### 4.2 PULLBACK_PARTIAL_RECLAIM

对应切片：兆易创新。

识别条件：

```text
L0/L1 向上
L2 回落到中枢下沿附近
价格重新站回 L2.ZD，但未稳定站上 L2.ZG
```

A/B/C：

| Scenario | Boundary |
|---|---|
| A partial | 站回 `L2.ZD` |
| A full | 站上并守住 `L2.ZG` |
| B | 守住 `L1.ZD` |
| C | 跌破 `L1.ZD` 或最近 L1 买点 |

Algorithm gap:

```text
历史评估已有 A_PARTIAL，但实时输出还应明确 partial/full 两级确认。
```

### 4.3 TOP_DIVERGENCE_THIRD_SELL

对应切片：澜起科技。

识别条件：

```text
高位创新高
同级别后一段上攻力度低于前主升笔
出现 S1/S1P 风险
跌破 L1 中枢 ZD
反抽不过 L1.ZD，形成 S3
```

A/B/C：

| Scenario | Boundary |
|---|---|
| A | 重新站回 L1.ZD 并化解卖点压力 |
| B | 高位震荡但不跌破 L1.ZD |
| C | 跌破 L1.ZD 后三卖确认 |

Algorithm gap:

```text
已补 detect_structural_divergence，但 path 分类还没有把结构背驰 + S1 + S3 串成 pattern。
```

### 4.4 B1_REPAIR_FAILURE

对应切片：汇川技术。

识别条件：

```text
L0/L1/L2 下行
下跌中出现 B1/B1P 或底背驰
反弹不能站回前 L1 中枢 ZD
随后跌破 B1 低点或再创新低
```

A/B/C：

| Scenario | Boundary |
|---|---|
| A | 防守中继确认：跌破 B1 或反弹不过 L1.ZD 后继续下行 |
| B | 仍在 L1.ZD 下方震荡 |
| C | 站回 L1.ZD，防守解除转修复 |

Algorithm gap:

```text
DOWNWARD_DEFENSE 的 A/B/C 语义和其他上涨路径相反，需要在 contract 中显式标注 role。
```

### 4.5 BOTTOM_REPAIR_TO_MAJOR_WAVE

对应切片：陕西华达。

识别条件：

```text
下跌后出现 B1/B1P
B2/B2S 不破 B1
突破最近下跌中枢 ZG
回踩不破并形成 B3A/B3B
```

A/B/C：

| Scenario | Boundary |
|---|---|
| A | 突破最近下跌中枢 `L1.ZG` |
| B | B2/B2S 上方维持 |
| C | 跌破 B2/B2S 或跌回下跌中枢 |

Algorithm gap:

```text
当前算法能在三买后归入 UPWARD_MAJOR_WAVE，但缺少“修复链条”解释。
需要输出 transition: DOWNWARD_DEFENSE -> CENTER_REBOUND -> UPWARD_MAJOR_WAVE。
```

### 4.6 BIG_CENTER_SMALL_CENTER_UP_BREAK

对应切片：新易盛。

识别条件：

```text
L0 大中枢上沿形成三买
L1 在 L0 中枢上方形成小中枢
L1 小中枢不跌回 L0 大中枢内部
L1/L2 向上离开小中枢
L1/L2 没有未化解的一卖/二卖风险
```

A/B/C：

| Scenario | Boundary |
|---|---|
| A | 突破小中枢 `ZG`，并不跌回 |
| B | 小中枢继续在大中枢上方震荡 |
| C | 跌回大中枢内部，三买失败或主升降级 |

Algorithm gap:

```text
当前只看 current/previous center，不够稳定识别“大中枢上小中枢”。
需要 historical_centers 和跨级别 center nesting。
```

### 4.7 HISTORICAL_HIGH_PRESSURE

对应切片：澜起科技。

识别条件：

```text
L0 日线存在历史前高
当前价距离历史前高 <= 8%
或当前价已经突破历史前高
```

A/B/C：

| Scenario | Boundary |
|---|---|
| A | 放量突破历史前高后不快速跌回，另等短级别回踩确认 |
| B | 历史前高下方震荡消化，不破当前结构防线 |
| C | 前高附近放量滞涨、顶背驰或跌破短级别关键中枢 |

Algorithm rule:

```text
历史前高是价格记忆边界，不是当前中枢边界。
它可以进入 Pattern 和关键观察位，但不能单独覆盖当前 path / A/B/C。
若同时存在 BIG_CENTER_SMALL_CENTER_UP_BREAK 或 THIRD_BUY_RETEST_UP，以结构模板为主，历史前高作为风险观察。
```

### 4.8 SMALL_TURN_BIG_FAST_B2_B3

对应切片：东田微。

识别条件：

```text
下跌末端 B1P
随后一根或少数几根 K 线强势拉回中枢上方
B2/B2S 与 B3A 间隔很短
三买确认时已离低点较远
```

A/B/C：

| Scenario | Boundary |
|---|---|
| A | 突破修复中枢 `ZG` |
| B | B2/B2S 上方维持 |
| C | 跌破 B2/B2S，修复失败 |

Algorithm gap:

```text
不能要求所有底部修复都有标准慢二买。
需要 event_sequence 判断 B1P/B2/B2S/B3A 的压缩链条。
```

### 4.9 THIRD_BUY_FAST_SELL_RISK

对应切片：东方日升。

识别条件：

```text
三买后快速向上离开
很快出现 S1/S1P
反抽形成 S2/S2S
S2 未过 S1 前高
第二段上攻力度明显小于第一段
```

A/B/C：

| Scenario | Boundary |
|---|---|
| A | 三买后突破并完成一笔上攻 |
| B | 回落但不触发 S2 风险 |
| C | S2 未过前高且力度衰减 |

Algorithm gap:

```text
需要把 sell sequence 和 momentum_compare 加入 pattern。
三买成功后的卖点风险不能被误写成“三买失败”。
```

### 4.10 BIG_CENTER_SMALL_CENTER_PULLBACK_REPAIR

对应切片：赣锋锂业。

识别条件：

```text
离开大级别中枢后形成 L1 小中枢
尝试向上离开后出现卖点压力
价格跌回 L1 小中枢内部
随后 L2 出现 B1/B2/B2S/B3A 小转大拉回
但未完全突破前卖点压力
```

A/B/C：

| Scenario | Boundary |
|---|---|
| C first | 跌回 L1 小中枢内部，主升尝试失败 |
| A partial | L2 小转大重新站回小中枢上沿 |
| A full | 突破前卖点压力 |
| B | 修复中但卖点压力未化解 |

Algorithm gap:

```text
需要允许同一切片出现 C -> A_PARTIAL 的状态机。
当前单次 build 只能表达一个 path，缺少 transition history。
```

## 5. Path Versus Pattern

| Path | Pattern Examples | Action Bias |
|---|---|---|
| `UPWARD_MAJOR_WAVE` | `BIG_CENTER_SMALL_CENTER_UP_BREAK` | `HOLD_OR_TRAIL` |
| `PULLBACK_IN_UPTREND` | `MICRO_CONVERSION_BREAKOUT`, `PULLBACK_PARTIAL_RECLAIM`, `FAILED_PULLBACK_SECOND_BUY_REPAIR` | `WAIT_BREAKOUT` / `WAIT_RECLAIM` |
| `HIGH_VOLATILITY_OSCILLATION` | `THIRD_BUY_FAST_SELL_RISK`, `BIG_CENTER_SMALL_CENTER_PULLBACK_REPAIR` | `REDUCE_CHASING` |
| `CENTER_REBOUND` | `SMALL_TURN_BIG_FAST_B2_B3` | `WATCH_REBOUND` |
| `DOWNWARD_DEFENSE` | `B1_REPAIR_FAILURE` | `DEFENSIVE` |

Rule:

```text
path 是当前状态，pattern 是结构解释。
同一个 pattern 可以跨 path 演化。
例如 SMALL_TURN_BIG_FAST_B2_B3: CENTER_REBOUND -> UPWARD_MAJOR_WAVE。
```

## 6. Implementation Plan

### Phase 1: Pattern Extraction

新增纯函数：

```text
detect_patterns(atoms, freshness) -> list[Pattern]
```

输出：

```json
{
  "code": "BIG_CENTER_SMALL_CENTER_UP_BREAK",
  "confidence": "MEDIUM",
  "levels": ["day", "30", "5"],
  "evidence": [],
  "boundaries": {
    "confirm": [],
    "maintain": [],
    "invalidate": []
  }
}
```

### Phase 2: Pattern-Aware Boundaries

当前 `build_boundaries_v2` 只按 path 生成边界。下一步改成：

```text
base boundaries from path
pattern boundaries from top pattern
merge and deduplicate
```

合并规则：

```text
pattern 边界优先级高于 path 边界。
同 field/value/trigger 去重。
风险类 pattern 可以增加 invalidate，不直接删除 maintain。
```

### Phase 3: Transition State

新增字段：

```json
{
  "transition": {
    "from": "CENTER_REBOUND",
    "to": "UPWARD_MAJOR_WAVE",
    "status": "CONFIRMED",
    "trigger": "B3A above L1.ZG"
  }
}
```

用于表达：

```text
东田微: CENTER_REBOUND -> UPWARD_MAJOR_WAVE
赣锋锂业: C_TRIGGERED -> A_PARTIAL_TRIGGERED
```

### Phase 4: Regression Expansion

每个 pattern 至少一个 fixture：

```text
tests/test_radar_historical_slices.py
```

新增测试要求：

| Requirement | Meaning |
|---|---|
| Pattern lock | 固定 `pattern.code` |
| Boundary lock | 固定关键边界值和 role |
| Transition lock | 固定 path 迁移 |
| Future lock | 固定 A/B/C 触发结果 |

## 7. Current Status In Code

| Capability | Status | Slice Covered |
|---|---|---|
| 显式 pattern layer | Done | 全部 |
| `historical_centers` | Done | 新易盛、赣锋锂业 |
| `event_sequence` | Done | 东田微、东方日升 |
| sell sequence | Done | 东方日升 |
| `transition` | Done | 东田微、赣锋锂业 |
| 实时 `A_PARTIAL/A_FULL` | Done | 兆易创新、赣锋锂业 |
| `center_binding` | Done | 三买/三卖绑定中枢位置 |
| `leave_return_status` | Done | 澜起科技、赣锋锂业 |
| `momentum_compare` | Done | 澜起科技、东方日升 |

当前仍需补强：

| Gap | Impact | Slice Exposing It |
|---|---|---|
| `center_binding` 还未反向约束所有 pattern | detector 已可消费绑定信息，但部分模板仍用价格比较 | 陕西华达、东田微 |

## 8. Next Build Tasks

推荐按这个顺序做：

| Step | Task | Files | Status |
|---|---|---|
| 1 | 新增 pattern dataclass 和 detector skeleton | `server/engines/decision/radar_patterns.py` | Done |
| 2 | 实现 3 个最高价值 pattern | 新易盛、东方日升、赣锋锂业 | Done |
| 3 | 把 pattern 输出挂到 `algorithm_v2.patterns` | `radar_algorithm_v2.py` | Done |
| 4 | pattern-aware boundary merge | `radar_algorithm_v2.py` | Done |
| 5 | 新增 transition 输出 | `radar_patterns.py`, `radar_algorithm_v2.py` | Done |
| 6 | 扩展历史切片测试锁 `pattern.code` 和 `transition` | `tests/test_radar_historical_slices.py` | Done |
| 7 | 从 `LevelAtom` 派生更完整的 `event_sequence` | `radar_algorithm_v2.py` | Done |
| 8 | 接入 `historical_centers` 和跨级别 center nesting | `radar_algorithm_v2.py`, `radar_patterns.py` | Done |
| 9 | 引入实时 `A_PARTIAL/A_FULL` 字段 | `radar_algorithm_v2.py` | Done |
| 10 | 引入 `center_binding`，把买卖点绑定到当前/前中枢位置 | `radar_algorithm_v2.py` | Done |
| 11 | 引入 `leave_return_status` | `radar_algorithm_v2.py`, `radar_patterns.py` | Done |
| 12 | 引入 `momentum_compare` | `structure/divergence.py`, `radar_patterns.py` | Done |

优先实现 3 个：

```text
BIG_CENTER_SMALL_CENTER_UP_BREAK
THIRD_BUY_FAST_SELL_RISK
BIG_CENTER_SMALL_CENTER_PULLBACK_REPAIR
```

理由：

```text
它们直接对应“吃大肉”和“防过山车”。
它们也最能暴露现有 path-only 算法的不足。
```

## 9. Implemented Contract

当前 `algorithm_v2` 已输出：

```json
{
  "patterns": [
    {
      "code": "BIG_CENTER_SMALL_CENTER_UP_BREAK",
      "name": "大中枢上小中枢震荡后强势离开",
      "confidence": "HIGH",
      "path_hint": "UPWARD_MAJOR_WAVE",
      "evidence": []
    }
  ],
  "transition": {
    "from": "UPWARD_MAJOR_WAVE",
    "to": "UPWARD_MAJOR_WAVE",
    "status": "MAINTAINED",
    "trigger": "small center holds above big center and breaks upward",
    "pattern_code": "BIG_CENTER_SMALL_CENTER_UP_BREAK",
    "meaning": "大中枢上方小中枢向上离开，主升路径维持并强化。"
  }
}
```

同时已输出实时确认状态：

```json
{
  "a_state": "A_PARTIAL_TRIGGERED",
  "confirmation": {
    "state": "A_PARTIAL_TRIGGERED",
    "progress": 0.5,
    "matched": [
      {"field": "ZD", "value": 302.57}
    ],
    "unmatched": [
      {"field": "ZG", "value": 307.62}
    ],
    "meaning": "A 路径已经半确认，但仍有关键确认边界没有触发。"
  }
}
```

实时状态枚举：

| State | Meaning |
|---|---|
| `A_NOT_TRIGGERED` | A 路径尚未触发 |
| `A_PARTIAL_TRIGGERED` | 已触发部分确认边界，例如站回 ZD 但未突破 ZG |
| `A_FULL_TRIGGERED` | 已触发全部确认边界 |
| `B_MAINTAINED` | 当前仍属于 B 路径维持 |
| `C_TRIGGERED` | 当前已经触发失效边界 |

`atoms.L*.event_sequence` 已提供按时间排序的买卖点链条：

```json
[
  {
    "time": "2026-02-02 15:00:00",
    "side": "buy",
    "code": "B3A",
    "family": "THIRD_BUY",
    "price": 20.88,
    "is_current": true,
    "center_binding": {
      "primary": "current",
      "current": {
        "status": "above_zg",
        "zd": 18.0,
        "zg": 20.0,
        "distance_to_zd": 2.88,
        "distance_to_zg": 0.88
      },
      "previous": {
        "status": "above_zg",
        "zd": 12.0,
        "zg": 16.0,
        "distance_to_zd": 8.88,
        "distance_to_zg": 4.88
      }
    }
  },
  {
    "time": "2026-02-04 13:30:00",
    "side": "sell",
    "code": "S1",
    "family": "FIRST_SELL",
    "price": 25.98,
    "is_current": true
  }
]
```

`atoms.L*.center_binding` 同时提供按 `CODE@time` 索引的绑定结果，用于前端或 pattern detector 快速读取。

当前绑定状态枚举：

| Status | Meaning |
|---|---|
| `above_zg` | 事件价格在中枢上沿之上 |
| `inside` | 事件价格在中枢内部 |
| `below_zd` | 事件价格在中枢下沿之下 |
| `unknown` | 中枢或事件价格缺失 |

`atoms.L*.leave_return_status` 已提供离开/拉回状态：

```json
{
  "leave_return_status": {
    "status": "UP_RETURNED_TO_CENTER",
    "direction": "up",
    "has_left": true,
    "has_returned": true,
    "is_broken": false,
    "price": 19.0,
    "center_zd": 18.0,
    "center_zg": 20.0,
    "leave_extreme": 22.5
  }
}
```

当前状态枚举：

| Status | Meaning |
|---|---|
| `UP_LEAVING` | 向上离开中枢仍在中枢上方 |
| `UP_RETURNED_TO_CENTER` | 向上离开后跌回中枢内部 |
| `UP_RETURN_BROKEN` | 向上离开后跌穿中枢下沿 |
| `DOWN_LEAVING` | 向下离开中枢仍在中枢下方 |
| `DOWN_RETURNED_TO_CENTER` | 向下离开后拉回中枢内部 |
| `DOWN_RETURN_BROKEN` | 向下离开后突破中枢上沿 |
| `NO_LEAVE` | 当前没有可识别的离开行为 |
| `UNKNOWN` | 中枢或价格缺失 |

`atoms.L*.momentum_compare` 已提供最近同向笔和前强笔的力度比较：

```json
{
  "momentum_compare": {
    "direction": "up",
    "price_makes_extreme": false,
    "is_weaker": true,
    "area_ratio": 0.015,
    "dif_ratio": 0.1,
    "combined_score": 0.934,
    "previous": {
      "y1": 25.98,
      "momentum_metrics": {"area": 4.1426, "dif_extreme": 0.5}
    },
    "current": {
      "y1": 24.6,
      "momentum_metrics": {"area": 0.0635, "dif_extreme": 0.05}
    }
  }
}
```

使用原则：

| Field | Meaning |
|---|---|
| `area_ratio` | 当前同向笔 MACD 面积 / 前强同向笔 MACD 面积 |
| `dif_ratio` | 当前同向笔 DIF 极值 / 前强同向笔 DIF 极值 |
| `combined_score` | 力度衰减综合评分，越高表示衰减越明显 |
| `price_makes_extreme` | 当前同向笔是否创新高/新低 |
| `is_weaker` | 当前同向笔任一动能维度弱于前强笔 |

Pattern detectors now use `event_sequence` for order-sensitive templates:

| Pattern | Required Sequence |
|---|---|
| `THIRD_BUY_FAST_SELL_RISK` | `B3 -> S1 -> S2` |
| `SMALL_TURN_BIG_FAST_B2_B3` | `B1/B1P -> B2/B2S -> B3` |

`atoms.L*.historical_centers` 已提供最近中枢序列，`center_nesting` 已提供相邻级别中枢关系：

```json
{
  "center_nesting": {
    "L0_L1": {
      "relation": "CHILD_ABOVE_PARENT",
      "parent_level": "day",
      "child_level": "30",
      "parent_zg": 433.33,
      "child_zd": 442.86,
      "gap_to_parent_zg": 9.53
    },
    "L1_L2": {
      "relation": "CHILD_INSIDE_PARENT"
    }
  }
}
```

当前关系枚举：

| Relation | Meaning |
|---|---|
| `CHILD_ABOVE_PARENT` | 小级别中枢整体在大级别中枢上方 |
| `CHILD_BELOW_PARENT` | 小级别中枢整体在大级别中枢下方 |
| `CHILD_INSIDE_PARENT` | 小级别中枢包含在大级别中枢内部 |
| `PARENT_INSIDE_CHILD` | 大级别中枢包含在小级别区间内部 |
| `OVERLAP` | 两个中枢有重叠但没有包含 |
| `UNKNOWN` | 任一中枢缺失或无效 |

已支持的 transition：

| Pattern | Transition |
|---|---|
| `BIG_CENTER_SMALL_CENTER_UP_BREAK` | `UPWARD_MAJOR_WAVE -> UPWARD_MAJOR_WAVE / MAINTAINED` |
| `THIRD_BUY_FAST_SELL_RISK` | `UPWARD_MAJOR_WAVE -> HIGH_VOLATILITY_OSCILLATION / RISK` |
| `BIG_CENTER_SMALL_CENTER_PULLBACK_REPAIR` | `C_TRIGGERED -> A_PARTIAL_TRIGGERED / PARTIAL` |
| `FAILED_PULLBACK_SECOND_BUY_REPAIR` | `C_TRIGGERED -> BOTTOM_REPAIR / WATCH` |
| `SMALL_TURN_BIG_FAST_B2_B3` | `CENTER_REBOUND -> UPWARD_MAJOR_WAVE / CONFIRMED` |
| `MICRO_CONVERSION_BREAKOUT` | `PULLBACK_IN_UPTREND -> UPWARD_MAJOR_WAVE / PENDING` |
