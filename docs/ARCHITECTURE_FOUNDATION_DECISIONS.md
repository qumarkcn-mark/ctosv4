# CT-OS V4.0 Architecture Foundation Decisions

> Created: 2026-04-24
> Purpose: record foundation decisions that future contracts, refactors, code reviews, and tests must follow.

This document is the baseline for rebuilding CT-OS. If an implementation conflicts with this document, the implementation is wrong unless this document is explicitly revised first.

## D1: Product Stages

CT-OS has three stages:

1. Phase 1: Chan Analysis Workbench
2. Phase 2: Strategy Coach and Alerts
3. Phase 3: Private QMT Intraday T Execution

Phase 1 and Phase 2 can become a public multi-user product.

Phase 3 is private execution infrastructure and must not be exposed to public users.

## D2: Trading Coach Boundary

Phase 1 and Phase 2 are trading coach features.

They may:

- show structures
- generate plans
- compare candidates
- create alerts
- explain why a condition triggered
- record user behavior

They must not:

- place orders
- submit cancels
- control QMT
- act as hosted trading infrastructure
- let AI invent buy/sell rules

All trading-action language in Phase 1 and Phase 2 must include "仅供参考".

## D3: QMT Execution Boundary

QMT automation only belongs to Phase 3.

QMT must be isolated behind:

```text
Execution Intent -> Risk Gate -> QMT Bridge -> Windows QMT Agent -> QMT Client -> Audit Log
```

CT-OS Core must not directly call QMT.

Strategy code must not directly call QMT.

The Windows QMT Agent is the only component allowed to talk to the QMT client.

## D4: Public Product Boundary

The public product includes:

- Chan analysis workbench
- Radar
- scanner
- watchlist
- strategy coach
- alerts
- behavior review
- optional AI narrative

The public product excludes:

- QMT setup
- automatic order placement
- execution intent submission
- account custody
- hosted trading
- shared execution infrastructure

## D5: Chan Structure Authority

`chan.py` is the only authority for basic Chan structures.

CT-OS must not maintain a second authoritative implementation of:

- K-line inclusion
- fractals
- pens
- segments
- centers
- raw buy/sell point structure

Allowed CT-OS logic:

- adapt `chan.py` output to stable contracts
- classify strategies from structure facts
- generate coach plans
- generate alerts
- render UI
- produce narrative from structured data

All `chan.py` integration must go through a future adapter:

```text
server/engines/structure/chan_adapter.py
```

## D6: Vendor Strategy

`server/vendor/chan_py` is third-party vendor code.

Rules:

- Do not directly edit vendor source for CT-OS behavior changes.
- Put all CT-OS-specific mapping, normalization, and compatibility logic in `chan_adapter.py`.
- Before upgrading vendor code, run adapter contract tests.
- If vendor behavior changes, update the adapter or contracts deliberately.

## D7: Data Source Authority

Each data source has a specific role.

| Use case | Authority | Notes |
|---|---|---|
| Scanner full-market daily scan | TDX lake | `freq=day`, `adjustflag=3`, local `.day` import |
| Radar/Chan formal structure | BaoStock lake | multi-level K-lines, `adjustflag=2` |
| UI current price | Tencent quote | preview and display only |
| UI real-time preview K-line | Tencent quote/minute K | preview only |
| Normal price alerts | Tencent quote | Phase 1/2 reminder source |
| QMT execution price | QMT quote/order context | Phase 3 only |
| Account, positions, orders, fills | QMT account callbacks | Phase 3 only |

Formal `chan.py` structure analysis must not use Tencent real-time preview K-lines.

Scanner may use TDX as candidate discovery, but Radar remains the deep structure view.

## D8: No Mixed Structure Inputs

One `chan.py` structure analysis must use one data source and one adjustment mode.

Forbidden:

```text
day from TDX + 30m from BaoStock + 5m from BaoStock
```

Required for Radar:

```text
day/week/minute levels all from BaoStock lake with the same intended adjustment mode
```

If data is incomplete or stale, the system must expose freshness status instead of silently mixing sources.

## D9: Symbol Standard

Internal symbol format:

```text
sh.600519
sz.000001
```

API inputs may accept:

```text
sh600519
sh.600519
sh-600519
```

External conversions:

| Target | Format |
|---|---|
| Internal/domain/data | `sh.600519` |
| Tencent quote API | `sh600519` |
| TDX file name | `sh600519.day` |
| Display | symbol plus optional stock name |

All API entry points should normalize symbols before calling data, structure, decision, or execution code.

## D10: Price And Adjustment Modes

Scanner:

```text
TDX unadjusted daily bars, adjustflag=3
```

Radar/Chan structure:

```text
BaoStock front-adjusted bars, adjustflag=2
```

Trading facts:

```text
actual trade price, actual cost, actual current price
```

The following must use real trading price units:

- `trades.price`
- `positions.avg_cost`
- `positions.current_price`
- `positions.stop_loss_price`
- `positions.trailing_stop_price`
- execution intent price fields
- QMT order prices

Front-adjusted structure prices may guide structure interpretation, but must not directly become order prices.

## D11: Execution Price Rule

Any Phase 3 execution intent must use QMT quote/order context for executable prices.

Forbidden:

```text
limit_price = BaoStock front-adjusted structure price
```

Allowed:

```text
structure condition triggered -> QMT quote checked -> risk gate approved -> executable price derived from QMT context
```

## D12: Freshness Contract

All formal analysis contracts should expose data freshness:

```json
{
  "source": "baostock",
  "adjustflag": "2",
  "last_bar_at": "2026-04-24 15:00:00",
  "is_stale": false,
  "stale_reason": ""
}
```

Rules:

- Stale data must not trigger trading-action reminders.
- Stale data may trigger data-quality reminders.
- Scanner must know whether TDX daily sync completed.
- Radar must know whether BaoStock levels are fresh enough.
- Execution must know whether QMT quote/account state is fresh.

## D13: Structure, Decision, Narrative, Execution

The architecture must keep these layers separate.

| Layer | Responsibility |
|---|---|
| Structure | What happened on the chart |
| Decision | What the coach watches or plans |
| Narrative | How to explain structured facts |
| Execution | How to submit orders in Phase 3 |

Structure outputs facts.

Decision consumes facts and creates plans/alerts.

Narrative consumes facts/plans and writes user-facing text.

Execution consumes approved intents only.

## D14: AI Boundary

AI may:

- summarize structured results
- explain plans
- generate review text
- format user-facing narratives
- help parse user input into candidate structured fields

AI must not:

- invent trigger prices
- change stop-loss lines
- decide buy/sell by itself
- override stale data
- generate execution intent without deterministic strategy output
- encourage holding after deterministic invalidation

## D15: Strategy Boundary

Strategies must be expressed through Strategy Contract.

Strategies may output:

- plans
- alerts
- watch conditions
- risk notes
- execution intent candidates for Phase 3

Strategies must not:

- directly call QMT
- directly write UI text as the only output
- directly send push notifications
- bypass freshness checks

## D16: Multi-User Boundary

Multi-user support applies to public Phase 1 and Phase 2.

Shared data:

- market data lakes
- `chan.py` adapter implementation
- strategy templates
- global scanner candidate pool

Private user data:

- trades
- positions
- watchlist
- alerts
- strategy configs
- coach events
- behavior reports
- push subscriptions
- user scanner actions

Public multi-user mode must not expose QMT features.

Development fallback to `user_id=1` is allowed temporarily, but contracts should be designed around authenticated `current_user`.

## D17: Scanner Boundary

Scanner is candidate discovery, not final structure judgment.

Scanner may use TDX daily data to find candidates quickly.

Radar is the deep structure view and uses BaoStock multi-level data.

Scanner results should carry source metadata so the UI can explain that candidate discovery and deep structure analysis may use different data sources.

## D18: Worker Write Permissions

Workers need explicit ownership.

| Worker | Allowed writes |
|---|---|
| `kline_sync_worker` | BaoStock lake only |
| TDX import/update scripts | TDX lake only |
| `scanner_worker` | `scan_results` and scanner status |
| fundamental analysis worker | LLM fields on scan results, never structure fields |
| `price_monitor` | current prices, alerts, coach events |
| future push worker | alert deliveries, push status |
| future QMT agent | execution audit only, Phase 3 private |

Market data workers must not write user trading facts except through explicitly approved monitor paths.

## D19: API Version Strategy

Existing compatibility endpoint:

```text
/api/chan/matrix/v2
```

New stable endpoint:

```text
/api/radar/{symbol}
```

Migration rule:

- Keep old endpoint until the UI moves to Radar contract.
- Do not add new product behavior directly to the old endpoint unless needed for compatibility.
- New UI work should target stable contracts.

## D20: Test Boundary

The first architecture tests should cover:

- symbol normalization
- data source routing
- Radar empty/holding field exclusivity
- stale data cannot trigger trading-action reminders
- scanner uses TDX lake
- Radar formal structure uses BaoStock lake
- `chan.py` adapter output stability
- execution intent cannot use front-adjusted prices

## Open Decisions

These are intentionally not finalized yet:

- SQLite to Postgres migration timing
- final trading calendar source
- whether structure snapshots are stored daily or only on trigger
- whether Windows QMT Agent polls cloud or receives pushed intents
- exact strategy configuration storage shape

These should be resolved before implementing Phase 2 at scale or Phase 3 live execution.
