# CT-OS V4.0 Radar API Contract

> Created: 2026-04-24
> Depends on: `docs/ARCHITECTURE_FOUNDATION_DECISIONS.md`, `docs/DATA_SOURCE_CONTRACT.md`, `docs/STRATEGY_CONTRACT.md`, `docs/STRUCTURE_ALGORITHM_INVENTORY.md`

This contract defines the new Radar API for Phase 1 Chan Analysis Workbench and Phase 2 Strategy Coach.

Radar is the user's deep single-symbol view. It shows formal Chan structure, multi-level context, plans, freshness, and risk reminders.

Radar is not an execution API.

## Endpoint

```http
GET /api/radar/{symbol}?user_id=1&cost=0&qty=0
```

Path params:

| Field | Required | Meaning |
|---|---:|---|
| `symbol` | yes | accepted formats: `sh600519`, `sh.600519`, `sh-600519` |

Query params:

| Field | Required | Default | Meaning |
|---|---:|---|---|
| `user_id` | no in first compatible version, yes later | `null` | user context for watchlist, positions, configs |
| `cost` | no | `0` | temporary compatibility field for holding mode |
| `qty` | no | `0` | temporary compatibility field for holding mode |

Mode rule:

```text
cost > 0 and qty > 0 => HOLDING
otherwise => EMPTY
```

Later, Radar should prefer persisted position state from `ctos.db` by `user_id + symbol`, and treat `cost/qty` as debug compatibility only.

## Response Envelope

```json
{
  "status": "success",
  "data": {
    "api_version": "radar.v1",
    "symbol": "sh.600519",
    "mode": "EMPTY",
    "as_of": "2026-04-24T20:35:00+08:00",
    "data_source": {},
    "freshness": {},
    "structure": {},
    "strategy": {},
    "entry_plan": {},
    "holding_plan": null,
    "plans": [],
    "alerts": [],
    "narrative": null,
    "legacy_refs": {},
    "disclaimer": "仅供参考，不构成投资建议"
  }
}
```

Error envelope:

```json
{
  "status": "error",
  "data": {
    "api_version": "radar.v1",
    "symbol": "sh.600519",
    "mode": "EMPTY",
    "error": {
      "code": "ENGINE_ERROR",
      "message": "structure engine failed",
      "fallback_used": false
    },
    "freshness": {
      "is_stale": true,
      "stale_reason": "ENGINE_ERROR"
    },
    "disclaimer": "仅供参考，不构成投资建议"
  }
}
```

## Top-Level Fields

| Field | Owner | Meaning |
|---|---|---|
| `api_version` | API | stable contract version |
| `symbol` | API/domain | canonical internal symbol |
| `mode` | API/domain | `EMPTY` or `HOLDING` |
| `as_of` | API | response generation time |
| `data_source` | data layer | source and adjustment metadata |
| `freshness` | data layer | whether formal structure is current enough |
| `structure` | structure engine | formal Chan facts and derived structure facts |
| `strategy` | decision engine | selected strategy and condition results |
| `entry_plan` | decision engine | empty-position plan; null in holding mode |
| `holding_plan` | decision engine | holding-management plan; null in empty mode |
| `plans` | decision engine | normalized plan list for UI/alerts |
| `alerts` | decision engine | delivery candidates, not pushed by Radar |
| `narrative` | coach/AI | optional prose generated from structured facts |
| `legacy_refs` | API adapter | compatibility pointers to old `/api/chan/matrix/v2` fields |
| `disclaimer` | API | required risk disclaimer |

## Data Source

Radar formal structure source:

```json
{
  "structure": {
    "provider": "baostock",
    "adjustflag": "2",
    "levels": ["week", "day", "60", "30", "15", "5"],
    "engine": "chan.py",
    "adapter": "server.engines.structure.chan_adapter"
  },
  "quote": {
    "provider": "tencent",
    "purpose": "current_price_display_only"
  }
}
```

Rules:

- Formal structure must come from BaoStock lake and `chan.py`.
- Tencent may provide current price display and preview only.
- TDX scanner results may link into Radar, but Radar must recompute deep structure from BaoStock.
- QMT data is not part of public Radar Phase 1/2.

## Freshness

```json
{
  "source": "baostock",
  "adjustflag": "2",
  "last_bar_at": "2026-04-24 15:00:00",
  "checked_at": "2026-04-24T20:35:00+08:00",
  "is_stale": false,
  "stale_reason": "",
  "levels": {
    "day": {"last_bar_at": "2026-04-24", "is_stale": false},
    "30": {"last_bar_at": "2026-04-24 15:00:00", "is_stale": false},
    "5": {"last_bar_at": "2026-04-24 15:00:00", "is_stale": false}
  }
}
```

Rules:

- Stale structure can be displayed with warning.
- Stale structure must block trading-action alerts.
- Missing required levels should set `stale_reason = "LEVEL_INCOMPLETE"`.
- Engine failure should set `stale_reason = "ENGINE_ERROR"`.

## Structure

`structure` contains formal Chan facts plus allowed derived facts.

```json
{
  "levels": {
    "day": {
      "level": "day",
      "price": 123.45,
      "state": "UPWARD_LEAVING",
      "bi_count": 12,
      "seg_count": 3,
      "zhongshu_count": 2,
      "last_bi": {},
      "active_zhongshu": {},
      "bis": [],
      "segs": [],
      "bi_zhongshus": [],
      "seg_zhongshus": [],
      "bsps": [],
      "patterns": [],
      "source": {
        "engine": "chan.py",
        "provider": "baostock",
        "adjustflag": "2"
      }
    }
  },
  "systems": {
    "short_term": {
      "name": "day_30_5",
      "levels": ["day", "30", "5"],
      "interval_nesting": {}
    },
    "swing": {
      "name": "day_60_15",
      "levels": ["day", "60", "15"],
      "interval_nesting": {}
    }
  },
  "summary": {
    "primary_level": "day",
    "trend_state": "UPWARD_LEAVING",
    "risk_state": "NORMAL",
    "key_levels": {
      "support": [],
      "resistance": []
    }
  }
}
```

Authority rules:

- `bis`, `segs`, `bi_zhongshus`, `seg_zhongshus`, and `bsps` must come from `chan.py adapter`.
- `state`, `patterns`, `summary`, and `interval_nesting` are CT-OS derived facts and must be based on adapter output.
- Radar must not expose self-authored basic structure as formal structure.

## Mode Exclusivity

Radar has two exclusive modes.

Allowed in `EMPTY` mode:

```json
{
  "mode": "EMPTY",
  "entry_plan": {},
  "holding_plan": null
}
```

Allowed in `HOLDING` mode:

```json
{
  "mode": "HOLDING",
  "entry_plan": null,
  "holding_plan": {}
}
```

Rules:

- Empty mode may show entry checklist, watch conditions, risk/reward, and position sizing.
- Holding mode may show structure validity, trailing stop, reduce/exit conditions, and holding stage.
- Holding mode must not tell the user whether they should newly enter the stock.
- Empty mode must not pretend there is an existing position.

## Strategy

```json
{
  "strategy_id": "war1_third_buy",
  "strategy_version": "1.0.0",
  "strategy_type": "战法一",
  "name": "战法一：日线三买",
  "status": "WATCHING",
  "conditions": [
    {
      "condition_id": "day_breakout_above_zg",
      "label": "日线突破中枢上沿",
      "status": "PASS",
      "severity": "INFO",
      "evidence": {
        "level": "day",
        "zg": 12.34,
        "close": 12.58
      }
    }
  ]
}
```

Rules:

- Strategy output must follow `docs/STRATEGY_CONTRACT.md`.
- Strategy decisions must be deterministic.
- AI may translate strategy result into narrative, but cannot decide condition status.

## Entry Plan

Only present in `EMPTY` mode.

```json
{
  "plan_id": "war1_wait_5m_confirm",
  "plan_type": "ENTRY",
  "status": "WATCHING",
  "title": "等待 5 分钟确认",
  "conditions": [],
  "risk": {
    "invalid_if": "跌回日线中枢上沿下方",
    "stop_reference": {
      "source": "structure",
      "level": "day",
      "field": "zd"
    }
  },
  "targets": [],
  "position_sizing": null,
  "reward_ratio": null,
  "disclaimer": "仅供参考，不构成投资建议"
}
```

Compatibility mapping from `/api/chan/matrix/v2`:

| Radar field | Old V2 field |
|---|---|
| `entry_plan.conditions` | `entry_checklist` |
| `strategy` | `strategy_classification` |
| `entry_plan.position_sizing` | `position_sizing` |
| `entry_plan.targets` | `targets` |
| `entry_plan.reward_ratio` | `reward_ratio` |
| `entry_plan.risk.stop_check` | `stop_atr_check` |

## Holding Plan

Only present in `HOLDING` mode.

```json
{
  "plan_id": "holding_stage_manager",
  "plan_type": "HOLDING",
  "status": "WATCHING",
  "stage": "PROTECT_PROFIT",
  "conditions": [],
  "risk": {
    "trailing_stop": 118.2,
    "invalid_if": "跌破台阶止损或结构破坏"
  },
  "reduce_plan": null,
  "exit_plan": null,
  "disclaimer": "仅供参考，不构成投资建议"
}
```

Compatibility mapping from `/api/chan/matrix/v2`:

| Radar field | Old V2 field |
|---|---|
| `holding_plan.stage` | `holding_stage_v2.stage` or `holding_status.stage` |
| `holding_plan.risk.trailing_stop` | `holding_stage_v2.trailing_stop` or `holding_status.stair_stop_price` |
| `holding_plan.conditions` | `holding_stage_v2` condition fields |
| `holding_plan.legacy_status` | `holding_status` |

## Plans

`plans` is the normalized list consumed by Phase 2 alerts and Coach Event Log.

```json
[
  {
    "plan_id": "war1_wait_5m_confirm",
    "plan_type": "ENTRY",
    "status": "WATCHING",
    "strategy_id": "war1_third_buy",
    "conditions": ["day_breakout_above_zg"],
    "disclaimer": "仅供参考，不构成投资建议"
  }
]
```

Rules:

- `entry_plan` or `holding_plan` is the primary plan for the current mode.
- `plans` may contain additional watch plans.
- Plans are not orders and cannot be consumed directly by QMT.
- Phase 3 must convert eligible plans into execution intent candidates through Execution Layer only.

## Alerts

Radar may return alert candidates, but must not send push messages.

```json
[
  {
    "alert_type": "STRATEGY_TRIGGER",
    "priority": "HIGH",
    "condition_id": "m5_buy_signal",
    "dedupe_key": "1:sh.600519:war1_third_buy:m5_buy_signal:2026-04-24",
    "message": {
      "title": "5 分钟确认信号出现",
      "body": "战法一观察条件触发，请检查日线中枢和 5 分钟买点。仅供参考。"
    }
  }
]
```

Rules:

- Stale structure blocks trading-action alert candidates.
- Push Service handles delivery, dedupe, subscription, and send result.
- All trading-action alert messages must include "仅供参考".

## Narrative

`narrative` is optional.

```json
{
  "provider": "deepseek",
  "source": "structured_facts_only",
  "text": "当前日线处于离开段观察区，30 分钟结构仍需确认。仅供参考。"
}
```

Rules:

- Narrative must be generated from structured JSON.
- Narrative cannot create new conditions, stop prices, targets, or structure facts.
- Narrative must include "仅供参考" if it mentions trading actions.

## Legacy Refs

First compatible implementation may wrap `/api/chan/matrix/v2`.

```json
{
  "source_endpoint": "/api/chan/matrix/v2/{symbol}",
  "matrix_a_levels": ["day", "m30", "m5"],
  "matrix_b_levels": ["day", "m60", "m15"],
  "compatibility_mode": true
}
```

Rules:

- `legacy_refs` is temporary.
- Frontend must not rely on it for new features.
- It can help validate parity during migration.

## Contract Tests

Required first tests:

- `EMPTY` mode returns `entry_plan != null` and `holding_plan == null`.
- `HOLDING` mode returns `entry_plan == null` and `holding_plan != null`.
- Response includes `disclaimer`.
- `structure.levels.day` exists when engine succeeds.
- `structure` exposes `source.engine = "chan.py"`.
- `data_source.structure.provider = "baostock"`.
- Stale or engine error returns stable error/freshness envelope.
- `plans` never contain executable order instructions.
- `alerts` are candidates only and do not send push.
