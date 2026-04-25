# CT-OS V4.0 Product Roadmap

> Created: 2026-04-24
> Purpose: define the product stages, boundaries, non-goals, and acceptance criteria before architecture refactoring.

CT-OS is built in three stages:

1. Chan analysis workbench
2. Strategy coach and alerts
3. Private QMT intraday T execution

Phase 1 and Phase 2 can become a multi-user product. Phase 3 is private execution infrastructure and is not part of the public product.

## Product Principle

CT-OS starts as a trading coach, not a trading robot.

The system records, explains, compares, warns, and reminds. It does not execute trades in Phase 1 or Phase 2.

Automatic trading only belongs to Phase 3, and must be isolated behind Execution Intent, Risk Gate, QMT Adapter, and Audit Log.

## Phase 1: Chan Analysis Workbench

### Goal

Make one stock understandable.

The user should be able to open a stock, see its K-line chart, see Chan structures, and understand the current multi-level structure through Radar.

### Core User Jobs

- View K-line chart with Chan overlays.
- Inspect pens, segments, centers, buy/sell points, and divergence markers.
- Compare daily, 60m, 30m, 15m, and 5m structures.
- See whether data is fresh, which source was used, and which adjustment mode was used.
- Switch between empty-position and holding-position views without mixed signals.

### System Scope

- Use `chan.py` as the only authority for basic Chan structures.
- Use BaoStock multi-level K-lines for Radar/Chan structure analysis.
- Use TDX daily K-lines for scanner preparation only, not for Radar multi-level structure.
- Use Tencent quotes for current price and real-time preview only.
- Expose stable Radar API contract before rewriting UI.

### Non-Goals

- No automatic trading.
- No QMT integration.
- No strategy execution.
- No user-configurable strategy builder.
- No AI-generated trading rules.

### Acceptance Criteria

- K-line chart can render `chan.py` pens, segments, centers, and buy/sell point markers.
- Radar can show multi-level structure with `data_source`, `adjustflag`, and `freshness`.
- Empty mode only shows entry-related plans.
- Holding mode only shows holding management.
- Formal structure analysis does not use real-time Tencent preview K-lines.
- The UI can keep working while `/api/chan/matrix/v2` remains as a compatibility endpoint.

## Phase 2: Strategy Coach And Alerts

### Goal

Turn the user's planned trading strategies into structured plans, candidate lists, and reminders.

The system should surface which stocks deserve attention, what condition is being watched, and what the user should check when a rule triggers.

### Core User Jobs

- Run daily scanner and see candidate stocks.
- Add scanner candidates to watchlist.
- Open Radar and see strategy-specific plans.
- Compare holdings and candidates in Rotation Compass.
- Receive reminders when watched conditions trigger.
- Review why a reminder was triggered and what happened later.

### System Scope

- Define strategies through `Strategy Contract`.
- Keep strategy rules in Decision Engine, not UI components.
- Generate plans and alerts from structured Chan facts.
- Keep AI as narrative only, never as rule authority.
- Record strategy triggers and reminders in Coach/Event Log.
- Support multi-user Phase 1/2 product boundaries.

### Non-Goals

- No automatic order placement.
- No hosted QMT for public users.
- No discretionary AI buy/sell decision.
- No strategy that bypasses data freshness checks.

### Acceptance Criteria

- Strategy definitions have `strategy_id`, `strategy_version`, inputs, conditions, plans, alerts, and risk fields.
- Scanner, Radar, Rotation, and Push consume the same Strategy Contract shape.
- Every alert can be traced to a strategy trigger, structure snapshot, data source, and user.
- Stale data cannot trigger trading-action reminders.
- User-specific watchlist, positions, strategy configs, alerts, and coach events are isolated by `user_id`.
- Public multi-user mode includes Phase 1/2 only.

## Phase 3: Private QMT Intraday T Execution

### Goal

Execute intraday T strategies on stocks with existing base positions through the user's private QMT setup.

This phase is for the owner's private deployment only. It is not part of the public Phase 1/2 product.

### Deployment Shape

```text
Cloud or Mac CT-OS Core
  -> Execution Intent
  -> Risk Gate
  -> QMT Bridge
  -> Windows QMT Agent
  -> XtQuant / QMT Client
```

The QMT client runs on a separate Windows computer. CT-OS Core must not directly call QMT.

### Core User Jobs

- Authorize specific symbols for intraday T.
- Confirm base position exists.
- Run strategies in dry-run before real trading.
- Set daily risk limits.
- Stop all automated execution quickly.
- Review every intent, order, fill, cancel, and failure.

### System Scope

- Generate Execution Intent only after strategy conditions are met.
- Run every intent through Risk Gate.
- Use QMT quotes and account state for execution decisions.
- Use QMT order and fill reports as execution truth.
- Record everything in Execution Audit Log.

### Non-Goals

- No public QMT service.
- No shared QMT execution node.
- No trading for external users.
- No execution based on front-adjusted structure prices.
- No strategy code directly calling QMT.

### Acceptance Criteria

- QMT integration is isolated in Windows QMT Agent.
- CT-OS Core only sends approved intents or stores intents for agent polling.
- Every intent has idempotency key, user/account scope, strategy version, and risk checks.
- Dry-run mode exists before live mode.
- Kill switch supports global stop and per-symbol stop.
- Execution price comes from QMT quote/order context, not BaoStock front-adjusted K-lines.
- Audit log can reconstruct every automated action.

## Public Product Boundary

The public product only includes:

- Chan analysis workbench
- Strategy coach
- Scanner
- Watchlist
- Alerts
- Behavior review
- Optional AI narrative

The public product excludes:

- QMT setup
- Automatic order placement
- Account custody
- Hosted trading
- Shared execution infrastructure

## Architecture Implications

- `chan.py` adapter is the structure authority.
- Data source rules must be defined before Radar contract.
- Strategy Contract must exist before expanding scanner, rotation, and push.
- Coach/Event Log must exist before serious behavior review.
- Execution Intent Contract must exist before any QMT code.
- Multi-user architecture applies to Phase 1/2, not Phase 3.

## First Build Path

1. Lock foundation decisions.
2. Lock data source contract.
3. Lock strategy contract.
4. Lock Radar API contract.
5. Add Radar compatibility API.
6. Move UI to stable contract.
7. Split `chan_service.py` behind tests.
8. Build Strategy Coach and event logs.
9. Add QMT execution only as private Phase 3 infrastructure.
