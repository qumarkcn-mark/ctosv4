# Radar Level Chain Deduction Plan

> Created: 2026-04-26
> Status: planning
> Scope: empty-position Radar redesign, day -> 30 -> 5 level-chain deduction
> Supersedes: parts of `雷达重设计方案.md` where the first screen is driven by entry score or "can enter / cannot enter" verdicts.

## Premise

Radar should not first answer "can I buy now?"

For the current product need, Radar should answer:

```text
当前走势正在推演哪条路径？
接下来发生什么，才会形成可操作买点？
发生什么，当前推演作废？
```

This keeps CT-OS as a trading coach. It does not place orders, does not replace the user, and does not hide the structure behind an AI conclusion.

## Why The Old Radar Plan Is Only Partly Usable

`雷达重设计方案.md` has useful pieces:

- structure facts remain algorithm-owned
- AI only translates structured facts
- empty and holding modes must stay separate
- stop, target, risk, and invalidation need explicit fields
- UI should show action context before raw structure details

But its empty-position flow is too verdict-driven:

```text
五条件全满足 -> 可入场
缺一个条件 -> 观察中
```

That is not how the user wants to use Radar in live trading. The user wants a live deduction board:

```text
日线机会有效
30分正在验证
5分买点尚未形成
如果接下来出现 X，则进入买点确认
如果接下来出现 Y，则当前推演失败
```

So the five-condition checklist should move from "main product model" to "evidence model".

## Product Definition

```text
Radar is a single-symbol Chan deduction workbench.
It is not a scanner.
It is not an execution API.
It is not an AI opinion engine.
```

Responsibilities:

- consume formal Chan structure from `chan_adapter`
- organize multiple levels into a deduction path
- expose what is confirmed, forming, waiting, failed, or stale
- show next trigger conditions and invalidation conditions
- let AI narrate only the structured deduction output

Non-responsibilities:

- full-market candidate ranking
- portfolio concentration analysis
- automatic execution
- QMT order placement
- holding-stage redesign in this first phase
- T+0 logic

## First Version Scope

Only support the empty-position main chain:

```text
day -> 30 -> 5
```

Level roles:

| Level | Role | Question |
|---|---|---|
| day | context / setup | Is there a tradeable higher-level structure? |
| 30 | confirmation | Is the day-level setup being locally supported or invalidated? |
| 5 | trigger | Which buy-point path is forming, confirmed, or failed? |

Optional context:

- `week` may be displayed as background safety, but it is not part of v1 path selection.
- `60 -> 15` remains secondary context, not the first implementation target.
- `30 -> 5 -> 1` becomes relevant only after QMT read-only minute data exists.

## Current Codebase Fit

Existing foundation:

- `server/engines/structure/chan_adapter.py` already loads `week/day/60/30/15/5`.
- `chan_adapter` exposes `bis`, `segs`, `bi_zhongshus`, `bsps`, `patterns`, `zoushi_type`, `div_info`, `zg`, `zd`, and `state`.
- `server/engines/structure/nesting.py` has a simple interval-nesting detector.
- `server/engines/decision/entry_planner.py` already produces an empty-position checklist.
- `/api/radar/{symbol}` is now the active product contract.

Gap:

```text
The structure facts exist, but the decision layer compresses them into boolean checks.
It does not yet build a day -> 30 -> 5 deduction path.
```

## Proposed Architecture

```text
BaoStock lake
    |
    v
chan.py via chan_adapter
    |
    v
Formal structure facts
    |
    +--> existing entry_plan evidence
    |
    v
level_chain_deduction.py
    |
    v
Radar deduction contract
    |
    v
TRadarV2 deduction board
```

New module:

```text
server/engines/decision/level_chain_deduction.py
```

This module must be pure:

- no database calls
- no HTTP calls
- no LLM calls
- no push notifications
- no QMT calls

Inputs:

```python
{
    "levels": {
        "day": {...},
        "30": {...},
        "5": {...}
    },
    "freshness": {...}
}
```

Output:

```json
{
  "chain": ["day", "30", "5"],
  "status": "WAITING_TRIGGER",
  "confidence": "PREVIEW",
  "summary": "日线机会有效，30分回踩验证，等待5分买点形成",
  "main_path": {},
  "buy_point_candidates": [],
  "level_roles": {},
  "evidence": {},
  "invalid_if": [],
  "next_if": []
}
```

## Deduction Status

Use explicit states:

| Status | Meaning |
|---|---|
| `NO_SETUP` | day-level setup is absent or unsafe |
| `WAITING_CONFIRMATION` | day has setup, 30 is not yet supportive |
| `WAITING_TRIGGER` | day and 30 support observation, 5 has no confirmed buy point |
| `TRIGGER_FORMING` | 5-level buy point path is forming but not confirmed |
| `TRIGGER_CONFIRMED` | 5-level buy point is confirmed by structure facts |
| `FAILED` | a structural invalidation condition has fired |
| `STALE` | required formal structure is stale or incomplete |

Status precedence:

```text
STALE
  > FAILED
  > NO_SETUP
  > WAITING_CONFIRMATION
  > TRIGGER_CONFIRMED
  > TRIGGER_FORMING
  > WAITING_TRIGGER
```

Rationale:

- stale formal structure cannot confirm any live deduction
- structural invalidation overrides any old buy point
- an absent day setup means lower-level signals are noise
- confirmed 5-level buy points only matter after day and 30 are supportive

Important rule:

```text
Only TRIGGER_CONFIRMED may be styled as a confirmed structural buy point.
All other states must be shown as deduction / waiting / failed, not as an action instruction.
```

## Buy Point Object

Patterns strings are not enough for live use.

Radar v2 needs structured buy-point candidates:

```json
{
  "id": "5m_third_buy_waiting",
  "type": "THIRD_BUY",
  "level": "5",
  "status": "FORMING",
  "role": "trigger",
  "parent_level": "30",
  "parent_context": "30分回踩验证",
  "label": "等待5分三买确认",
  "trigger_if": [
    "5分向上离开中枢后第一次回试",
    "回试低点不跌破5分/30分关键ZG",
    "回试段出现力度衰减或向上转折"
  ],
  "invalid_if": [
    "5分回试跌回中枢内部",
    "30分跌破关键ZD"
  ],
  "evidence": {
    "zg": 0,
    "zd": 0,
    "last_bi_dir": "down",
    "patterns": []
  }
}
```

Supported v1 types:

| Type | Meaning | v1 role |
|---|---|---|
| `FIRST_BUY` | downtrend or down leg bottom divergence | left-side trigger candidate |
| `SECOND_BUY` | first pullback after first buy, low does not break prior buy low | confirmation trigger candidate |
| `THIRD_BUY` | upward departure from center, first pullback does not break ZG | primary right-side trigger candidate |

`THIRD_BUY` should be the clearest v1 UI path because it is easiest to explain and invalidate:

```text
成立：离开中枢后第一次回试不破 ZG
失效：回试跌回中枢区间
```

## Level Role Contract

Each level gets a role-specific state.

```json
{
  "level_roles": {
    "day": {
      "role": "setup",
      "state": "VALID",
      "summary": "日线有可观察结构",
      "invalid_if": ["日线回到原中枢内部", "日线出现卖点风险"]
    },
    "30": {
      "role": "confirmation",
      "state": "SUPPORTIVE",
      "summary": "30分回踩未破关键中枢",
      "invalid_if": ["30分跌破关键ZD"]
    },
    "5": {
      "role": "trigger",
      "state": "WAITING",
      "summary": "5分买点未确认",
      "next_if": ["出现5分一买/二买/三买结构"]
    }
  }
}
```

Allowed role states:

```text
VALID
UNSAFE
SUPPORTIVE
NEUTRAL
CONFLICTING
WAITING
FORMING
CONFIRMED
FAILED
STALE
```

## First Heuristic Rules

These rules should be conservative and explicit.

### Stale Gate

If `freshness.is_stale` is true, return:

```text
status = STALE
confidence = STALE
```

No buy point can be confirmed from stale formal structure.

### Day Setup

Day is supportive when at least one is true:

- day patterns include a buy-side point: `一买`, `二买`, `三买`, `类二买`, `类三买`
- day state indicates upward leaving or third-buy confirmation
- day has bottom divergence and is not showing top divergence / sell point risk

Day is unsafe when:

- day patterns include `顶背驰`, `1卖`, `二卖`, `三卖`, `三卖确认`
- price has returned below a key day center invalidation boundary

### 30 Confirmation

30 is supportive when:

- 30 patterns include buy-side point, or
- 30 state is upward leaving / third-buy confirmed, or
- 30 pullback remains above its active center support

30 is conflicting when:

- 30 patterns include sell-side point or top divergence, or
- 30 breaks below its key `zd` while day setup needs 30 support

### 5 Trigger

5 is confirmed when:

- the latest confirmed 5-level buy-side BSP is inside the active 5-level structure window, and
- its type is one of `1`, `1p`, `2`, `2s`, `3a`, or `3b`, and
- day and 30 roles are already supportive

5 is forming when:

- last 5-level bi direction is down or a pullback is in progress, and
- price is above the relevant invalidation boundary, and
- no confirmed buy-side BSP exists yet

5 is waiting when:

- day and 30 are supportive, but 5 has no forming or confirmed candidate

Latest-window rule:

```text
Do not treat any historical BSP as current.
A buy-side BSP is current only when its timestamp is at or after the latest active
5-level center begin time, or within the last N serialized 5-level bis.
Initial v1 value: N = 3.
```

This is intentionally conservative. A stale 5-level buy point from 40 bars ago should not light up the current deduction.

### Invalidation Boundary

For v1, choose the invalidation boundary in this order:

```text
1. 30-level active center ZD, when available
2. 5-level active center ZD, when the deduction is specifically about a 5-level center
3. latest relevant buy-side BSP low
4. no structural boundary -> cannot confirm, stay WAITING_TRIGGER
```

If price is below the chosen invalidation boundary:

```text
status = FAILED
```

If there is no reliable boundary, the engine must not return `TRIGGER_CONFIRMED`.

## API Contract Change

Add `deduction` to Radar response:

```json
{
  "api_version": "radar.v1",
  "structure": {},
  "entry_plan": {},
  "deduction": {
    "version": "level_chain_deduction.v1",
    "mode": "EMPTY",
    "chain": ["day", "30", "5"],
    "status": "WAITING_TRIGGER",
    "confidence": "PREVIEW",
    "summary": "日线机会有效，30分回踩验证，等待5分买点形成",
    "main_path": {
      "id": "day_30_wait_5_buy",
      "label": "等待5分买点确认",
      "current_step": "5分买点未确认",
      "next_if": [],
      "invalid_if": []
    },
    "buy_point_candidates": [],
    "level_roles": {},
    "evidence": {},
    "disclaimer": "仅供参考，不构成投资建议"
  }
}
```

Compatibility:

- Keep existing `entry_plan` for now.
- `entry_plan.conditions` becomes supporting evidence.
- UI should prefer `deduction` when present.
- Existing consumers of `entry_plan` must not break.

## UI Redesign

First screen:

```text
┌──────────────────────────────────────────┐
│ 数据：BaoStock 正式结构，最后更新 15:00   │
│ 模式：空仓推演 day -> 30 -> 5             │
├──────────────────────────────────────────┤
│ 当前推演                                  │
│ 日线机会有效，30分回踩验证，等待5分买点形成 │
├──────────────────────────────────────────┤
│ 接下来如果                                │
│ - 5分向上离开后第一次回试不破ZG           │
│ - 回试段力度衰减或转折                    │
│ 则：进入5分买点确认                       │
├──────────────────────────────────────────┤
│ 推演失效                                  │
│ - 30分跌破关键ZD                          │
│ - 5分回试跌回中枢                         │
└──────────────────────────────────────────┘
```

Second screen / expanded evidence:

- day role card
- 30 role card
- 5 role card
- buy-point candidate table
- center boundaries
- divergence facts
- risk and reward evidence

AI:

- default collapsed
- only narrates `deduction`, `structure`, and `entry_plan`
- cannot create additional triggers, stops, targets, or buy points

## Testing Contract

Unit tests:

```text
tests/test_level_chain_deduction.py
```

Required cases:

| Case | Expected |
|---|---|
| stale freshness | `status=STALE`, no confirmed trigger |
| day unsafe | `status=NO_SETUP` or `FAILED` |
| day valid, 30 neutral | `status=WAITING_CONFIRMATION` |
| day valid, 30 supportive, 5 no candidate | `status=WAITING_TRIGGER` |
| day valid, 30 supportive, 5 buy-side BSP latest | `status=TRIGGER_CONFIRMED` |
| day valid, 30 supportive, 5 pullback above invalidation | `status=TRIGGER_FORMING` |
| 30 breaks invalidation boundary | `status=FAILED` |
| pattern strings include buy words but stale | still `STALE` |

API tests:

- `/api/radar/{symbol}` includes `deduction` in empty mode.
- holding mode may return `deduction=null` in v1.
- stale adapter response returns stable deduction stale envelope.
- `entry_plan` compatibility remains intact.

UI tests / QA:

- Radar displays deduction summary when present.
- Radar does not show "可入场" as primary copy unless `TRIGGER_CONFIRMED`.
- Stale data is visually distinct from waiting state.
- AI narrative remains optional and folded.

## Implementation Phases

### Phase 1: Deduction Engine

- Add `level_chain_deduction.py`.
- Add pure unit tests for day -> 30 -> 5 cases.
- Wire output into `/api/radar/{symbol}` as `deduction`.
- Keep old UI unchanged except hidden payload availability.

Phase 1 acceptance:

```text
Backend-only.
No visual redesign.
No QMT.
No holding rewrite.
All deduction logic unit-tested before UI consumes it.
```

### Phase 2: Radar UI Deduction Board

- Update `TRadarV2.jsx` to prefer `deduction`.
- Keep legacy normalized adapter as fallback.
- Move old condition score into evidence section.
- Default AI folded.

### Phase 3: Plan / Playbook Integration

- Daily Playbook uses `deduction.status` and `main_path.next_if`.
- Alerts can only be created from non-stale deduction states.
- No push delivery directly from Radar.

### Phase 4: QMT Read-Only Preview

Only after formal deduction works:

- add `intraday_preview`
- mark QMT data as forming / preview
- do not let QMT preview override formal BaoStock structure
- do not expose order execution

## Open Questions

1. Should `TRIGGER_CONFIRMED` be worded as "买点确认" or still avoid action words in the first screen?
2. Should `FIRST_BUY` be shown in v1 UI, or should v1 prioritize `THIRD_BUY` as the clearest real-trading path?
3. What freshness threshold should block empty-position Radar during trading hours once QMT preview exists?
4. Should week-level danger be a hard gate in v1 or an evidence warning?

## Decision

Use this plan as the new Radar redesign base.

Keep `雷达重设计方案.md` as historical context, but do not implement its first-screen "entry score / can enter" model as the main Radar experience.

## Engineering Review Notes

Review date: 2026-04-26

Scope decision:

```text
Proceed with Phase 1 only before touching UI.
```

Why:

- the existing code already has formal structure facts through `chan_adapter`
- the risky gap is the missing deduction layer, not the UI skin
- adding a pure decision module keeps blast radius small
- existing `/api/radar/{symbol}` compatibility can remain intact

Architecture recommendation:

```text
Create `server/engines/decision/level_chain_deduction.py` as a pure function module.
Expose its result as `data.deduction` from `/api/radar/{symbol}`.
Do not remove or reinterpret `entry_plan` in the same change.
```

Test recommendation:

```text
Write `tests/test_level_chain_deduction.py` first.
Then add targeted Radar API contract assertions.
Only after that should `TRadarV2` consume `deduction`.
```

Known implementation risks:

| Risk | Mitigation |
|---|---|
| Historical BSP misread as current trigger | use latest-window rule |
| Stale structure shows a current trigger | status precedence makes `STALE` highest |
| 5-level signal treated without day/30 support | confirmed trigger requires supportive higher levels |
| UI regresses while backend contract changes | Phase 1 keeps UI unchanged |
| QMT preview pollutes formal structure | QMT remains Phase 4 and must be marked preview only |

## Design Review Notes

Review date: 2026-04-26

UI classification:

```text
APP UI, not landing page.
```

Design direction:

```text
Radar first screen should behave like a dense trading workbench.
It must not become a decorative card grid or a "buy / do not buy" verdict panel.
```

First-screen hierarchy:

```text
1. Data source and chain freshness
2. Current deduction summary
3. Next-if and invalid-if conditions
4. Level-role strip: day / 30 / 5
5. Old complete-classification plans and entry checklist as evidence
```

Copy rules:

- Prefer "当前推演", "接下来如果", "推演失效", and "预案".
- Avoid first-screen wording like "可入场", "不建议入场", or "指令".
- `TRIGGER_CONFIRMED` may be shown as "买点确认", but still not as an order instruction.
- Old entry checklist may remain visible, but it is evidence, not the main decision.

Interaction states:

| State | UI behavior |
|---|---|
| Loading | Keep existing skeleton. Do not flash a false deduction. |
| Stale | Show stale banner and deduction status `数据过期`. No confirmed trigger styling. |
| Waiting | Highlight next-if conditions. Keep invalid-if visible. |
| Forming | Show "形成中"; do not use action wording. |
| Confirmed | Show "买点确认"; require risk/evidence review copy nearby. |
| Failed | Show "推演失效"; keep the failed level visible. |

Responsive rule:

```text
On narrow screens, next-if / invalid-if and day / 30 / 5 role strips collapse to one column.
```
