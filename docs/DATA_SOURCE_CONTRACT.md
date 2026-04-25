# CT-OS V4.0 Data Source Contract

> Created: 2026-04-24
> Depends on: `docs/ARCHITECTURE_FOUNDATION_DECISIONS.md`

This contract defines which data source is authoritative for each product flow.

The goal is to prevent hidden mixing of data sources, adjustment modes, symbol formats, and stale data.

## Source Roles

| Source | Role | Database / Access |
|---|---|---|
| TDX local `.day` files | full-market daily scanner facts | `data/tdx_lake.db` |
| BaoStock | formal multi-level Chan structure facts | `data/baostock_lake.db` |
| Tencent quote API | UI current price, real-time preview K-line, normal price alerts | HTTP |
| QMT / XtQuant | private Phase 3 execution quotes, account, orders, fills | Windows QMT Agent |
| `ctos.db` | user facts and product state | `data/ctos.db` |

Market data lakes are shared market facts.

User facts and product state must live in `ctos.db`.

## Symbol Format

Internal canonical symbol:

```text
sh.600519
sz.000001
```

Accepted API input formats:

```text
sh600519
sh.600519
sh-600519
```

External formats:

| Target | Format | Example |
|---|---|---|
| Internal/domain/data | `{market}.{code}` | `sh.600519` |
| Tencent quote | `{market}{code}` | `sh600519` |
| TDX file | `{market}{code}.day` | `sh600519.day` |
| UI display | canonical symbol plus name | `sh.600519 贵州茅台` |

All API handlers should normalize input before calling data, structure, decision, or execution code.

## Adjustment Modes

| Flow | Source | Adjustment |
|---|---|---|
| Scanner full-market daily scan | TDX | unadjusted, `adjustflag=3` |
| Radar/Chan formal structure | BaoStock | front-adjusted, `adjustflag=2` |
| UI current price | Tencent | real current price |
| UI real-time preview K-line | Tencent | real current price aggregated or Tencent minute K |
| User trades | user input / broker statement | actual trade price |
| Positions | product state | actual cost/current price |
| QMT execution | QMT | executable quote/order context |

Rules:

- Front-adjusted structure prices must not be used as QMT order prices.
- User trade prices and position prices are real trading prices.
- Scanner candidate prices are discovery facts, not final Radar structure prices.
- If a price is shown to the user, the UI should make clear whether it is structure price, current price, or trade price.

## Feature Source Matrix

| Feature | Primary Source | Fallback | Can fallback drive formal structure? |
|---|---|---|---|
| K-line chart historical bars | BaoStock lake | Tencent K-line for display only | No |
| Chan formal structure | BaoStock lake | None for formal result | No |
| Radar | BaoStock lake | stale/error state | No |
| Scanner initial filter | TDX lake | None | N/A |
| Scanner strategy scan | TDX lake | None | N/A |
| Scanner fundamental narrative | `scan_results` + LLM | deterministic fallback summary | N/A |
| Current price display | Tencent quote | last cached price | No |
| Normal price alert | Tencent quote | skip or stale alert | No |
| Position PnL display | Tencent quote + `positions` | last cached price | No |
| QMT execution quote | QMT | no fallback | N/A |
| QMT account/position/order state | QMT | no fallback | N/A |

Formal `chan.py` structure has no Tencent fallback.

If BaoStock is missing or stale, Radar should return data freshness status and avoid pretending structure is current.

## Scanner Contract

Scanner is candidate discovery.

Authoritative source:

```text
TDX lake
freq = day
adjustflag = 3
```

Scanner may:

- use full-market TDX daily bars
- run initial filters
- run daily strategy scans
- write `scan_results`
- pass candidates to watchlist

Scanner must not:

- claim final multi-level Chan judgment
- mix BaoStock minute data into scanner result
- use Tencent real-time quote as scanner source

Scanner result should include source metadata:

```json
{
  "source": "tdx",
  "freq": "day",
  "adjustflag": "3",
  "scan_date": "2026-04-24",
  "last_bar_at": "2026-04-24"
}
```

When a scanner candidate opens Radar, Radar recomputes deep structure from BaoStock.

## Radar / Chan Contract

Radar is the deep structure view.

Authoritative source:

```text
BaoStock lake
freq in day/week/60/30/15/5
adjustflag = 2
```

Radar may:

- read multi-level BaoStock bars
- call `chan.py` through adapter
- expose structure facts
- expose decision plans
- expose data freshness

Radar must not:

- mix TDX day bars with BaoStock minute bars
- use Tencent preview K-lines for formal structure
- hide stale data behind a normal result
- use front-adjusted structure prices as execution prices

Radar response should expose:

```json
{
  "data_source": {
    "structure": "baostock",
    "adjustflag": "2",
    "levels": ["day", "60", "30", "15", "5"]
  },
  "freshness": {
    "last_bar_at": "2026-04-24 15:00:00",
    "is_stale": false,
    "stale_reason": ""
  }
}
```

## Real-Time Preview Contract

Tencent can be used for:

- UI current price
- last-bar preview
- lightweight price alerts
- position PnL display

Tencent must not be used for:

- formal `chan.py` structure
- scanner source
- QMT execution price
- execution intent limit price

Preview K-line rule:

```text
Tencent quote/minute data may render a preview last bar, but that bar is not committed to formal structure until the official lake source updates.
```

## Phase 3 QMT Source Contract

QMT data is private Phase 3 execution data.

Authoritative source:

```text
Windows QMT Agent -> QMT / XtQuant
```

QMT provides:

- executable quote context
- account state
- actual holdings
- available cash
- order submission
- cancel submission
- order status
- fill status

QMT must not be exposed to public Phase 1/2 users.

CT-OS Core must not directly call QMT.

Execution quotes and order prices must come from QMT context, not from BaoStock or Tencent.

## Freshness Contract

All data-driven product contracts should expose freshness.

Recommended shape:

```json
{
  "source": "baostock",
  "adjustflag": "2",
  "last_bar_at": "2026-04-24 15:00:00",
  "checked_at": "2026-04-24 20:35:00",
  "is_stale": false,
  "stale_reason": ""
}
```

Suggested stale reasons:

| Reason | Meaning |
|---|---|
| `SOURCE_UNAVAILABLE` | source cannot be reached |
| `NO_DATA` | no bars for symbol/frequency |
| `OUTDATED` | latest bar is older than expected |
| `LEVEL_INCOMPLETE` | required level is missing |
| `SYNC_NOT_COMPLETED` | expected sync job has not finished |
| `QUOTE_STALE` | real-time quote is too old |
| `QMT_OFFLINE` | Windows QMT Agent or QMT client is unavailable |

Rules:

- Stale formal structure can be displayed with warning.
- Stale formal structure must not trigger trading-action reminders.
- Stale quote data must not trigger price-action reminders.
- Stale QMT data must block execution.

## Data Ownership

| Database | Owns | Must not contain |
|---|---|---|
| `data/ctos.db` | users, trades, positions, alerts, scan results, strategy configs, coach events | large market history |
| `data/tdx_lake.db` | full-market TDX daily bars | user facts, AI output, alerts |
| `data/baostock_lake.db` | BaoStock K-line cache | user facts, AI output, alerts |

Market data can be shared across users.

User facts must be scoped by `user_id` in Phase 1/2 public product.

## Fallback Rules

Allowed:

- Tencent quote fallback for UI current price.
- Tencent K-line fallback for display-only preview.
- Deterministic LLM fallback summary for scanner fundamental analysis.
- Cached last price for UI display when clearly marked stale.

Not allowed:

- Tencent fallback for formal Chan structure.
- TDX fallback for Radar minute levels.
- BaoStock front-adjusted price as execution price.
- AI fallback for deterministic structure fields.
- QMT fallback to Tencent for execution order price.

## Required Tests

The first data source tests should prove:

- `day + adjustflag=3` reads TDX lake by default.
- BaoStock writes do not pollute TDX lake.
- Radar formal structure path reads BaoStock source.
- Scanner reads TDX source.
- Tencent real-time preview is not passed into formal `chan.py` analysis.
- Stale data blocks trading-action reminders.
- Execution intent rejects front-adjusted structure prices.

## Open Decisions

- Final trading calendar source.
- Whether Radar stores daily structure snapshots.
- Whether cloud deployment keeps SQLite or moves to Postgres before public launch.
- Whether Windows QMT Agent polls cloud or receives pushed intents.
