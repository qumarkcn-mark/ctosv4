# CT-OS V4.0 Symbol, Freshness, And Worker Contract

> Created: 2026-04-25
> Depends on: `docs/ARCHITECTURE_FOUNDATION_DECISIONS.md`, `docs/DATA_SOURCE_CONTRACT.md`

This contract turns the foundation decisions for symbol normalization, data freshness, and worker write permissions into implementation rules.

## Symbol Normalize Contract

Canonical internal symbol format:

```text
sh.600519
sz.000001
```

Supported inputs:

```text
sh600519
sh.600519
sh-600519
SH600519
600519
000001
```

Plain six-digit codes are accepted at import and internal helper boundaries:

- `6`, `5`, `9` prefixes infer `sh`
- all other six-digit A-share codes infer `sz`

The canonical implementation is:

```text
server/domain/symbols.py
```

Public helpers:

| Helper | Output | Example |
|---|---|---|
| `normalize_symbol()` | canonical internal symbol | `sh.600519` |
| `parse_symbol()` | structured `Symbol(market, code)` | `market=sh`, `code=600519` |
| `to_tencent_symbol()` | Tencent quote format | `sh600519` |
| `to_tdx_filename()` | TDX daily filename | `sh600519.day` |

Rules:

- API handlers must normalize symbols before calling data, structure, decision, or execution code.
- Data repositories should accept canonical symbols unless the repository explicitly wraps an external source.
- External adapters convert at the boundary only.
- New code must not hand-roll `symbol.replace(".", "")` or `f"{symbol[:2]}.{symbol[2:]}"`.
- Unsupported markets such as HK/US must be rejected until product contracts define them.

## Price And Adjustment Coordination

Scanner discovery:

```text
TDX daily lake, unadjusted, adjustflag=3
```

Radar/Chan formal structure:

```text
BaoStock multi-level lake, front-adjusted, adjustflag=2
```

Trading facts:

```text
actual trade price / actual current price / actual cost
```

Rules:

- Structure prices explain structure; they do not become order prices.
- `trades.price`, `positions.avg_cost`, `positions.current_price`, stop prices, trailing stop prices, and execution prices use real trading price units.
- Scanner candidates must be recomputed through Radar before deep structure display.
- Any UI that mixes current price and structure price must label the source.

## Freshness Contract

All data-driven contracts should expose the same minimum fields:

```json
{
  "source": "baostock",
  "adjustflag": "2",
  "last_bar_at": "2026-04-25 15:00:00",
  "checked_at": "2026-04-25T20:35:00+08:00",
  "is_stale": false,
  "stale_reason": "",
  "levels": {
    "day": {"last_bar_at": "2026-04-25", "is_stale": false},
    "30": {"last_bar_at": "2026-04-25 15:00:00", "is_stale": false}
  }
}
```

Allowed `stale_reason` values:

| Reason | Meaning |
|---|---|
| `SOURCE_UNAVAILABLE` | source cannot be reached |
| `NO_DATA` | no bars or quote for symbol/frequency |
| `OUTDATED` | latest data is older than expected |
| `LEVEL_INCOMPLETE` | required structure level is missing |
| `SYNC_NOT_COMPLETED` | expected sync job has not finished |
| `QUOTE_STALE` | real-time quote is too old |
| `ENGINE_ERROR` | structure engine failed |
| `QMT_OFFLINE` | private QMT agent/client unavailable |

Rules:

- Stale formal structure can be displayed with warning.
- Stale formal structure must not trigger trading-action alerts.
- Stale quote data must not trigger price-action reminders.
- Stale QMT quote/account state must block execution approval.
- Missing required Radar levels should set `LEVEL_INCOMPLETE`.
- Engine failures should return a stable error envelope with `ENGINE_ERROR`.
- Stale data may create coach event `DATA_STALE_BLOCKED`.

## Worker Write Permissions

Workers need explicit write ownership.

| Worker / Script | Allowed writes | Forbidden writes |
|---|---|---|
| BaoStock sync worker | `baostock_lake.db`, sync metadata | user trades, positions, alerts, coach events |
| TDX import/update scripts | `tdx_lake.db`, sync metadata | user trades, positions, alerts, coach events |
| scanner worker | `scan_results`, scanner job status | positions, alerts, formal Radar structure |
| fundamental analysis worker | LLM fields on scan results | deterministic structure fields, strategy condition status |
| price monitor | current price cache, alert state, coach events for triggered reminders | trades, position quantities, structure fields |
| push worker | `alert_deliveries`, push status | strategy condition status, trades, positions |
| future QMT agent | execution audit fields only, Phase 3 private | public Phase 1/2 alerts, user-authored trade records |

Rules:

- Market data workers must not write user trading facts.
- LLM workers must not write deterministic structure or strategy status fields.
- Push workers deliver already-created alerts; they do not decide strategy truth.
- Any worker that writes user-scoped state must include `user_id`.
- Any worker that emits action-related reminders must preserve the "仅供参考" disclaimer.

## Required Tests

Initial tests:

- `normalize_symbol()` accepts `sh600519`, `sh.600519`, `sh-600519`, uppercase variants, and six-digit A-share codes.
- `normalize_symbol()` rejects unsupported or malformed symbols.
- helpers convert canonical symbols to Tencent and TDX formats.
- Radar contract keeps stale/error envelope stable.
- stale data blocks trading-action alerts.
- scanner reads TDX source and Radar reads BaoStock source.
- worker write paths do not cross ownership boundaries.

## Migration Notes

Existing modules still contain local symbol helpers. Future refactors should replace those call sites gradually with `server.domain.symbols`.

Do not migrate every call site in one change. Replace them when touching the surrounding API or repository so tests can stay focused.
