# Radar Algorithm Whitepaper v0.1

本文档定义 CT-OS 雷达算法的理论框架。它不是 UI 方案，也不是交易建议生成器。雷达算法的目标是把 CChan 结构事实组织成可验证的走势推演：

```text
不预测涨跌，只做完全分类。
不输出买卖命令，只输出路径、边界、触发和失效条件。
```

本文档仅供产品与工程设计参考。涉及市场判断的内容均仅供参考，不构成投资建议。

## 1. 理论依据

雷达算法以缠论的几个核心概念为底层约束：

| 缠论概念 | 算法含义 |
|---|---|
| 走势类型 | 上涨、下跌、盘整 |
| 走势终完美 | 任何当前走势都在未完成到完成之间演化 |
| 走势中枢 | 以 ZD/ZG/GG/DD 描述结构边界 |
| 中枢延伸、新生、扩张 | 判断趋势延续、震荡升级或级别扩大 |
| 三类买卖点 | 一买/二买/三买，一卖/二卖/三卖 |
| 背驰 | 力度衰竭证据，不单独构成买卖命令 |
| 级别 | 背景级别、结构级别、触发级别递归联立 |

雷达路径分类不是缠论本体。缠论本体是走势类型、中枢关系、买卖点、背驰和级别。雷达路径只是把这些底层事实翻译成实盘可读的推演状态。

## 2. 总流水线

```text
CChan 结构事实
  -> 单级别缠论原子状态
  -> 多级别联立
  -> 主路径分类
  -> A/B/C 完全分类
  -> 边界、背驰、权重修正
  -> 雷达算法输出
```

每一层只做一件事：

| 层级 | 职责 |
|---|---|
| 事实层 | 读取价格、中枢、买卖点、背驰、级别新鲜度 |
| 原子状态层 | 判断单级别处于中枢内、向上离开、向下离开、回试或反抽 |
| 联立层 | 判断 L0/L1/L2 的关系 |
| 路径层 | 归入七类主路径 |
| 推演层 | 生成 A/B/C 三条互斥路径 |
| 风控层 | 输出边界、触发、失效、权重解释 |

## 3. 级别抽象

算法不应写死 day/30/5，而应使用三级抽象：

| 抽象级别 | 职责 | 当前默认 |
|---|---|---|
| L0 context_level | 背景级别，定大局 | day |
| L1 structure_level | 结构级别，定边界 | 30 |
| L2 trigger_level | 触发级别，定事件 | 5 |

同一套算法可以递归到：

```text
week -> day -> 30
day -> 30 -> 5
30 -> 5 -> 1
```

这使得盘后复盘和 QMT 实时盯盘可以共用同一个算法骨架。

## 4. 单级别原子状态

每个级别先独立判断，不能一开始就混合 day/30/5。

### 4.1 位置状态

| 状态 | 含义 |
|---|---|
| CENTER_INSIDE | 价格在中枢 ZD/ZG 内 |
| UP_LEAVING | 价格在 ZG 上方，向上离开 |
| DOWN_LEAVING | 价格在 ZD 下方，向下离开 |
| UP_RETEST | 向上离开后回试中枢上沿 |
| DOWN_PULLBACK | 向下离开后反抽中枢下沿 |
| UNKNOWN | 数据不足或结构不可判定 |

### 4.2 事件标签

事件标签不与位置状态互斥：

| 标签 | 含义 |
|---|---|
| BUY_SIGNAL | 最新窗口有一买/二买/三买 |
| SELL_SIGNAL | 最新窗口有一卖/二卖/三卖 |
| BOTTOM_DIVERGENCE | 底背驰 |
| TOP_DIVERGENCE | 顶背驰 |
| THIRD_BUY | 三买确认或形成中 |
| THIRD_SELL | 三卖确认或形成中 |

## 5. 中枢演化

当前雷达不能只看一个中枢。至少需要：

```text
current_center
previous_center
historical_centers
```

推荐结构：

```json
{
  "current_center": {
    "zd": 69.98,
    "zg": 75.48,
    "dd": 56.27,
    "gg": 82.59,
    "start": "...",
    "end": "..."
  },
  "previous_center": {
    "zd": 56.27,
    "zg": 65.05,
    "dd": 52.10,
    "gg": 68.20
  }
}
```

### 5.1 中枢关系

设 `prev` 为前中枢，`curr` 为当前中枢：

| 关系 | 判定 | 含义 |
|---|---|---|
| UP_NEWBORN | `curr.ZD > prev.ZG` | 向上新生，趋势可能延续 |
| DOWN_NEWBORN | `curr.ZG < prev.ZD` | 向下新生，下跌可能延续 |
| EXPANSION | `curr` 与 `prev` 有重叠 | 可能形成更大级别中枢 |
| EXTENSION | 价格持续围绕当前中枢波动 | 原中枢延伸 |

### 5.2 第三类买卖点与中枢

第三类买点：

```text
price 离开中枢上方
retest_low > center.ZG
CChan BSP in B3A/B3B
```

第三类卖点：

```text
price 离开中枢下方
rebound_high < center.ZD
CChan BSP in S3
```

三买是延续确认，不是底部转折。三卖是下跌延续确认。

## 6. 三类买卖点

### 6.1 买点

| 买点 | 算法含义 | 路径作用 |
|---|---|---|
| B1 / B1P | 下跌或盘整下方的转折线索 | BOTTOM_REPAIR_PREVIEW |
| B2 / B2S | 一买后的回踩不破确认 | BOTTOM_REPAIR |
| B3A / B3B | 离开中枢后回试不破 | UPWARD_MAJOR_WAVE 或 PULLBACK 修复 |

### 6.2 卖点

| 卖点 | 算法含义 | 路径作用 |
|---|---|---|
| S1 / S1P | 上涨或盘整上方的风险线索 | HIGH_VOLATILITY |
| S2 / S2S | 一卖后的反抽不强确认 | 高位风险确认 |
| S3 | 跌破中枢后反抽不回 | DOWNWARD_DEFENSE |

### 6.3 买卖点纪律

```text
一买/一卖 = 转折线索
二买/二卖 = 转折确认
三买/三卖 = 延续确认
```

买卖点必须绑定最近中枢，并输出成立条件和失效条件。

## 7. 多级别联立

| L0/L1/L2 关系 | 主路径 |
|---|---|
| L0 强，L1 强，L2 强 | UPWARD_MAJOR_WAVE |
| L0 强，L1 强，L2 高位乱 | HIGH_VOLATILITY_OSCILLATION |
| L0 强，L1 强，L2 弱 | PULLBACK_IN_UPTREND |
| L0 弱，L1 弱，L2 弱 | DOWNWARD_DEFENSE |
| L0 弱，L1 弱，L2 修复 | BOTTOM_REPAIR |
| L0 中枢内，L1/L2 修复 | CENTER_REBOUND |
| 无清晰主次 | NO_EDGE |

核心纪律：

```text
L0 定背景，L1 定结构，L2 定触发。
L2 不能单独推翻 L0。
路径切换必须经过 L1 边界或 L1 买卖点确认。
```

## 8. 七类主路径

### 8.1 UPWARD_MAJOR_WAVE

主升延伸。多级别处于中枢上方或向上离开，短级别没有明显破坏。

准入：

```text
L0 强
L1 支持
L2 强
无明确顶背驰
```

纪律：

```text
趋势没坏不猜顶。
跌破 L2 上一中枢 ZG 后降级为回落验证。
```

### 8.2 HIGH_VOLATILITY_OSCILLATION

高位剧震。大级别仍强，但短级别出现卖点、冲高回落、宽幅震荡或潜在顶背驰。

纪律：

```text
高位不猜顶，但防利润回撤。
守住 L2 ZG 是高位震荡，跌破关键 L1 边界才升级防守。
```

### 8.3 PULLBACK_IN_UPTREND

上升趋势回落验证。L0/L1 仍强，L2 已经走弱，但 L1 防线未破。

纪律：

```text
L2 破不等于趋势坏。
L1 破才是趋势坏。
```

### 8.4 DOWNWARD_DEFENSE

向下离开防守。多级别跌破主要中枢或处于向下离开段。

纪律：

```text
多级别向下，不接飞刀。
一买只是线索，二买或站回中枢才是修复。
```

### 8.5 BOTTOM_REPAIR

底部修复。弱背景下，L2 出现底背驰、一买后不创新低、二买或站回短级别中枢。

纪律：

```text
底背驰是力度衰竭，不是买入命令。
B2 才是修复确认。
```

### 8.6 CENTER_REBOUND

中枢内反弹。L0 在中枢内，L1/L2 先修复，推演从中枢下沿到上沿。

纪律：

```text
中枢内反弹不等于主升。
突破并回试 L0 ZG 不破，才升级主升。
```

### 8.7 NO_EDGE

无清晰优势。结构事实不足、级别冲突或边界不可验证。

纪律：

```text
看不清就闭嘴。
NO_EDGE 不输出推演权重。
```

## 9. 路径判定优先级

```text
if data_invalid:
    path = NO_EDGE
elif is_downward_defense():
    if repair_confirmed_strong():
        path = BOTTOM_REPAIR
    else:
        path = DOWNWARD_DEFENSE
elif is_high_volatility():
    path = HIGH_VOLATILITY_OSCILLATION
elif is_pullback_in_uptrend():
    path = PULLBACK_IN_UPTREND
elif is_upward_major_wave():
    path = UPWARD_MAJOR_WAVE
elif is_bottom_repair():
    path = BOTTOM_REPAIR
elif is_center_rebound():
    path = CENTER_REBOUND
else:
    path = NO_EDGE
```

优先级铁律：

1. 数据无效高于一切。
2. 破位防守高于买点。
3. 高位剧震高于主升。
4. 小级别破坏高于趋势延伸。
5. 中枢内反弹不能升级为主升，除非突破并回试不破。

## 10. 状态机

```text
DOWNWARD_DEFENSE
  -> BOTTOM_REPAIR
  -> NO_EDGE

BOTTOM_REPAIR
  -> CENTER_REBOUND
  -> DOWNWARD_DEFENSE
  -> NO_EDGE

CENTER_REBOUND
  -> UPWARD_MAJOR_WAVE
  -> DOWNWARD_DEFENSE
  -> NO_EDGE

UPWARD_MAJOR_WAVE
  -> HIGH_VOLATILITY_OSCILLATION
  -> PULLBACK_IN_UPTREND
  -> DOWNWARD_DEFENSE

PULLBACK_IN_UPTREND
  -> UPWARD_MAJOR_WAVE
  -> HIGH_VOLATILITY_OSCILLATION
  -> DOWNWARD_DEFENSE

HIGH_VOLATILITY_OSCILLATION
  -> UPWARD_MAJOR_WAVE
  -> PULLBACK_IN_UPTREND
  -> DOWNWARD_DEFENSE

NO_EDGE
  -> 任一明确路径
```

状态机铁律：

1. 一买是线索，二买是修复，站回中枢是确认。
2. 主升先降级，不直接判死。
3. 回落失败后，不再找买点，先防守。
4. 高位震荡要重新证明自己。
5. 数据失效直接切 NO_EDGE。

## 11. A/B/C 完全分类

雷达不预测，只做完全分类。

| 场景 | 含义 |
|---|---|
| A | 有利路径确认或延续 |
| B | 震荡、延长、等待 |
| C | 当前主推演失败或防守 |

完全分类要求：

```text
A/B/C 覆盖所有后续走势
A/B/C 互斥
每条路径有确认条件
每条路径有失效条件
触发后能切换到新主路径
```

示例：

| 当前路径 | A | B | C |
|---|---|---|---|
| UPWARD_MAJOR_WAVE | 趋势延伸 | 高位构建新中枢 | 顶背驰/跌破防线 |
| DOWNWARD_DEFENSE | 底部修复确认 | 底部震荡预览 | 下跌继续延伸 |
| CENTER_REBOUND | 突破 L0 ZG | 中枢内震荡 | 跌破 L0 ZD |
| HIGH_VOLATILITY | 突破前高不背驰 | 高位震荡 | 顶背驰/破防线 |
| PULLBACK_IN_UPTREND | L2 重新转强 | 回落验证 | 跌破 L1 防线 |

## 12. 边界计算

每条路径至少输出：

```text
support_boundary
resistance_boundary
failure_boundary
upgrade_boundary
```

边界必须来自结构事实：

| 来源 | 用途 |
|---|---|
| L2 ZD/ZG | 短线触发、第一压力/防线 |
| L1 ZD/ZG | 结构确认/失效 |
| L0 ZD/ZG | 大局切换 |
| 买点低点 | B1/B2/B3 防线 |
| 卖点高点 | S1/S2 压力 |
| 历史中枢 | 历史支撑/压力 |
| 前高/前低 | 突破/破位确认 |

边界纪律：

1. 路径决定边界角色。
2. L2 边界用于触发，L1 边界用于确认/失效，L0 边界用于大局切换。
3. 三买低点优先于旧中枢 ZD。
4. 向下防守先看压力，不先看支撑幻想。
5. 历史中枢必须作为补充支撑/压力。

## 13. 背驰算法

背驰不是买卖命令，而是力度衰竭证据。

```text
单个背驰只改权重，不切路径。
背驰 + 一买/一卖 = 预览。
背驰 + 二买/二卖 + 边界确认 = 路径切换。
高级别背驰优先于低级别触发。
```

推荐事实结构：

```json
{
  "level": "5",
  "direction": "bottom",
  "status": "confirmed",
  "strength": "medium",
  "related_bsp": "1",
  "price": 63.63,
  "time": "2026-04-24 15:00:00"
}
```

## 14. 推演权重

权重不是预测概率，而是路径优先级。

基础权重：

| 路径 | A | B | C |
|---|---:|---:|---:|
| UPWARD_MAJOR_WAVE | 60 | 30 | 10 |
| HIGH_VOLATILITY_OSCILLATION | 30 | 50 | 20 |
| PULLBACK_IN_UPTREND | 35 | 45 | 20 |
| DOWNWARD_DEFENSE | 20 | 30 | 50 |
| BOTTOM_REPAIR | 35 | 40 | 25 |
| CENTER_REBOUND | 30 | 45 | 25 |
| NO_EDGE | - | - | - |

权重流程：

```text
1. 先判路径
2. 读取基础权重
3. 应用背驰修正
4. 应用边界距离修正
5. 应用级别一致性修正
6. clamp 每项 5-80
7. normalize 到 100
8. 输出权重解释
```

硬切换优先于权重修正。

## 15. 样本校验

| 股票 | 目标路径 | 核心理由 |
|---|---|---|
| 002176 | UPWARD_MAJOR_WAVE | 多级别中枢上方，强势离开 |
| 澜起科技 | HIGH_VOLATILITY_OSCILLATION | 日线强，短级别高位剧震 |
| 兆易创新 | PULLBACK_IN_UPTREND | 日线强，30 分支持，5 分走弱 |
| 汇川技术 | DOWNWARD_DEFENSE | 日/30/5 多级别向下离开 |
| 捷佳伟创 | DOWNWARD_DEFENSE | 三层向下，买点只是止跌线索 |
| 长春燃气 | CENTER_REBOUND | 日线中枢内，小级别修复 |

## 16. 待补能力

1. 历史中枢列表与历史支撑/压力识别。
2. 中枢延伸、新生、扩张的结构化输出。
3. BSP 与最近中枢的绑定关系。
4. 背驰强度分级。
5. 自同构级别参数化，支持 week/day/30、day/30/5、30/5/1。
6. 权重解释字段。
7. NO_EDGE 的边界观察输出。

## 17. 总结

雷达算法的核心不是给结论，而是把走势放入可验证的路径状态机。

```text
缠论原子状态
  -> 中枢演化
  -> 多级别联立
  -> 七类路径
  -> A/B/C 完全分类
  -> 边界与触发
```

最终目标：

```text
让用户知道现在是什么局，
接下来发生什么才转强，
发生什么只是延长，
发生什么必须放弃。
```

仅供参考，不构成投资建议。
