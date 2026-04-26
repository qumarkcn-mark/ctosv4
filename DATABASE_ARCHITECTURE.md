# CT-OS V4.0 Database Architecture

CT-OS uses three SQLite databases with clear ownership boundaries. The product database stores user and coach state. The two market-data lakes store large K-line caches by source.

```text
data/ctos.db
  product state: users, trades, positions, alerts, scan_results

data/tdx_lake.db
  TDX daily facts: day + adjustflag=3

data/baostock_lake.db
  BaoStock cache: day/week/60/30/15/5, usually adjustflag=2
```

## Product Database

Path: `data/ctos.db`

Owner: FastAPI business logic.

Rule: keep user facts and product state here. Do not store large K-line history in this database.

| Table | Purpose | Main Writers | Main Readers |
|---|---|---|---|
| `users` | WeChat/dev users and settings | auth, bootstrap | all user-scoped APIs |
| `trades` | Immutable trade records | trade form, CSV/import flows | positions, behavior analysis |
| `positions` | Current holdings aggregated from trades | trades API, price monitor | dashboard, chan view, alerts |
| `alerts` | Stop-loss and signal reminders | price monitor, chan monitor | alert UI, push flow |
| `behavior_stats` | Cached behavior metrics | behavior engine | analysis page |
| `watchlist_groups` | User-defined watchlist groups | watchlist API, scanner observe action | scanner, rotation, monitor |
| `watchlist_items` | Watchlist symbols in groups | watchlist API, scanner observe action | scanner, rotation, monitor |
| `scan_results` | Daily scanner candidates and research | scanner worker, fundamental service | scanner page |
| `radar_deductions` | AI radar deduction snapshots | radar/agent flows | radar history |
| `multiverse_snapshots` | Daily multi-level classification snapshots | multiverse worker | simulation/review views |
| `portfolio_strategies` | Portfolio-level strategy records | agent/strategy flows | strategy history |

## TDX Lake

Path: `data/tdx_lake.db`

Owner: TDX import/update scripts.

Rule: this is the scanner's daily fact source. It should only contain unadjusted daily bars.

Expected rows:

```sql
freq = 'day'
adjustflag = '3'
```

Schema:

| Table | Purpose |
|---|---|
| `klines` | Full-market daily OHLCV imported from local TDX `.day` files |
| `tdx_sync_meta` | Last TDX daily sync date per symbol |

Current compatibility note: `kline_sync_meta` exists because both lake files share the same base schema, but TDX code should use `tdx_sync_meta`.

Main flows:

```text
/Volumes/tdx_vipdoc
  -> scripts/import_tdx_daily.py
  -> scripts/update_tdx_daily.py
  -> data/tdx_lake.db
  -> screener_filter.batch_screen()
  -> scanner worker
  -> ctos.db.scan_results
```

## BaoStock Lake

Path: `data/baostock_lake.db`

Owner: BaoStock service and K-line sync worker.

Rule: this is a cache, not a source of user truth. If it is stale or missing, the app can refill it from BaoStock.

Expected rows:

```sql
freq IN ('week', 'day', '60', '30', '15', '5')
adjustflag = '2'  -- default for BaoStock reads/writes
```

Schema:

| Table | Purpose |
|---|---|
| `klines` | Cached BaoStock OHLCV for charting, chan analysis, price fallback |
| `kline_sync_meta` | Last BaoStock sync date per symbol/frequency |

Main flows:

```text
BaoStock
  -> server.services.baostock_service
  -> data/baostock_lake.db
  -> chan_detail_service / price_service / sand_table / lake_meta
```

## Data Access Rules

All code should go through `server/db/kline_lake.py`.

Default routing:

| Call | Default Source |
|---|---|
| `query_klines(..., freq='day', adjustflag='3')` | TDX lake |
| `query_klines(...)` for all other cases | BaoStock lake |
| `upsert_klines(...)` | BaoStock lake |
| `get_lake_connection('tdx')` | TDX lake |
| `get_lake_connection('baostock')` | BaoStock lake |

Callers that know their source should pass it explicitly. Scanner code should pass or obtain `tdx`. Charting and price code should pass or obtain `baostock`.

## Migration Rules

Never delete the old lake during a split or recovery. Keep it as a rollback source.

One-time split:

```bash
venv/bin/python scripts/migrate_split_lakes.py
```

This copies:

```text
old kline_lake.db day + adjustflag=3 -> tdx_lake.db
old kline_lake.db everything else    -> baostock_lake.db
```

TDX rebuild:

```bash
venv/bin/python scripts/import_tdx_daily.py
```

TDX daily update:

```bash
venv/bin/python scripts/update_tdx_daily.py
```

BaoStock refill happens through `baostock_service` and `kline_sync_worker`.

## Known Schema Risk

`alerts.alert_type` has historically drifted between the live database and `server/db/database.py`. SQLite cannot alter a `CHECK` constraint directly, so expanding alert types requires rebuilding the `alerts` table inside a transaction after copying existing rows.
