# CT-OS V4.0 Coach Event Contract

> Created: 2026-04-25
> Depends on: `docs/ARCHITECTURE_FOUNDATION_DECISIONS.md`, `docs/DATA_SOURCE_CONTRACT.md`, `docs/STRATEGY_CONTRACT.md`, `docs/RADAR_API_CONTRACT.md`

This contract defines how CT-OS records strategy triggers, alerts, user responses, and later outcomes.

Coach events are the audit trail for Phase 2 Strategy Coach and Alerts. They also provide the historical evidence needed by Behavior Report and future private Phase 3 execution audit.

Coach events do not execute trades.

## Core Boundary

Coach events may record:

- deterministic strategy triggers
- watch conditions
- alert delivery attempts
- user acknowledgement and actions
- structure snapshot references
- stale-data blocks
- later outcomes for review

Coach events must not:

- place orders
- mutate strategy condition outcomes
- let AI rewrite deterministic evidence
- use stale structure to trigger trading-action alerts
- store only prose without structured evidence

Phase 1/2 events are coach-only. Any event or alert that implies a trading action must include "仅供参考".

Phase 3 may reuse the same event discipline for private execution audit, but execution events belong to the Execution Layer and must be linked through `execution_intent_id`.

## Event Flow

```text
Structure snapshot
  -> Strategy condition evaluation
  -> Strategy trigger record
  -> Alert delivery candidate
  -> Alert delivery attempt
  -> User response
  -> Later outcome
  -> Behavior review
```

All steps should be idempotent by `dedupe_key`.

## Tables

Initial minimum tables:

```text
coach_events
strategy_triggers
alert_deliveries
```

Optional future tables:

```text
structure_snapshots
user_responses
coach_event_outcomes
```

The first implementation may keep response and outcome data in JSON columns on `coach_events`. If those fields become hot query paths, split them later.

## coach_events

`coach_events` is the append-only event log.

Recommended shape:

```json
{
  "event_id": "evt_20260425_000001",
  "event_type": "STRATEGY_TRIGGERED",
  "user_id": 1,
  "symbol": "sh.600519",
  "occurred_at": "2026-04-25T10:35:00+08:00",
  "source": "strategy_engine",
  "severity": "WATCH",
  "dedupe_key": "1:sh.600519:war1_third_buy:m5_buy_signal:2026-04-25",
  "strategy": {
    "strategy_id": "war1_third_buy",
    "strategy_version": "1.0.0",
    "plan_id": "war1_wait_5m_confirm",
    "condition_id": "m5_buy_signal"
  },
  "data_source": {
    "structure": "baostock",
    "adjustflag": "2",
    "quote": "tencent"
  },
  "freshness": {
    "is_stale": false,
    "stale_reason": "",
    "last_bar_at": "2026-04-25 10:30:00"
  },
  "structure_ref": {
    "snapshot_id": "snap_20260425_103500_sh600519",
    "api_version": "radar.v1"
  },
  "evidence": {},
  "message": {
    "title": "5 分钟确认信号出现",
    "body": "战法一观察条件触发，请检查日线中枢和 5 分钟买点。仅供参考。"
  },
  "user_response": null,
  "outcome": null,
  "metadata": {}
}
```

Required fields:

| Field | Meaning |
|---|---|
| `event_id` | stable event ID |
| `event_type` | event category |
| `user_id` | owner |
| `symbol` | canonical internal symbol, nullable for account-level events |
| `occurred_at` | event time |
| `source` | producer component |
| `severity` | event importance |
| `dedupe_key` | duplicate protection |
| `strategy` | source strategy metadata when applicable |
| `freshness` | freshness at trigger time |
| `evidence` | structured deterministic evidence |

## Event Types

Allowed initial `event_type` values:

| Event Type | Meaning |
|---|---|
| `STRATEGY_EVALUATED` | strategy conditions evaluated |
| `STRATEGY_TRIGGERED` | deterministic condition triggered |
| `PLAN_INVALIDATED` | plan no longer valid |
| `ALERT_CANDIDATE_CREATED` | alert candidate created |
| `ALERT_DELIVERY_ATTEMPTED` | delivery attempted |
| `ALERT_DELIVERED` | delivery succeeded |
| `ALERT_FAILED` | delivery failed |
| `USER_ACKNOWLEDGED` | user acknowledged an event |
| `USER_MARKED_ACTION` | user recorded a follow-up action |
| `OUTCOME_RECORDED` | later outcome attached |
| `DATA_STALE_BLOCKED` | stale or incomplete data blocked action alert |
| `RISK_NOTE_RECORDED` | non-action risk note recorded |

Phase 3 private extensions:

| Event Type | Meaning |
|---|---|
| `EXECUTION_INTENT_CREATED` | intent created by Execution Layer |
| `RISK_GATE_RESULT` | Risk Gate result recorded |
| `ORDER_AUDIT_EVENT` | QMT order lifecycle audit |

Phase 3 events must link to `execution_intent_id` and follow `docs/EXECUTION_INTENT_CONTRACT.md`.

## Severity

Allowed `severity` values:

| Severity | Meaning |
|---|---|
| `INFO` | background record |
| `WATCH` | user should observe a condition |
| `WARNING` | risk is rising |
| `CRITICAL` | deterministic risk condition triggered |

Severity is not an order instruction. It only controls UI and delivery priority.

## strategy_triggers

`strategy_triggers` stores normalized trigger facts for query and backtest.

Recommended shape:

```json
{
  "trigger_id": "trg_20260425_000001",
  "event_id": "evt_20260425_000001",
  "user_id": 1,
  "symbol": "sh.600519",
  "strategy_id": "war1_third_buy",
  "strategy_version": "1.0.0",
  "plan_id": "war1_wait_5m_confirm",
  "condition_id": "m5_buy_signal",
  "condition_status": "PASS",
  "triggered_at": "2026-04-25T10:35:00+08:00",
  "mode": "EMPTY",
  "data_source": {
    "structure": "baostock",
    "adjustflag": "2"
  },
  "freshness": {
    "is_stale": false
  },
  "evidence": {},
  "dedupe_key": "1:sh.600519:war1_third_buy:m5_buy_signal:2026-04-25"
}
```

Rules:

- A trigger must reference one `coach_events.event_id`.
- `strategy_id` and `strategy_version` are required.
- `condition_status` must come from deterministic strategy evaluation.
- Stale structure may create `DATA_STALE_BLOCKED`, but must not create a trading-action trigger.
- Evidence must be structured enough to explain why the trigger fired later.

## alert_deliveries

`alert_deliveries` records delivery attempts. It does not decide whether a strategy condition is true.

Recommended shape:

```json
{
  "delivery_id": "del_20260425_000001",
  "event_id": "evt_20260425_000001",
  "alert_id": "war1_m5_confirmed",
  "user_id": 1,
  "symbol": "sh.600519",
  "channel": "WECHAT_MINIPROGRAM",
  "status": "DELIVERED",
  "priority": "HIGH",
  "attempt_count": 1,
  "last_attempt_at": "2026-04-25T10:35:05+08:00",
  "delivered_at": "2026-04-25T10:35:06+08:00",
  "expires_at": "2026-04-25T15:00:00+08:00",
  "dedupe_key": "1:sh.600519:war1_third_buy:m5_buy_signal:2026-04-25:wechat",
  "provider_response": {},
  "error": null
}
```

Allowed `channel` values:

| Channel | Meaning |
|---|---|
| `IN_APP` | web or miniprogram in-app notice |
| `WECHAT_MINIPROGRAM` | WeChat subscription message |
| `DAILY_REPORT` | daily report digest |
| `WEBHOOK` | future private integration |

Allowed `status` values:

| Status | Meaning |
|---|---|
| `PENDING` | queued |
| `SKIPPED_DEDUPE` | duplicate suppressed |
| `SKIPPED_STALE` | stale data blocked delivery |
| `SENT` | provider accepted send request |
| `DELIVERED` | delivery confirmed when available |
| `FAILED` | provider or local failure |
| `EXPIRED` | no longer useful |

Rules:

- Delivery must reference a coach event.
- Delivery must use the alert candidate produced by Strategy Contract.
- Push provider responses belong here, not in strategy code.
- Delivery retries must preserve the same `dedupe_key`.

## User Response

User responses record what the user did after an event.

Recommended embedded shape:

```json
{
  "response_type": "ACKNOWLEDGED",
  "responded_at": "2026-04-25T10:42:00+08:00",
  "action": {
    "type": "NO_TRADE",
    "trade_id": null,
    "note": "等待 30 分钟回踩确认"
  }
}
```

Allowed `response_type` values:

| Response Type | Meaning |
|---|---|
| `ACKNOWLEDGED` | user saw the reminder |
| `DISMISSED` | user dismissed it |
| `MARKED_DONE` | user says the planned check is done |
| `MARKED_NO_ACTION` | user chose not to act |
| `LINKED_TRADE` | user linked a trade record |
| `MUTED_STRATEGY` | user muted this strategy |

Allowed action `type` values:

| Action Type | Meaning |
|---|---|
| `NO_TRADE` | user intentionally did not trade |
| `BUY_RECORDED` | user linked a buy trade |
| `SELL_RECORDED` | user linked a sell trade |
| `REDUCE_RECORDED` | user linked a reduce-position trade |
| `EXIT_RECORDED` | user linked an exit trade |
| `WATCH_CONTINUED` | user keeps watching |

The system must not infer a brokerage trade from acknowledgement alone.

## Outcome

Outcomes attach later results for behavior review.

Recommended embedded shape:

```json
{
  "outcome_type": "PRICE_REACHED_TARGET",
  "evaluated_at": "2026-04-30T15:30:00+08:00",
  "window": "5 trading days",
  "evidence": {
    "entry_reference": 12.34,
    "max_price": 13.20,
    "min_price": 12.05,
    "structure_invalidated": false
  }
}
```

Allowed `outcome_type` values:

| Outcome Type | Meaning |
|---|---|
| `PENDING` | not evaluated yet |
| `PRICE_REACHED_TARGET` | target/reference condition was reached |
| `STOP_REFERENCE_BROKEN` | structural or risk reference broke |
| `PLAN_INVALIDATED` | plan became invalid |
| `USER_FOLLOWED` | linked user action matched plan |
| `USER_IGNORED` | event was delivered but no response/action |
| `NO_LONGER_RELEVANT` | watch window expired |

Outcome rules:

- Outcome evaluation must use explicit windows.
- Behavior Report must show whether data was stale at trigger time.
- Outcome evidence must not be overwritten by AI narrative.

## Structure Snapshot Reference

Events should reference the structure state used at trigger time.

Recommended shape:

```json
{
  "snapshot_id": "snap_20260425_103500_sh600519",
  "api_version": "radar.v1",
  "symbol": "sh.600519",
  "mode": "EMPTY",
  "as_of": "2026-04-25T10:35:00+08:00",
  "hash": "sha256:...",
  "storage": "radar_deductions"
}
```

Rules:

- Formal structure must come from Radar/Chan contract output.
- Snapshot references are preferred over copying the full Radar payload into every event.
- If no snapshot table exists yet, store a compact `structure_ref` and the minimum evidence fields required for audit.
- The snapshot must preserve `data_source` and `freshness`.

## Freshness And Stale Data

Stale data handling is mandatory.

Rules:

- `freshness.is_stale = true` blocks trading-action alerts.
- Stale structure may still create `DATA_STALE_BLOCKED`.
- Stale events may be shown as data quality notices.
- Stale events must not be used as proof that a strategy condition triggered.
- Alert delivery status should be `SKIPPED_STALE` when freshness blocks delivery.

Example:

```json
{
  "event_type": "DATA_STALE_BLOCKED",
  "severity": "WARNING",
  "freshness": {
    "is_stale": true,
    "stale_reason": "LEVEL_INCOMPLETE"
  },
  "message": {
    "title": "结构数据未完成",
    "body": "BaoStock 5 分钟级别数据不完整，暂不触发交易动作提醒。"
  }
}
```

## Dedupe Key

`dedupe_key` prevents duplicate events and duplicate pushes.

Recommended format:

```text
{user_id}:{symbol}:{strategy_id}:{condition_id}:{trading_day}
```

For alert delivery:

```text
{user_id}:{symbol}:{strategy_id}:{condition_id}:{trading_day}:{channel}
```

Rules:

- Dedupe must be stable across retries.
- Different channels may have separate delivery dedupe keys.
- Different strategy versions may be included when version behavior changes trigger meaning.
- Plan invalidation events should use the invalidated `plan_id`.

## Disclaimer

Any event, alert, or message that implies buy, sell, reduce, exit, add, stop, or position sizing must include:

```text
仅供参考，不构成投资建议
```

Short push copy may use:

```text
仅供参考
```

Messages that only describe data quality or system status do not require the trading disclaimer.

## AI Boundary

AI may:

- summarize event history
- explain deterministic evidence in plain language
- group behavior patterns for review

AI must not:

- create strategy triggers
- alter `condition_status`
- alter `freshness`
- invent a user response
- invent an outcome
- replace structured evidence with prose

## Initial Test Coverage

Initial implementation tests should cover:

- strategy trigger creates one coach event and one trigger row
- duplicate trigger uses `dedupe_key` and does not create duplicate delivery
- stale freshness creates `DATA_STALE_BLOCKED` and no trading-action delivery
- action-related alert copy includes "仅供参考"
- user acknowledgement does not create a trade
- linked trade response references an existing trade ID
- event stores `strategy_id` and `strategy_version`
- alert retry preserves delivery dedupe key

## Open Decisions

- Whether `structure_snapshots` should be a dedicated table or reuse `radar_deductions`.
- Whether user response should start embedded in `coach_events` or split into `user_responses`.
- Exact retention policy for alert provider response payloads.
- Whether daily report digest should aggregate events by event time or by trading day.
