# CT-OS V4.0 Data Source Contract

> Created: 2026-04-24
> Depends on: `docs/ARCHITECTURE_FOUNDATION_DECISIONS.md`

This contract defines which data source is authoritative for each product flow.

The goal is to prevent hidden mixing of data sources, adjustment modes, symbol formats, and stale data.

## Source Roles

| Source | Role | Database / Access |
|---|---|---|
| TDX local `.day` files | full-market daily scanner facts | `data/tdx_lake.db` |
| TDX local 1-minute files | local 1-minute display and replay supplement | future `tdx_minute_lake.db` or source-specific lake |
| BaoStock | formal multi-level Chan structure facts | `data/baostock_lake.db` |
| Tencent quote API | UI current price, real-time preview K-line, normal price alerts | HTTP |
| QMT / XtQuant | read-only realtime market data now; private Phase 3 execution data later | Windows QMT Gateway / Agent |
| `ctos.db` | user facts and product state | `data/ctos.db` |

Market data lakes are shared market facts.

User facts and product state must live in `ctos.db`.

## Local Lake Layout

The active K-line cache is physically split by source. Do not reintroduce a single
mixed `kline_lake.db` as an active source.

| Lake | Active | Contents | Reader contract |
|---|---:|---|---|
| `data/tdx_lake.db` | Yes | Full-market daily bars, `freq=day`, `adjustflag=3` | Scanner, broad market filters, AI Native candidate discovery |
| `data/baostock_lake.db` | Yes | BaoStock `week/day/60/30/15/5`, usually `adjustflag=2` | Chan, Radar, AI Native structure reasoning |
| `data/qmt_lake.db` | Yes | QMT closed realtime bars, unadjusted | Intraday preview/private workstation context |
| `data/kline_lake.db` | No | Legacy mixed lake from the old architecture | Cleanup candidate after split verification |
| `data/corrupt-backups/*` | No | Malformed or quarantined historical DB files | Delete/archive only, never read in production |

Operational status endpoint:

```text
GET /api/data/lake/status
```

The endpoint is read-only. It reports each active lake path, disk size, row count,
symbol count, date range, frequency distribution, legacy lake presence, and any
files still left in `data/corrupt-backups`.

Cleanup rule:

- Delete `data/corrupt-backups/*` after confirming it is not in the active code path.
- Keep `data/kline_lake.db` only during the migration window; remove it once frontend K-line display, scanner, Radar, and AI Native checks all pass against split lakes.
- Never write new data into `data/kline_lake.db`.

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
| QMT read-only preview | QMT | realtime quote/tick/minute context, no orders |
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
| K-line chart 1-minute bars | QMT realtime 1m | TDX local 1m for display/replay | No for forming/replay-only bars |
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

## Watchlist Data Lifecycle

Adding a symbol to watchlist is also a market-data subscription intent.

Write path:

```text
POST /api/watchlist/groups/{group_name}/stocks
  -> write watchlist_items
  -> queue BaoStock background backfill for that symbol
  -> return immediately with data_sync.status = queued
```

Backfill order:

```text
1. quick day
2. quick 5m
3. full day / 60 / 30 / 15 / 5
```

The quick stage exists only to make the frontend responsive. The full stage writes
`kline_sync_meta`, which puts the symbol into the long-term incremental sync set.

Worker tracking set:

```text
kline_sync_meta symbols
+ open positions
+ watchlist_items
```

This means a watchlist symbol stays fresh even if the user has no position yet.
After 17:30 the worker refreshes daily bars; after 20:30 it refreshes all BaoStock
levels. BaoStock failures must surface as freshness/error state, not as a fake
successful formal structure.

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

## QMT Source Contract

QMT has two separate roles:

| Role | Phase | Allowed |
|---|---|---|
| Read-only market data | Phase 1/2 private workstation setup | health, stream probe, quotes, closed minute bars, display preview |
| Execution data and orders | future private Phase 3 | account, positions, order submit/cancel/status, fills |

The read-only market data role does not grant execution permission.

Authoritative source:

```text
Windows QMT Gateway / Agent -> QMT / XtQuant
```

QMT read-only market data may provide:

- realtime quote/tick context
- closed intraday minute bars
- forming bar preview labels
- bridge health and subscriptions

Future QMT execution provides:

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
- TDX local 1-minute fallback for Kline display and historical replay only.
- Deterministic LLM fallback summary for scanner fundamental analysis.
- Cached last price for UI display when clearly marked stale.

Not allowed:

- Tencent fallback for formal Chan structure.
- TDX fallback for Radar minute levels.
- TDX local 1-minute fallback for live Radar confirmation when QMT is unavailable.
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
