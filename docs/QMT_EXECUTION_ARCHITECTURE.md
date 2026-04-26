# CT-OS V4.0 QMT Execution Architecture

> Created: 2026-04-26
> Scope: private Phase 3 QMT intraday T execution reserve
> Depends on: `docs/EXECUTION_INTENT_CONTRACT.md`, `docs/STRATEGY_CONTRACT.md`, `docs/DATA_SOURCE_CONTRACT.md`

This document defines the execution architecture boundary for future private QMT integration.

Phase 1 and Phase 2 remain trading coach products. They record, analyze, remind, and help the user review decisions. They must not place orders.

Phase 3 may execute intraday T only inside a private owner-controlled deployment, and only through the Execution Layer described here.

## Non-Negotiable Boundary

CT-OS Core must not directly call QMT or XtQuant.

Strategy code must not directly call QMT or XtQuant.

Structure Engine and Decision Engine must not submit orders.

LLM output must not create executable orders.

The only allowed live path is:

```text
Strategy candidate
  -> Execution Intent
  -> Risk Gate
  -> approved intent
  -> Windows QMT Agent
  -> QMT Adapter
  -> QMT / XtQuant
  -> Execution Audit Log
```

`docs/EXECUTION_INTENT_CONTRACT.md` defines the intent shape. This document defines the runtime responsibilities around it.

## Component Responsibilities

### Execution Layer

The Execution Layer owns execution workflow state.

It may:

- create execution intents from approved Phase 3 strategy candidates
- store intents and status transitions
- call Risk Gate
- expose approved intents to the Windows QMT Agent
- record execution audit events
- expire stale or unsafe intents
- enforce dry-run/live mode configuration

It must not:

- calculate Chan structure
- re-score strategy conditions
- derive executable prices from front-adjusted K-lines
- bypass Risk Gate
- talk directly to QMT

### Risk Gate

Risk Gate is the only component allowed to approve an intent for live execution.

Required checks:

- user explicitly authorized this `symbol`
- private execution mode is enabled
- dry-run qualification exists for the strategy and symbol class
- global kill switch is off
- agent kill switch is off
- symbol kill switch is off
- Windows QMT Agent heartbeat is fresh
- QMT quote snapshot is fresh
- QMT account snapshot is fresh
- market session is open and allowed by the strategy
- symbol is tradable and not blocked
- current base position is confirmed
- sell quantity keeps protected base position
- buy quantity fits available cash
- order amount is within max single order amount
- daily trade count is within limit
- realized and simulated daily loss are within limit
- expected slippage is within limit
- idempotency key is not already used for a live order

Any `FAIL` or `BLOCKED` result prevents approval.

Warnings may be recorded, but live approval requires every required check to be `PASS`.

### Windows QMT Agent

The Windows QMT Agent runs on the user's private Windows machine with QMT installed.

It may:

- authenticate to CT-OS Core using a private agent token
- publish heartbeat and capabilities
- publish QMT account snapshots
- publish QMT position snapshots
- publish QMT quote snapshots
- pull approved intents
- submit orders through QMT Adapter
- submit cancel requests through QMT Adapter
- report order state and fills
- write local execution logs

It must not:

- run strategy logic
- call `chan.py`
- override Risk Gate decisions
- execute an intent for the wrong `user_id`, `account_id`, or `agent_id`
- accept public inbound requests without explicit secure network controls

Preferred network model:

```text
Windows QMT Agent polls CT-OS Core for approved intents.
```

Polling avoids opening inbound ports on the Windows machine.

### QMT Adapter

The QMT Adapter is a thin wrapper around QMT / XtQuant.

It may only provide:

- account query
- position query
- quote query
- order submit
- order cancel
- order status query
- fill report query or callback normalization

It must not:

- inspect Chan structure
- call radar, scanner, rotation, or LLM services
- decide whether a strategy condition is true
- decide position sizing beyond adapter-level lot normalization
- convert front-adjusted structure prices into order prices

Executable prices must come from QMT quote/order context.

### Execution Audit Log

Every execution transition must be append-only and auditable.

Required event classes:

- intent created
- risk checks started
- risk checks completed
- intent approved
- intent rejected
- dry-run order simulated
- agent picked up intent
- order request created
- order request accepted
- order request rejected
- fill reported
- cancel requested
- cancel confirmed
- intent failed
- intent expired
- kill switch triggered

Every audit event must include:

- `intent_id`
- `idempotency_key`
- `user_id`
- `agent_id`
- `account_id`
- `symbol`
- `strategy_id`
- `strategy_version`
- `data_source`
- `price_source`
- `risk_check_snapshot`
- raw request/response payload where applicable
- timestamp

Audit records must preserve failed and rejected attempts. Failed attempts are product evidence, not noise.

## Intraday T Preconditions

Intraday T execution is allowed only for symbols with a confirmed protected base position.

Per-symbol configuration must include:

- explicit user authorization for the symbol
- protected base position quantity
- max sell quantity per T leg
- max buy quantity per T leg
- max single order amount
- max daily trade count
- max daily loss
- max slippage percent
- allowed trading sessions
- blocked trading windows
- strategy IDs allowed for the symbol
- dry-run qualification status

Default blocked windows:

- before market open
- midday break
- last 3 minutes before close
- call auction windows unless explicitly enabled
- any period where QMT quote or account snapshot is stale

Base position rule:

```text
available_sell_for_t = current_position - protected_base_position - pending_sell_quantity
```

Risk Gate must reject any sell intent where `available_sell_for_t < intent.quantity`.

## Dry-Run Mode

Dry-run is mandatory before live execution.

Dry-run must:

- create real execution intents with `dry_run = true`
- run all strategy candidate checks
- run all Risk Gate checks
- use QMT quote/account snapshots when available
- record simulated order request
- record simulated order result
- record simulated fill assumptions
- record what would have been rejected
- never call QMT order submit or cancel

Live mode cannot be enabled until:

- dry-run has run for the strategy
- dry-run has run for the symbol class
- dry-run audit logs are reviewed
- kill switch behavior is tested
- duplicate idempotency behavior is tested
- stale quote/account blocking is tested

Recommended dry-run qualification:

```json
{
  "strategy_id": "intraday_t_base_position",
  "symbol_scope": "A_SHARE_MAIN_BOARD",
  "sample_days": 10,
  "min_intents": 30,
  "required_block_tests": [
    "kill_switch",
    "stale_quote",
    "stale_account",
    "duplicate_idempotency",
    "base_position_guard"
  ],
  "status": "QUALIFIED"
}
```

## Kill Switch

Execution must support manual and automatic stop controls.

Manual switches:

- global kill switch
- per-agent kill switch
- per-account kill switch
- per-symbol kill switch
- per-strategy kill switch

Automatic switches:

- QMT quote stale beyond threshold
- account snapshot stale beyond threshold
- agent heartbeat stale beyond threshold
- repeated order submit failures
- repeated QMT reject responses
- slippage beyond configured limit
- daily loss beyond configured limit
- unexpected fill quantity
- intent status stuck beyond timeout

When any relevant kill switch is active:

- new intents cannot become `APPROVED`
- approved but unsent intents must become `EXPIRED` or `CANCEL_REQUESTED`
- Windows Agent must stop submitting new orders
- audit log must record the switch cause

Kill switch state must be checked by both CT-OS Core and Windows QMT Agent.

## Data Freshness Rules

Execution must use QMT executable context.

Allowed execution price sources:

- QMT quote snapshot
- QMT order book snapshot
- QMT order callback context

Forbidden execution price sources:

- BaoStock front-adjusted K-lines
- TDX front-adjusted structure K-lines
- Tencent quote as final order price
- LLM-provided prices
- UI-displayed estimated prices

Structure data may explain why an intent exists. It cannot supply the executable order price.

If QMT quote freshness is stale, Risk Gate must return `BLOCKED`.

If account or position freshness is stale, Risk Gate must return `BLOCKED`.

## Order Lifecycle

Allowed status flow:

```text
DRAFT
  -> PENDING_RISK
  -> REJECTED

DRAFT
  -> PENDING_RISK
  -> APPROVED
  -> SENT_TO_AGENT
  -> ORDER_SUBMITTED
  -> PARTIALLY_FILLED
  -> FILLED

ORDER_SUBMITTED
  -> CANCEL_REQUESTED
  -> CANCELLED

Any active state
  -> FAILED
  -> EXPIRED
```

Dry-run status flow:

```text
DRAFT
  -> PENDING_RISK
  -> DRY_RUN_RECORDED
```

## Idempotency

Every intent must include an idempotency key.

Recommended key:

```text
user_id:agent_id:account_id:symbol:strategy_id:trade_date:sequence
```

Rules:

- one idempotency key maps to at most one live QMT order request
- retries reuse the same key
- duplicate live keys must return the existing order result or be rejected
- dry-run keys must not be reused for live orders
- Windows Agent must persist local key history across restarts

## Security

Private execution must not be exposed in public product mode.

Minimum controls:

- feature flag disabled by default
- private owner deployment only
- per-agent token
- per-account binding
- signed or otherwise authenticated agent requests
- no public QMT credential storage in CT-OS Core
- no QMT account password in git, `.env.example`, logs, or audit payloads
- all order payload logs redact account secrets

## Implementation Order

Phase 3 implementation must follow this order:

1. Define execution DB schema and migrations.
2. Implement dry-run-only Execution Layer.
3. Implement Risk Gate with mocked QMT snapshots.
4. Implement audit log writer.
5. Implement Windows Agent heartbeat and snapshot reporting.
6. Implement QMT Adapter read-only account, position, and quote queries.
7. Run dry-run qualification.
8. Add live order submit behind feature flag.
9. Test kill switch and idempotency under failure.
10. Enable only for explicitly authorized private symbols.

No live order submit code should be written before dry-run, Risk Gate, audit log, and kill switch tests exist.

## Acceptance Criteria

Phase F is complete when:

- `docs/EXECUTION_INTENT_CONTRACT.md` defines intent shape and status.
- this document defines Execution Layer, Risk Gate, Windows Agent, QMT Adapter, and Audit Log boundaries.
- intraday T preconditions are explicit.
- dry-run is mandatory before live QMT.
- kill switch rules are explicit.
- QMT Adapter is restricted to account, position, quote, order, cancel, and fill I/O.
- audit events include strategy, data source, price source, risk checks, and raw execution payloads.
- Phase 1/2 remain trading coach only and expose no QMT execution surface.
