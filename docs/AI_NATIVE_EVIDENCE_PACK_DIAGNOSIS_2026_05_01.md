# AI Native Evidence Pack 排查记录

日期：2026-05-01

目标：排除 AI 推演偏差来源。这里不调用 LLM，只检查当前系统真实发给 LLM 的 commander Evidence Pack，和 Gemini 黄金案例所需字段是否一致。

## 结论

第一层已经排出：**当前 commander Evidence Pack 和黄金案例包不一致，是主要偏差来源。**

- 汇川、澜起、江特、天孚：黄金主价位大量缺失，Prompt 和模型无法凭空复原。
- 瑞芯微：黄金价位基本存在，所以当前 DeepSeek 返回最接近黄金案例。
- 兆易创新：黄金价位存在，但黄金语义断言缺失，同时卖点压力太近，主叙事被抢走。

## 字段存在性总表

| 股票 | zone 是否一致 | 黄金价位存在情况 | 黄金语义断言 | 判定 |
|---|---|---|---|---|
| 汇川技术 `sz.300124` | 否 `between_nearest_support_and_resistance` | 2/5 | 0/2 | 缺字段为主 |
| 澜起科技 `sh.688008` | 否 `between_nearest_support_and_resistance` | 1/5 | 0/1 | 缺字段为主 |
| 瑞芯微 `sh.603893` | 否 `between_nearest_support_and_resistance` | 4/5 | 0/1 | 语义断言/优先级问题 |
| 兆易创新 `sh.603986` | 否 `between_nearest_support_and_resistance` | 4/5 | 0/2 | 语义断言/优先级问题 |
| 江特电机 `sz.002176` | 否 `price_structure_gap` | 1/5 | 0/0 | 缺字段为主 |
| 天孚通信 `sz.300394` | 否 `between_nearest_support_and_resistance` | 3/6 | 0/0 | 优先级问题为主 |

## 逐只排查

### 汇川技术 `sz.300124`

- 黄金 zone：`REBOUND_INTO_30M_ZHONGSHU`
- 当前 zone：`between_nearest_support_and_resistance`
- 黄金价位检查：
  - `66.38`：缺失
  - `68.63`：存在，位置：basic_anchors.allowed_prices[114].value, basic_anchors.allowed_prices[141].value, levels.30.recent_bis[0].from, operative_context.immediate_resistances[0].price
  - `66.98`：缺失
  - `65.53`：缺失
  - `65.3`：存在，位置：basic_anchors.allowed_prices[123].value, basic_anchors.allowed_prices[158].value, basic_anchors.allowed_prices[179].value, levels.30.recent_bis[4].to...
- 黄金语义断言检查：
  - `MACD_BOTTOM_DIVERGENCE_CONFIRMED`：缺失
  - `FIRST_BUY_POINT_TRIGGERED`：缺失
- 初步判断：黄金主叙事所需的 66.38、66.98、65.53 不在当前 commander pack；68.63 存在但只是最近压力。语义断言全缺。当前系统只能写成 68.5 附近压力震荡。

### 澜起科技 `sh.688008`

- 黄金 zone：`REBOUND_BETWEEN_TWO_5M_ZHONGSHUS`
- 当前 zone：`between_nearest_support_and_resistance`
- 黄金价位检查：
  - `170.35`：缺失
  - `175.51`：缺失
  - `168.5`：缺失
  - `165.42`：缺失
  - `180.0`：存在，位置：basic_anchors.allowed_prices[73].value, basic_anchors.allowed_prices[100].value, levels.60.recent_bsp_events[5].price, operative_context.immediate_resistances[0].price
- 黄金语义断言检查：
  - `REBOUND_TESTING_THIRD_SELL`：缺失
- 初步判断：黄金核心 170.35/175.51/168.50/165.42 全缺，只剩 180.00 附近旧压力。当前包自然无法写“两 5m 中枢之间的颈线战”。

### 瑞芯微 `sh.603893`

- 黄金 zone：`MACRO_BREAKOUT_EDGE`
- 当前 zone：`between_nearest_support_and_resistance`
- 黄金价位检查：
  - `180.37`：存在，位置：basic_anchors.allowed_prices[18].value, basic_anchors.allowed_prices[32].value, basic_anchors.allowed_prices[33].value, levels.day.center.zg...
  - `177.3`：存在，位置：basic_anchors.allowed_prices[135].value, basic_anchors.allowed_prices[144].value, basic_anchors.allowed_prices[145].value, basic_anchors.allowed_prices[148].value...
  - `187.58`：存在，位置：basic_anchors.allowed_prices[20].value, basic_anchors.allowed_prices[28].value, basic_anchors.allowed_prices[29].value, levels.day.center.gg...
  - `173.13`：缺失
  - `174.35`：存在，位置：basic_anchors.allowed_prices[91].value, basic_anchors.allowed_prices[92].value, basic_anchors.allowed_prices[101].value, levels.30.recent_bis[1].to...
- 黄金语义断言检查：
  - `ATTEMPTING_MACRO_BREAKOUT`：缺失
- 初步判断：黄金价位 180.37/177.30/187.58 大多存在，只有 current_zone 和 ATTEMPTING_MACRO_BREAKOUT 缺失。因此瑞芯微返回最接近黄金案例。

### 兆易创新 `sh.603986`

- 黄金 zone：`RETRACE_TESTING_3RD_BUY`
- 当前 zone：`between_nearest_support_and_resistance`
- 黄金价位检查：
  - `309.75`：存在，位置：basic_anchors.allowed_prices[143].value, basic_anchors.allowed_prices[151].value, basic_anchors.allowed_prices[152].value, basic_anchors.allowed_prices[154].value...
  - `304.05`：存在，位置：basic_anchors.allowed_prices[153].value, levels.5.recent_bis[4].to
  - `302.57`：存在，位置：basic_anchors.allowed_prices[136].value, basic_anchors.allowed_prices[159].value, basic_anchors.allowed_prices[160].value, basic_anchors.allowed_prices[162].value...
  - `307.62`：存在，位置：basic_anchors.allowed_prices[135].value, basic_anchors.allowed_prices[155].value, basic_anchors.allowed_prices[158].value, levels.5.center.zg
  - `321.0`：缺失
- 黄金语义断言检查：
  - `BREAKOUT_PULLBACK`：缺失
  - `POTENTIAL_THIRD_BUY_FORMING`：缺失
- 初步判断：黄金价位 309.75/304.05/302.57/307.62 存在，但语义断言 BREAKOUT_PULLBACK/POTENTIAL_THIRD_BUY_FORMING 缺失，且 313.65/313.68 卖点成为最近压力，抢走主叙事。

### 江特电机 `sz.002176`

- 黄金 zone：`EXTREME_ABOVE_ALL_STRUCTURES`
- 当前 zone：`price_structure_gap`
- 黄金价位检查：
  - `15.8`：缺失
  - `14.68`：缺失
  - `14.25`：缺失
  - `14.14`：缺失
  - `11.02`：存在，位置：basic_anchors.allowed_prices[24].value, basic_anchors.allowed_prices[38].value, basic_anchors.allowed_prices[93].value, levels.day.center.zg...
- 黄金语义断言检查：黄金案例未要求。
- 初步判断：黄金动态防线 15.80/14.68/14.25/14.14 全缺，仅有远端 11.02。结构真空状态有，但缺少近端 trailing defense。

### 天孚通信 `sz.300394`

- 黄金 zone：`BREAKDOWN_BELOW_30M`
- 当前 zone：`between_nearest_support_and_resistance`
- 黄金价位检查：
  - `310.01`：缺失
  - `313.99`：缺失
  - `307.83`：缺失
  - `325.68`：存在，位置：basic_anchors.allowed_prices[77].value, basic_anchors.allowed_prices[86].value, basic_anchors.allowed_prices[87].value, basic_anchors.allowed_prices[91].value...
  - `336.0`：存在，位置：basic_anchors.allowed_prices[90].value, basic_anchors.allowed_prices[111].value, levels.30.center.zg, levels.30.recent_bis[0].from
  - `228.0`：存在，位置：basic_anchors.allowed_prices[26].value, basic_anchors.allowed_prices[46].value, levels.day.center.zg, levels.day.recent_bis[0].from...
- 黄金语义断言检查：黄金案例未要求。
- 初步判断：黄金深支撑 228.00 与 30m 中枢 325.68/336 存在，但近端 310.01/313.99/307.83 缺失，当前包用 314.88/298.33/290.55 替代。

## 下一步排除顺序

1. 先修 commander Evidence Pack，不碰模型：让它显式产出 `primary_context`、`must_use_levels`、`semantic_assertions`。
2. 对汇川先做最小闭环：当前结构数据里如果找不到 66.38/66.98/65.53，就查上游结构快照为什么变了。
3. 对兆易做优先级实验：保留当前价位，但加入 `RETRACE_TESTING_3RD_BUY` 与 `POTENTIAL_THIRD_BUY_FORMING`，看 DeepSeek 是否从顶部压力切回回踩三买。
4. 等 Evidence Pack 固定后，再做 DeepSeek/Gemini 同包 A/B。
