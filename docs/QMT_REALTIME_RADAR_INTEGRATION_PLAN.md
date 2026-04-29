# QMT Realtime Minute Data Integration Plan

> Created: 2026-04-28
> Scope: Radar realtime minute data, read-only market data integration
> Depends on: `docs/DATA_SOURCE_CONTRACT.md`, `docs/RADAR_API_CONTRACT.md`, `docs/QMT_EXECUTION_ARCHITECTURE.md`

This document defines how CT-OS should connect QMT realtime minute data into Radar without turning CT-OS into an execution robot.

CT-OS remains a trading coach. This integration is read-only. It can improve intraday structure freshness, position coaching, and near-boundary alerts, but it must not place orders.

涉及市场判断的内容均仅供参考，不构成投资建议。

## 1. Decision

Use a separate Windows-side `qmt_bridge` process to read QMT / XtQuant data and expose normalized, read-only market data to CT-OS Core.

CT-OS Core must not import `xtquant`.

```text
Windows QMT Client
  -> xtquant / xtdata
  -> qmt_bridge read-only process
  -> local HTTP / WebSocket API
  -> CT-OS FastAPI
  -> qmt_lake.db + realtime cache
  -> chan_adapter / radar / kline UI
```

The QMT bridge is a market-data adapter, not an execution adapter.

## 2. Why This Is Needed

Current Radar is mostly a post-close review tool:

```text
BaoStock lake
  -> official day / 30 / 5 bars
  -> chan.py
  -> Radar deduction
```

During the trading day, we have only Tencent current price for display and position PnL. That is not enough for formal 5-minute or 1-minute Chan structure.

QMT can provide intraday minute bars from the user's Windows client. That lets Radar move from:

```text
盘后结构复盘
```

to:

```text
收线结构 + 正在形成的分钟线推演
```

The distinction matters. A forming 5-minute bar can warn that a boundary is being approached. A closed 5-minute bar can confirm a structure event.

## 3. Source Authority

Update the data contract conceptually as follows:

| Flow | Official Source | Realtime Source | Formal Structure Allowed |
|---|---|---|---|
| Post-close Radar | BaoStock lake | None | Yes |
| Intraday Radar preview | BaoStock lake + QMT forming bars | QMT | Closed QMT bars only |
| Current price / PnL | Tencent or QMT quote | QMT preferred | No |
| Kline display | BaoStock + QMT overlay | QMT | Display only if forming |
| Execution quote | QMT execution agent | QMT | Not part of Radar |

Rules:

- `CLOSED` QMT minute bars may feed formal intraday `chan.py` structure.
- `FORMING` QMT minute bars must never confirm a BSP, ZhongShu break, or Radar A/B/C path.
- `FORMING` bars may update distance-to-boundary, warning labels, and "if this closes here" preview text.
- BaoStock remains the official post-close fallback.
- If QMT is unavailable, Radar must degrade to the current BaoStock + Tencent behavior.

TDX local 1-minute data is allowed as a display and replay supplement, not as the
first realtime confirmation source:

| Source | Best Use | Must Not Do |
|---|---|---|
| QMT `1m` | live intraday trigger preview and latest closed 1-minute bar | long-history dependency |
| TDX local `1m` | K-line display, local replay, filling earlier intraday 1-minute history | override QMT realtime confirmation |

Priority:

```text
1-minute Kline display:
  QMT realtime 1m
    -> TDX local 1m
    -> unavailable

Radar 1-minute trigger:
  QMT closed 1m
    -> TDX local 1m only in replay/historical slice mode
    -> no 1-minute trigger
```

UI labels must show this difference:

```text
1分 · QMT实时
1分 · TDX本地历史
1分不可用
```

Implemented TDX 1-minute local spike:

```text
server/services/tdx_minute_service.py
  -> reads vipdoc/{sh,sz}/minline/*.lc1
  -> parses closed 1-minute rows
  -> marks source = tdx_local_1m
  -> marks usage = display/replay only at API boundary
```

TDX local endpoints:

```http
GET /api/data/tdx/minute/health?symbol=sh.688008
GET /api/data/tdx/minute/sh.688008?count=240
```

The endpoint deliberately returns:

```json
{
  "source": "tdx_local_1m",
  "usage": "display_replay_only"
}
```

This makes it hard for Radar to accidentally treat TDX 1-minute as the live
confirmation source later.

## 4. Bridge Boundary

The bridge runs on Windows because QMT / miniQMT is a Windows desktop client.

```text
qmt_bridge
  owns: xtquant imports, QMT session, subscriptions, local normalization
  does not own: Chan structure, Radar decisions, position advice, execution
```

Minimum bridge API:

```http
GET  /health
GET  /symbols
POST /subscribe
GET  /quotes?symbols=sh.688008,sh.603893
GET  /klines?symbol=sh.688008&period=5m&limit=240
GET  /klines/latest?symbol=sh.688008&period=5m
WS   /stream
```

Current Windows SSE gateway compatibility:

```text
Windows QMT gateway:
  health: http://192.168.100.157:8765/health
  stream: http://192.168.100.157:8765/stream?codes=000001.SZ&period=tick

CT-OS diagnostics:
  GET /api/data/qmt/health
  GET /api/data/qmt/stream-probe/{symbol}?period=tick
```

The current SSE gateway returns a health shape like:

```json
{
  "ok": true,
  "host": "MARK",
  "qmt": "localhost:58600",
  "subscriptions": {},
  "last_count": 0
}
```

CT-OS normalizes that to:

```json
{
  "available": true,
  "provider": "qmt_sse_gateway"
}
```

Known current status: Mac -> Windows gateway health is reachable. Real `xtdata.subscribe_quote`
still depends on the Windows QMT行情服务 being connected. If `/stream-probe` returns
`无法连接行情服务`, the blocker is on the Windows QMT side, not the Mac network or CT-OS.

Normalized kline row:

```json
{
  "symbol": "sh.688008",
  "freq": "5",
  "date": "2026-04-28 10:35:00",
  "open": 176.2,
  "high": 178.1,
  "low": 175.9,
  "close": 177.4,
  "volume": 123456,
  "amount": 45678901.23,
  "adjustflag": "3",
  "bar_status": "CLOSED",
  "source": "qmt",
  "received_at": "2026-04-28T10:35:03+08:00"
}
```

Normalized quote row:

```json
{
  "symbol": "sh.688008",
  "price": 177.4,
  "bid1": 177.39,
  "ask1": 177.4,
  "volume": 123456,
  "amount": 45678901.23,
  "quote_time": "2026-04-28T10:35:03+08:00",
  "source": "qmt"
}
```

## 5. CT-OS Core Architecture

Recommended minimal implementation:

```text
server/services/qmt_bridge_client.py
  -> talks to Windows qmt_bridge
  -> normalizes errors and timeout

server/db/kline_lake.py
  -> add source = "qmt"
  -> data/qmt_lake.db

server/services/realtime_kline_service.py
  -> chooses QMT when available
  -> separates CLOSED from FORMING
  -> writes CLOSED bars
  -> keeps FORMING bar in memory cache only

server/engines/structure/chan_adapter.py
  -> optional source="qmt" for intraday levels
  -> refuses FORMING bars for formal structure

server/api/radar.py
  -> query param data_mode=official|realtime_preview
  -> response data_source clearly labels source and bar status
```

Data flow:

```text
Radar request
  |
  +-- official mode
  |     -> BaoStock lake
  |     -> chan.py
  |     -> Radar
  |
  +-- realtime_preview mode
        -> QMT bridge health
        -> QMT closed 5m/1m bars
        -> qmt_lake.db
        -> chan.py formal intraday structure
        -> QMT forming bar overlay
        -> Radar with realtime labels
```

## 6. Radar Behavior

Radar needs three data freshness labels:

| Label | Meaning | Can Confirm Structure |
|---|---|---:|
| `official_close` | BaoStock official closed bars | Yes |
| `qmt_closed` | QMT closed minute bars | Yes, intraday mode only |
| `qmt_forming` | current bar still forming | No |

Recommended UI copy:

```text
正式结构：QMT 5分钟已收线，截至 10:35
实时预览：当前 5分钟K 形成中，仅用于边界距离，不确认买卖点
```

Radar A/B/C rules:

- A/B/C current state can be computed from `official_close` or `qmt_closed`.
- "接下来如果发生" can include `qmt_forming` preview.
- A path must not switch to `A confirmed` or `C invalidated` until the relevant trigger-level bar is closed.
- If price pierces a boundary intrabar but closes back, Radar should record this as "盘中触碰，未收线确认".

## 7. Symbol and Period Mapping

Internal CT-OS symbol:

```text
sh.688008
sz.300124
```

QMT / XtQuant common symbol:

```text
688008.SH
300124.SZ
```

The bridge should own QMT format conversion. CT-OS should keep using canonical symbols.

Period mapping:

| CT-OS | QMT / XtData |
|---|---|
| `1` | `1m` |
| `5` | `5m` |
| `15` | `15m` |
| `30` | `30m` |
| `60` | `1h` |
| `day` | `1d` |

## 8. Failure Modes

| Failure | Expected Behavior |
|---|---|
| QMT client closed | Radar falls back to BaoStock official structure and Tencent quote |
| Bridge timeout | Show `qmt_unavailable`, do not block official Radar |
| QMT bar timestamp jumps backward | Reject bar, log data anomaly |
| QMT returns duplicate bar | Upsert by `(symbol, freq, date)` |
| FORMING bar crosses boundary | Show preview warning only |
| CLOSED bar crosses boundary | Allow Radar path transition |
| Lunch break no new bars | Keep last closed bar, label market session paused |
| Adjustment mismatch | QMT bars use real prices. Do not mix with BaoStock front-adjusted prices in one formal structure chain |

The adjustment mismatch is the main architectural landmine.

For intraday Radar, the safest rule is:

```text
Use QMT real-price bars for trigger-level realtime deduction.
Use BaoStock front-adjusted bars for historical/day official deduction.
Display source and adjustment on every boundary.
Do not compare QMT real price directly against BaoStock front-adjusted historical boundary unless the boundary is explicitly marked as structure price.
```

## 9. Implementation Phases

### P0: Read-Only Bridge Spike

Goal: prove QMT can return latest quote and closed 5-minute bars.

Tasks:

- Build standalone `qmt_bridge` on Windows.
- Implement `/health`, `/quotes`, `/klines`.
- Normalize symbols and periods.
- Confirm with 3 symbols: 澜起科技、瑞芯微、兆易创新.
- No CT-OS backend code depends on it yet.

Implemented local skeleton:

```text
qmt_bridge/
  app.py       # standalone FastAPI service
  provider.py  # fake provider + lazy xtdata provider
  symbols.py   # CT-OS <-> QMT symbol and period mapping
```

Local fake run:

```bash
QMT_BRIDGE_PROVIDER=fake python -m qmt_bridge.app
```

Windows QMT run:

```bash
QMT_BRIDGE_PROVIDER=xtdata python -m qmt_bridge.app
```

Smoke checks:

```bash
curl http://127.0.0.1:8765/health
curl "http://127.0.0.1:8765/quotes?symbols=sh.688008,sh.603893,sh.603986"
curl "http://127.0.0.1:8765/klines?symbol=sh.688008&period=5m&limit=20"
```

### P1: CT-OS QMT Client and Data Lake

Goal: CT-OS can pull QMT closed bars and cache them.

Tasks:

- Add `qmt_bridge_client`.
- Add `qmt_lake.db` support.
- Add source-aware query/write tests.
- Add stale/timeout fallback.
- Add `/api/data/qmt/health`.

### P2: Radar Realtime Preview Mode

Goal: Radar can run with `data_mode=realtime_preview`.

Tasks:

- Add Radar request option.
- Use QMT closed bars for trigger-level intraday structure.
- Attach forming bar as preview only.
- Add response labels: `data_mode`, `bar_status`, `source`, `last_closed_bar_at`.
- Frontend shows visible data-source badge.

### P3: Watchlist Subscription and Push

Goal: 自选股/持仓股实时更新 without manual refresh.

Tasks:

- Bridge subscribes to watchlist symbols.
- CT-OS polls or receives stream updates.
- Debounce Radar recomputation to closed-bar events.
- Frontend updates boundary distance and source labels.

## 10. Test Plan

Unit tests:

- Symbol mapping: `sh.688008 <-> 688008.SH`.
- Period mapping: `5 <-> 5m`.
- Bridge payload normalization.
- Timeout fallback returns official mode.
- `FORMING` bars are excluded from formal structure.
- `CLOSED` bars may be upserted to `qmt_lake.db`.

Integration tests:

- Radar official mode unchanged when QMT disabled.
- Radar realtime mode labels QMT source.
- Radar does not confirm A/C from a forming bar.
- Position coach uses QMT quote for PnL when available.
- Stale QMT bridge does not block BaoStock structure.

Manual QA:

- Open Radar for 澜起科技 during market hours.
- Confirm the UI shows latest QMT quote time.
- Confirm current 5-minute bar says forming before it closes.
- Confirm after bar close, Radar can recompute using the closed bar.
- Disconnect bridge and confirm fallback message is clear.

## 11. Out of Scope

- QMT order submission.
- Intraday T auto execution.
- Risk Gate.
- Broker account reconciliation.
- Public deployment support for QMT.
- Full tick-level Chan structure.
- Using TDX local 1-minute data as the primary realtime confirmation source.

Those belong to the future execution architecture, not this read-only realtime Radar work.

## 12. Source Notes

QMT / XtQuant documentation indicates `xtdata` supports quote subscription and market data reads through APIs such as `subscribe_quote` and `get_market_data_ex`, with minute periods including `1m`, `5m`, `15m`, `30m`, `1h`, and `1d`.

Reference links:

- https://qmt.hxquant.com/?id=51
- https://qmt.hxquant.com/?id=13
- https://zsrl.github.io/xtquant-doc/xtquant/xtdata.html
