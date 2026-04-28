# CChan Layer And Config Plan

> Created: 2026-04-26
> Status: planning
> Scope: Kline CChan display layers, algorithm config visibility, and inspector UX

## Premise

The Kline view already has a useful layer system. It controls what is drawn on the chart:

```text
笔 / 线段 / 笔中枢 / 段中枢 / 买卖点 / 区间套投影 / 背驰辅助 / 防线 / 走势切分
```

But this is not enough to build trust in CChan. Display switches answer:

```text
我想看什么？
```

CChan config answers:

```text
为什么算法这样画？
```

These must be separated in the UI and in the API contract.

## Existing Foundation

Relevant files:

- `web/src/store/layerState.js`
- `web/src/components/LayerPanel.jsx`
- `web/src/components/KlineChart.jsx`
- `web/src/plugins/chanOverlay.js`
- `server/engines/structure/chan_adapter.py`
- `server/services/chan_detail_service.py`

Existing layer keys:

| Key | Meaning | Default |
|---|---|---|
| `bi` | 笔 | on |
| `seg` | 线段 | on |
| `bi_zs` | 笔中枢 | on |
| `bi_zs_decomp` | 笔中枢，同级别分解 | off |
| `seg_zs` | 段中枢 | on |
| `bsp` | 买卖点 | on |
| `ma` | 主图指标 | on |
| `vol` | 成交量 | on |
| `macd` | 副图指标窗格 | on |
| `projection` | 区间套投影 | off |
| `momentum_compare` | 背驰辅助 | off |
| `support_wall` | 防线预警 | off |
| `decomp_grid` | 走势切分 | off |

Existing presets:

| Preset | Meaning |
|---|---|
| `naked` | 裸K，只保留成交量 |
| `standard` | 标准缠论显示 |
| `full` | 全标注，额外开启背驰辅助和防线 |

Existing mutual exclusions:

- `bi_zs` and `bi_zs_decomp`
- `projection` and `decomp_grid`

## Current CChan Runtime Config

The formal structure adapter currently creates `CChanConfig` with:

```python
{
    "trigger_step": True,
    "kl_data_check": False,
    "bi_strict": False,
    "bi_fx_check": "loss",
    "gap_as_kl": True,
    "print_warning": False,
    "print_err_time": False,
    "auto_skip_illegal_sub_lv": True,
}
```

This is an execution choice. It means the current formal structure is not strict textbook mode. It is a more tolerant mode intended to survive real market data.

## Product Goal

Add CChan controls without turning the chart toolbar into a cockpit.

The user should be able to:

1. Toggle visual layers quickly while watching the chart.
2. See the active CChan algorithm config at all times.
3. Switch between safe preset configs.
4. Filter buy/sell point types.
5. Click a drawn structure and inspect its source facts.

Non-goals for first version:

- Arbitrary raw config editing.
- User-defined formulas.
- Per-symbol persistent algorithm tuning.
- QMT realtime mutation of CChan config.
- LLM explanation before structured facts are visible.

## UI Model

Split current `LayerPanel` into three tabs inside the same popover:

```text
┌──────────────────────────────┐
│ 图层控制                      │
├────────┬────────┬────────────┤
│ 显示   │ 买卖点 │ 算法        │
├────────┴────────┴────────────┤
│ tab content                   │
└──────────────────────────────┘
```

### Tab 1: 显示

Keep the existing fast toggles here.

Sections:

```text
预设
  裸K / 标准 / 全标注

结构
  笔
  线段
  笔中枢
  笔中枢(分解)
  段中枢

辅助
  区间套投影
  背驰辅助
  防线预警
  走势切分

指标
  均线
  成交量
  副图
```

Rename `macd` display text to `副图`. Keep the storage key in v1 to avoid migration churn.

### Tab 2: 买卖点

Do not leave buy/sell point display as one coarse `bsp` toggle.

Add filters:

```text
方向
  买点 / 卖点

类型
  1 / 1p / 2 / 2s / 3a / 3b
```

Recommended default:

```json
{
  "bsp": true,
  "bsp_buy": true,
  "bsp_sell": true,
  "bsp_types": ["1", "1p", "2", "2s", "3a", "3b"]
}
```

Chart behavior:

- `bsp=false` hides all buy/sell point markers.
- `bsp=true` plus filters decides which markers render.
- Empty type selection should render no BSP markers, not fall back to all.

### Tab 3: 算法

This is a config visibility and preset selection panel, not an advanced editor.

Top summary:

```text
当前：实盘容错
笔：宽松 / 分型：loss / 跳空：当K线 / 中枢：合并 / 逐K推进：开启
```

Preset buttons:

| Preset | Purpose |
|---|---|
| `live_tolerant` | 实盘容错，current behavior |
| `textbook_strict` | 严格验算，用于学习和排错 |
| `sensitive_probe` | 更敏感，用于观察可能形成的结构 |

First version should only expose presets. Raw advanced fields can be read-only.

Config fields to display:

| Field | Display |
|---|---|
| `trigger_step` | 逐K推进 |
| `bi_strict` | 笔严格 |
| `bi_fx_check` | 分型检查 |
| `gap_as_kl` | 跳空处理 |
| `seg_algo` | 线段算法 |
| `left_seg_method` | 未确认线段处理 |
| `zs_combine` | 中枢合并 |
| `zs_algo` | 中枢算法 |
| `bs_type` | 买卖点类型 |
| `macd_algo` | 背驰算法 |
| `divergence_rate` | 背驰阈值 |

## API Contract

Add config metadata to `chan/detail` and `radar` responses.

For `chan/detail`:

```json
{
  "data": {
    "config": {
      "preset": "live_tolerant",
      "version": "cchan_config.v1",
      "effective": {
        "trigger_step": true,
        "bi_strict": false,
        "bi_fx_check": "loss",
        "gap_as_kl": true,
        "seg_algo": "chan",
        "left_seg_method": "peak",
        "zs_combine": true,
        "zs_algo": "normal",
        "bs_type": ["1", "1p", "2", "2s", "3a", "3b"],
        "macd_algo": "peak",
        "divergence_rate": "inf"
      }
    }
  }
}
```

For `/api/radar/{symbol}`:

```json
{
  "data": {
    "structure_config": {
      "preset": "live_tolerant",
      "version": "cchan_config.v1"
    }
  }
}
```

Radar should not accept arbitrary config query params in v1. The Kline inspector can later expose side-by-side strict vs live comparison.

## Backend Architecture

Add one small config module:

```text
server/engines/structure/chan_config_presets.py
```

Responsibilities:

- define allowed presets
- return dict for `CChanConfig`
- return public metadata for API responses
- validate preset names

Do not duplicate config literals in `chan_adapter.py` and `chan_detail_service.py`.

Data flow:

```text
request
  |
  v
resolve preset
  |
  +--> CChanConfig dict
  |
  +--> public config metadata
  |
  v
chan.py
  |
  v
serialized structure + config metadata
```

## Frontend Architecture

Extend `layerState.js`:

```js
{
  // existing display keys...
  bsp_buy: true,
  bsp_sell: true,
  bsp_types: ['1', '1p', '2', '2s', '3a', '3b'],
  cchan_preset: 'live_tolerant',
}
```

Important compatibility rule:

```text
Old localStorage must still load.
Missing new fields get defaults.
```

Update `chanOverlay.js` BSP filtering before marker creation:

```text
if !vis.bsp -> skip
if is_buy and !vis.bsp_buy -> skip
if !is_buy and !vis.bsp_sell -> skip
if type not in vis.bsp_types -> skip
```

Update `KlineChart.jsx`:

- Pass `cchan_preset` to `/chan/detail`.
- Re-fetch structure when `cchan_preset` changes.
- Do not re-fetch for pure display toggles.
- Show active preset/config summary in toolbar stats or LayerPanel algorithm tab.

## Inspector Mode

After config and filtering exist, add structure inspector.

Minimum v1:

- Click or hover cannot rely on existing overlay events because current overlays use `checkEventOn: () => false`.
- First version can use a right-side "Latest Structure" panel instead of true click selection.
- Show:
  - latest BSP
  - latest active Zhongshu
  - latest Bi
  - latest Seg
  - current CChan config

Click-to-inspect can be v2 after overlay hit-testing is added.

## Button And Switch Layout

Keep top toolbar simple:

```text
[周线][日线][60分][30分][15分][5分] [MA][BOLL][无] [MACD][KDJ][RSI]     [DeepSeek 推演] [2500根][146笔] [图层]
```

Only one icon button opens the popover:

```text
📐
```

Popover content:

```text
图层控制
[显示] [买卖点] [算法]

显示 tab:
  preset segmented controls
  compact toggles

买卖点 tab:
  direction segmented control
  type chips

算法 tab:
  preset segmented control
  read-only config summary
```

Use controls by function:

- Presets: segmented buttons.
- Binary layer visibility: toggles.
- BSP type filters: chips with selected state.
- CChan preset: segmented buttons.
- Raw config fields: read-only key/value rows.

Do not put every algorithm field as a switch. That creates false precision and makes the user think tuning is normal. Presets first.

## Edge Cases

- If CChan preset is invalid, backend returns 400 with allowed presets.
- If localStorage has invalid `bsp_types`, reset to defaults.
- If no BSP matches filters, chart should stay clean and stats should still show total BSP count.
- If strict preset returns fewer structures, UI should make the preset visible so the user knows why.
- If `projection` is enabled on `week`, silently show no projection because week has no parent level.
- If `DESIGN.md` is still absent, use existing dark panel styles only.

## Test Plan

Backend:

- `test_chan_config_presets.py`
  - default preset returns current config
  - invalid preset rejected
  - public metadata excludes private/vendor-only objects
- `test_chan_detail_api.py`
  - response includes config metadata
  - preset query param changes metadata
  - invalid preset returns stable error
- `test_radar_api.py`
  - radar response includes structure config summary

Frontend:

- `layerState` default merge keeps old localStorage compatible.
- BSP filter hides sell points when `bsp_sell=false`.
- BSP filter hides unselected types.
- CChan preset change causes data reload.
- Pure display toggle does not refetch data.

Browser QA:

- Open layer popover.
- Switch display tab toggles.
- Filter only buy-side BSP markers.
- Switch algorithm preset and verify chart reloads plus config label changes.
- Confirm no console errors.

## Recommended Implementation Order

1. Add `chan_config_presets.py`.
2. Wire config metadata into `chan_detail_service.py`.
3. Wire config metadata into `chan_adapter.py` and Radar response.
4. Extend `layerState.js` with BSP filters and `cchan_preset`.
5. Update `chanOverlay.js` BSP filtering.
6. Redesign `LayerPanel.jsx` into tabs.
7. Update `KlineChart.jsx` to refetch only when preset changes.
8. Add tests.
9. Run `/review`, `/qa`, then `/ship`.

## Engineering Review Notes

Scope is medium and touches more than 8 files if implemented fully. The lowest-risk version is:

```text
config presets + metadata + BSP filtering + tabbed LayerPanel
```

Defer true click-to-inspect until after this lands. Existing overlays are locked and not hit-testable, so forcing click inspection into the same change would expand the blast radius.

## Status

Approved direction pending user confirmation.
