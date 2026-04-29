# CT-OS V4.0 Execution Intent Contract

> Created: 2026-04-24
> Scope: private Phase 3 QMT execution only
> Depends on: `docs/ARCHITECTURE_FOUNDATION_DECISIONS.md`, `docs/DATA_SOURCE_CONTRACT.md`, `docs/STRATEGY_CONTRACT.md`

This contract defines the boundary between strategy decisions and private QMT execution.

An execution intent is not an order. It is a structured request candidate that must pass Risk Gate before the Windows QMT Agent can submit anything to QMT.

## Core Boundary

Execution is private Phase 3 infrastructure.

Public Phase 1/2 must not expose:

- execution intent creation
- QMT agent setup
- order submission
- account custody
- hosted trading

Execution path:

```text
Strategy output
  -> execution intent candidate
  -> Execution Intent
  -> Risk Gate
  -> approved intent
  -> Windows QMT Agent
  -> QMT order
  -> Execution Audit Log
```

CT-OS Core must not directly call QMT.

Strategies must not directly call QMT.

The Windows QMT Agent is the only component that can talk to the QMT client.

## Intent Status

Allowed statuses:

| Status | Meaning |
|---|---|
| `DRAFT` | created but not risk checked |
| `PENDING_RISK` | waiting for Risk Gate |
| `REJECTED` | failed risk checks |
| `APPROVED` | ready for Windows Agent pickup |
| `SENT_TO_AGENT` | delivered or pulled by agent |
| `ORDER_SUBMITTED` | QMT accepted order request |
| `PARTIALLY_FILLED` | some shares filled |
| `FILLED` | fully filled |
| `CANCEL_REQUESTED` | cancel requested |
| `CANCELLED` | cancelled |
| `FAILED` | failed before completion |
| `EXPIRED` | no longer valid |
| `DRY_RUN_RECORDED` | simulated only |

Only `APPROVED` intents may be consumed by Windows QMT Agent.

## Intent Shape

Recommended shape:

```json
{
  "intent_id": "exec_20260424_000001",
  "idempotency_key": "1:agent-a:sh.600519:intraday_t_base_position:20260424:001",
  "status": "PENDING_RISK",
  "dry_run": true,
  "user_id": 1,
  "agent_id": "windows-qmt-home",
  "account_id": "qmt-account-alias",
  "symbol": "sh.600519",
  "side": "SELL",
  "quantity": 100,
  "price_type": "LIMIT",
  "limit_price": null,
  "price_policy": {
    "source": "QMT_QUOTE_REQUIRED",
    "method": "best_bid_or_better",
    "max_slippage_pct": 0.002
  },
  "time_in_force": "DAY",
  "expires_at": "2026-04-24T14:55:00+08:00",
  "strategy": {
    "strategy_id": "intraday_t_base_position",
    "strategy_version": "1.0.0",
    "plan_id": "intraday_t_sell_high"
  },
  "reason": {
    "condition_id": "intraday_upper_band_touch",
    "evidence": {}
  },
  "risk_checks": [],
  "created_at": "2026-04-24T10:35:00+08:00"
}
```

Required fields:

| Field | Meaning |
|---|---|
| `intent_id` | unique intent ID |
| `idempotency_key` | duplicate submission protection |
| `dry_run` | whether this is simulated only |
| `user_id` | owner |
| `agent_id` | target Windows QMT Agent |
| `account_id` | private account alias |
| `symbol` | canonical internal symbol |
| `side` | `BUY` or `SELL` |
| `quantity` | share quantity |
| `price_type` | `LIMIT`, `MARKET`, or `POLICY` |
| `price_policy` | how executable price is derived |
| `strategy` | source strategy metadata |
| `reason` | deterministic trigger evidence |

## Side And Quantity

Allowed `side` values:

```text
BUY
SELL
```

Quantity rules:

- Must be positive.
- Must be valid for A-share lot rules where applicable.
- Must respect available cash/position from QMT.
- Must respect base-position protection.
- Must respect daily strategy limits.

For intraday T:

- `SELL` quantity must not break protected base position.
- `BUY` quantity must not exceed cash and configured risk budget.
- A buy-back plan should be linked to the sell intent when strategy requires paired T behavior.

## Price Rules

Execution prices must come from QMT quote/order context.

Forbidden:

```json
{
  "limit_price": 12.34,
  "source": "baostock_front_adjusted_structure_price"
}
```

Allowed:

```json
{
  "price_policy": {
    "source": "QMT_QUOTE_REQUIRED",
    "method": "best_bid_or_better",
    "max_slippage_pct": 0.002
  }
}
```

`limit_price` may be populated only after QMT quote freshness is checked and Risk Gate approves the executable price.

Tencent quote may not be used as execution price.

BaoStock/TDX structure price may be used as strategy evidence, not as order price.

## Risk Gate Contract

Every intent must pass Risk Gate.

Risk check shape:

```json
{
  "check_id": "base_position_guard",
  "status": "PASS",
  "message": "selling 100 shares keeps protected base position",
  "evidence": {
    "current_position": 1000,
    "protected_base_position": 800,
    "sell_quantity": 100
  }
}
```

Allowed statuses:

```text
PASS
FAIL
WARN
BLOCKED
```

Minimum checks:

- user authorization
- agent online
- QMT account connected
- quote freshness
- account freshness
- market session open
- symbol allowed
- base position guard
- available cash or available shares
- max single order amount
- max daily trades
- max daily loss
- max position exposure
- duplicate idempotency key
- kill switch status

If any required check is `FAIL` or `BLOCKED`, the intent cannot become `APPROVED`.

## Dry-Run

Dry-run is required before live execution.

Dry-run intent:

- performs all deterministic strategy checks
- performs all Risk Gate checks using available quote/account snapshots
- records simulated order request
- records simulated result
- does not call QMT order API

Dry-run status should end as:

```text
DRY_RUN_RECORDED
```

Live mode must not be enabled until dry-run is verified for the strategy and symbol class.

## Kill Switch

Execution Layer must support:

- global kill switch
- per-agent kill switch
- per-symbol kill switch
- automatic kill on stale QMT data
- automatic kill on repeated order failures
- automatic kill on abnormal slippage

When kill switch is active:

- new intents cannot be approved
- approved but unsent intents should be expired or cancelled
- Windows QMT Agent should stop polling executable intents or refuse execution

## Windows QMT Agent Contract

Windows QMT Agent responsibilities:

- connect to QMT / XtQuant
- report heartbeat
- report capabilities
- report account snapshot
- report position snapshot
- pull approved intents or receive approved intents
- submit orders to QMT
- submit cancels to QMT
- report order status
- report fills
- write local logs
- return execution events to CT-OS Core

Windows Agent must not:

- run strategy logic
- call `chan.py`
- rewrite Risk Gate decisions
- execute intents for another user/account
- expose public HTTP endpoints without secure network controls

Recommended network model:

```text
Windows Agent polls cloud for approved intents.
```

This avoids exposing Windows inbound ports.

## Idempotency

Every intent must include `idempotency_key`.

Rules:

- Same key cannot submit multiple live QMT orders.
- Retries must reuse the same key.
- Agent must return existing result if it sees a duplicate key.
- Cloud must reject duplicate approved live intents.

Recommended key parts:

```text
user_id:agent_id:symbol:strategy_id:trade_date:sequence
```

## Audit Log

Every execution step must be auditable.

Event shape:

```json
{
  "event_id": "exe_evt_000001",
  "intent_id": "exec_20260424_000001",
  "event_type": "RISK_CHECK_COMPLETED",
  "status": "PASS",
  "payload": {},
  "created_at": "2026-04-24T10:35:01+08:00"
}
```

Required event types:

| Event | Meaning |
|---|---|
| `INTENT_CREATED` | intent was created |
| `RISK_CHECK_STARTED` | risk gate started |
| `RISK_CHECK_COMPLETED` | risk gate result |
| `INTENT_APPROVED` | approved for execution |
| `INTENT_REJECTED` | rejected |
| `AGENT_PICKED_UP` | Windows Agent received intent |
| `ORDER_REQUESTED` | QMT order request attempted |
| `ORDER_ACCEPTED` | QMT accepted order request |
| `ORDER_REJECTED` | QMT rejected order request |
| `FILL_REPORTED` | fill received |
| `CANCEL_REQUESTED` | cancel attempted |
| `CANCEL_CONFIRMED` | cancel confirmed |
| `INTENT_FAILED` | failed |
| `KILL_SWITCH_TRIGGERED` | execution stopped |

Audit logs must include:

- `strategy_id`
- `strategy_version`
- `user_id`
- `agent_id`
- `account_id`
- `symbol`
- `data_source`
- `price_source`
- risk check results

## Public Product Exclusion

Public Phase 1/2 must not show or expose:

- QMT agent setup
- live execution toggles
- execution intent APIs
- order placement APIs
- account custody features

Private owner deployment may enable Phase 3 modules behind explicit configuration.

## Required Tests

Initial execution tests should cover:

- front-adjusted structure price is rejected as order price
- intent cannot approve when QMT quote is stale
- intent cannot approve when kill switch is active
- duplicate idempotency key cannot submit twice
- dry-run does not call QMT order API
- strategy can create execution candidate but not order
- Windows Agent cannot execute intent for wrong user/account

## Open Decisions

- Whether Windows Agent polls cloud or receives pushed intents.
- Exact QMT/XtQuant account API shape after local testing.
- Whether execution logs are stored only in `ctos.db` or also locally on Windows.
- Whether paired intraday T intents are represented as linked intents or a parent order group.
