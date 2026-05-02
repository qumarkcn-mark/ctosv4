# CT-OS V4.0 Intraday T Plan Contract

> Created: 2026-04-29
> Scope: Intraday T watch windows, Radar/Strategy output, Command Deck consumption
> Depends on: `docs/ARCHITECTURE_FOUNDATION_DECISIONS.md`, `docs/DATA_SOURCE_CONTRACT.md`, `docs/RADAR_API_CONTRACT.md`, `docs/STRATEGY_CONTRACT.md`, `docs/COACH_EVENT_CONTRACT.md`, `docs/QMT_REALTIME_RADAR_INTEGRATION_PLAN.md`

This contract defines how CT-OS represents intraday T opportunities without turning the product into a trading robot.

Intraday T plans are coach-only. They may help the user observe sell-window, buyback-window, and near-inflection conditions. They must not place orders, auto-click QMT, or present a deterministic instruction as a guarantee.

交易相关内容仅供参考，不构成投资建议。

## Core Decision

Do not build a second intraday-T algorithm inside the Command Deck.

```text
Radar / Strategy Engine
  -> computes structure facts and T watch windows
  -> emits normalized plans / alerts / coach events

Command Deck
  -> ranks today's events
  -> shows evidence and next user response
  -> records ignored / observed / handled / invalidated
  -> never recomputes Chan structure
```

This keeps the system DRY:

- Radar owns single-symbol structure and deduction.
- Strategy owns deterministic rule evaluation.
- Command Deck owns today's response queue.
- Trading Ledger owns actual user trades and later behavior review.

## User Job

The user is not asking "should I trade automatically?"

The real job is:

```text
I have a base position.
The larger level is approaching a meaningful boundary.
I need CT-OS to tell me:
  - whether today is worth watching,
  - whether this is a sell-window or buyback-window,
  - whether the small structure is only approaching or confirmed,
  - when the idea has failed,
  - and later, whether I followed the plan.
```

## Event Types

Allowed initial intraday T event types:

| Event Type | Meaning | Primary Consumer |
|---|---|---|
| `T_SELL_WINDOW` | Price is near/inside pressure and small-level weakening may support reducing the T portion | Command Deck |
| `T_BUYBACK_WINDOW` | Larger-level down stroke is near completion and small-level repair may support buying back the T portion | Command Deck |
| `NEAR_INFLECTION` | Price/structure is close to a change point, but no action-grade confirmation exists yet | Command Deck |
| `SMALL_STRUCTURE_CONFIRM` | The smaller level has completed the required confirmation for an existing watch window | Radar + Command Deck |
| `T_BLOCKED` | A T idea is blocked by data, risk, liquidity, base-position, or discipline constraints | Command Deck |

These are event categories, not orders.

## State Machine

```text
                         stale data / risk block
                                  │
                                  ▼
                            ┌──────────┐
                            │ BLOCKED  │
                            └──────────┘

┌─────────────┐   condition closes   ┌───────────┐   user marks   ┌───────────┐
│ APPROACHING │ ───────────────────▶ │ CONFIRMED │ ─────────────▶ │ RESPONDED │
└─────────────┘                      └───────────┘                └───────────┘
       │                                  │
       │ invalidation line hit            │ expires / opposite proof
       ▼                                  ▼
┌─────────────┐                      ┌─────────┐
│ INVALIDATED │                      │ EXPIRED │
└─────────────┘                      └─────────┘
```

Allowed statuses:

| Status | Meaning |
|---|---|
| `APPROACHING` | Close to the setup, but the confirming bar/structure is not complete |
| `CONFIRMED` | Deterministic small-level confirmation is present |
| `BLOCKED` | Must not become an action-grade event because a guard failed |
| `INVALIDATED` | The setup failed structurally |
| `EXPIRED` | The setup was time-limited and is no longer relevant |
| `RESPONDED` | The user marked a response in CT-OS |

Mapping to existing generic `PlanStatus`:

| Intraday T Status | Generic Plan Status |
|---|---|
| `APPROACHING` | `WATCHING` |
| `CONFIRMED` | `TRIGGERED` |
| `BLOCKED` | `BLOCKED` |
| `INVALIDATED` | `INVALIDATED` |
| `EXPIRED` | `INVALIDATED` |
| `RESPONDED` | Stored as user response on playbook item / coach event |

## Source Authority

```text
Large structure context:
  BaoStock + chan.py formal structure
  levels: day / 60 / 30 / 15 / 5

Intraday confirmation:
  QMT closed 1m / 5m bars when available
  TDX local 1m only for display/replay

Preview:
  QMT forming bar or Tencent quote may show distance-to-boundary only
  forming bars must not confirm BSP, zhongshu break, or T event
```

Rules:

- A forming 1-minute bar may create `APPROACHING`.
- Only a closed QMT 1-minute or 5-minute bar may create `CONFIRMED`.
- BaoStock remains the formal post-close source.
- TDX local 1-minute data is display/replay only unless the product is explicitly in replay mode.
- If QMT is unavailable, intraday T events degrade to `BLOCKED` or `APPROACHING` based on available preview evidence.

## T Sell Window

`T_SELL_WINDOW` watches for "先卖后接".

Typical setup:

```text
30m / 5m context:
  price is entering pressure zone
  current up stroke is late or extended
  parent path has reduce/defense relevance

1m / 5m confirmation:
  top divergence, failed breakout, or sell event
  closed bar confirms weakening
  price cannot hold above pressure zone
```

Required evidence:

| Field | Meaning |
|---|---|
| `position.base_qty` | user has base position |
| `position.available_sell_for_t` | T portion is actually sellable |
| `zones.pressure` | pressure zone, not a single brittle price |
| `structure.parent_level` | usually `30` or `5` |
| `structure.child_level` | usually `1` or `5` |
| `confirmation` | closed-bar evidence when status is `CONFIRMED` |
| `invalid_if` | condition that cancels the sell-window |

Must not fire when:

- no base position exists
- today has no available sellable T quantity
- price is far below pressure
- small-level evidence is only a forming bar but status is marked `CONFIRMED`
- data freshness is stale

## T Buyback Window

`T_BUYBACK_WINDOW` watches for "先接后卖" or "卖出后接回".

Typical setup:

```text
30m context:
  down stroke is near support / completion
  parent structure has not broken the larger defense line

1m confirmation:
  small down structure completes
  selling pressure weakens
  bottom divergence / second-buy / repair event appears
  closed bar confirms the small structure
```

Required evidence:

| Field | Meaning |
|---|---|
| `position.base_qty` | user has base position or prior T sell response |
| `position.buyback_capacity` | cash / T response context allows buyback watch |
| `zones.support` | support / buyback observation zone |
| `structure.parent_level` | usually `30` |
| `structure.child_level` | usually `1` |
| `confirmation` | closed-bar evidence when status is `CONFIRMED` |
| `invalid_if` | condition that cancels buyback |

Must not fire when:

- parent defense has already failed
- small-level structure only has a single spike without repair confirmation
- market/position guard marks T as blocked
- the event is past its valid session window

## Guard Rails

Every intraday T plan must run guards before it can become `CONFIRMED`.

| Guard | Blocks When |
|---|---|
| `BASE_POSITION_REQUIRED` | user has no base position |
| `SELLABLE_QTY_REQUIRED` | sell-window has no available T quantity |
| `BUYBACK_CAPACITY_REQUIRED` | buyback-window has no cash or prior T context |
| `MAX_T_ATTEMPTS` | user already exceeded configured T attempts today |
| `FRESH_QMT_BAR_REQUIRED` | closed intraday confirmation is missing/stale |
| `PARENT_DEFENSE_FAILED` | larger-level structure invalidated the idea |
| `VOLATILITY_TOO_LOW` | expected spread is too small after cost/slippage |
| `MARKET_RISK_BLOCK` | market or sector state blocks aggressive intraday action |
| `NEAR_CLOSE_BLOCK` | too close to session end for the intended plan |
| `USER_DISCIPLINE_BLOCK` | user-configured cooldown after failed T / overtrading |

Blocked plans should still be visible when useful. They explain why CT-OS is not surfacing an action-grade event.

## Contract Shape

Recommended normalized plan payload:

```json
{
  "plan_id": "intraday_t:sh.600519:20260429:sell:1030",
  "plan_type": "INTRADAY_T",
  "event_type": "T_SELL_WINDOW",
  "status": "APPROACHING",
  "symbol": "sh.600519",
  "user_id": 1,
  "as_of": "2026-04-29T10:30:03+08:00",
  "expires_at": "2026-04-29T11:30:00+08:00",
  "source": {
    "producer": "strategy_engine",
    "radar_api_version": "radar.v1",
    "structure_provider": "baostock",
    "intraday_provider": "qmt",
    "bar_status": "FORMING"
  },
  "position": {
    "base_qty": 2000,
    "protected_base_qty": 1000,
    "available_sell_for_t": 1000,
    "buyback_capacity": 0,
    "today_t_attempts": 0
  },
  "levels": {
    "parent": "30",
    "child": "1",
    "display": ["30", "5", "1"]
  },
  "zones": {
    "pressure": {
      "low": 123.4,
      "high": 124.2,
      "source": "30m_zg_or_prior_high"
    },
    "support": null,
    "invalidation": {
      "price": 124.8,
      "meaning": "放量站稳压力区上方，卖T窗口失效"
    }
  },
  "confirmation": {
    "required": ["closed_1m_top_divergence", "failed_pressure_hold"],
    "current": ["near_pressure"],
    "missing": ["closed_1m_top_divergence"],
    "bar_status": "FORMING"
  },
  "guards": [
    {"guard": "BASE_POSITION_REQUIRED", "status": "PASS"},
    {"guard": "SELLABLE_QTY_REQUIRED", "status": "PASS"},
    {"guard": "FRESH_QMT_BAR_REQUIRED", "status": "WATCH"}
  ],
  "action_label": "观察减T窗口",
  "next_step": "等待1分钟收线确认背驰或压力区失败",
  "invalid_if": "1分钟放量站稳压力区上方，或30分钟上攻继续扩展",
  "dedupe_key": "1:sh.600519:intraday_t:T_SELL_WINDOW:2026-04-29:30m_pressure",
  "disclaimer": "仅供参考，不构成投资建议"
}
```

## Command Deck Consumption

The Command Deck should group intraday T events by user task, not by algorithm name.

Recommended queue groups:

| Queue Group | Includes |
|---|---|
| `临界变盘` | `NEAR_INFLECTION`, parent boundary approaching |
| `T窗口` | `T_SELL_WINDOW`, `T_BUYBACK_WINDOW` |
| `小级别确认` | `SMALL_STRUCTURE_CONFIRM`, child-level confirmation waiting |
| `持仓防线` | parent defense, stop, invalidation, T blocked by position risk |
| `观察池` | candidates/watchlist not yet action-grade |
| `复核/失效` | stale, blocked, invalidated, expired |

Command Deck detail panel should show:

- why this is in the queue
- sell-window vs buyback-window
- status: approaching / confirmed / blocked / invalidated
- parent level and child level
- pressure/support zone
- confirmation evidence and missing evidence
- invalidation condition
- response buttons: `观察`, `已处理`, `忽略`, `失效`, `去雷达`

Command Deck must not:

- recompute Chan structure
- turn forming bars into confirmed events
- hide guard failures
- show "buy/sell now" language

## Coach Event Mapping

Intraday T plans should create coach events with structured evidence.

Recommended mapping:

| Intraday T Status | Coach Event Type |
|---|---|
| `APPROACHING` | `ALERT_CANDIDATE_CREATED` or `STRATEGY_EVALUATED` |
| `CONFIRMED` | `STRATEGY_TRIGGERED` |
| `BLOCKED` | `DATA_STALE_BLOCKED` or `RISK_NOTE_RECORDED` |
| `INVALIDATED` | `PLAN_INVALIDATED` |
| `RESPONDED` | `USER_MARKED_ACTION` |

Event evidence must include:

- `event_type`
- `status`
- `levels`
- `zones`
- `confirmation`
- `guards`
- `source`
- `dedupe_key`

## Behavior Review Loop

The behavior system should not only evaluate executed trades. It should also evaluate missed or ignored T events.

Track:

| Metric | Why |
|---|---|
| `confirmed_t_events` | how many action-grade windows appeared |
| `responded_t_events` | whether user responded |
| `ignored_confirmed_events` | discipline / hesitation signal |
| `acted_without_confirmed_event` | planless trading signal |
| `blocked_event_overrides` | user acted despite risk/data block |
| `post_event_outcome` | whether response was helpful after N bars / end of day |

This is how CT-OS becomes a coach instead of another signal screen.

## Implementation Sequence

### Phase 1: Contract Only

- Add this document.
- Update panel redesign doc queue groups.
- No database change.
- No new algorithm.

### Phase 2: Domain Types and Tests

- Add `INTRADAY_T` plan type.
- Add typed payload helpers for `T_SELL_WINDOW` and `T_BUYBACK_WINDOW`.
- Unit test guard mapping and status mapping.
- Ensure payload always carries disclaimer.

### Phase 3: Strategy Engine Output

- Extend Radar/Strategy output with intraday T plan candidates.
- Use BaoStock formal structure for parent context.
- Use QMT closed bars for intraday confirmation when available.
- Emit `APPROACHING` for forming-bar preview only.

### Phase 4: Command Deck Integration

- Read intraday T plan candidates into `/api/playbook/today`.
- Group into `临界变盘 / T窗口 / 小级别确认 / 持仓防线 / 观察池 / 复核/失效`.
- Record user response through existing playbook response path.

### Phase 5: Behavior Report

- Join playbook responses, trades, and outcomes.
- Show plan-following metrics.
- Flag ignored confirmed events and planless intraday trades.

## Test Plan

Minimum tests when implementation starts:

```text
CODE PATH COVERAGE
==================
[ ] Strategy guard evaluation
    ├── base position missing -> T_BLOCKED
    ├── no sellable quantity -> T_BLOCKED
    ├── stale QMT bar -> APPROACHING or BLOCKED, never CONFIRMED
    ├── forming 1m bar -> APPROACHING
    └── closed 1m confirmation -> CONFIRMED

[ ] Command Deck ingestion
    ├── T_SELL_WINDOW appears in T窗口
    ├── T_BUYBACK_WINDOW appears in T窗口
    ├── NEAR_INFLECTION appears in 临界变盘
    ├── T_BLOCKED appears in 复核/失效 or 持仓防线
    └── user response writes coach event

[ ] Behavior review
    ├── trade linked to playbook item -> plan-following
    ├── trade without confirmed event -> planless trade
    └── confirmed event ignored -> missed response metric
```

## Engineering Review Notes

- [Layer 1] Reuse existing `RadarContract`, `StrategyContract`, and `CoachEventContract`.
- [Layer 1] Reuse existing `/api/playbook/today` response path before adding another queue API.
- [Layer 1] Reuse QMT read-only bridge and closed-bar semantics from `QMT_REALTIME_RADAR_INTEGRATION_PLAN.md`.
- Complexity smell to avoid: a second `intraday_t_engine` inside the frontend or playbook API.
- Reversible rollout: first surface `APPROACHING` and `BLOCKED`; only enable `CONFIRMED` after QMT closed-bar freshness is tested.
