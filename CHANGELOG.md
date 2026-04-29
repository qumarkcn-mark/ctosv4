# Changelog

## Unreleased

### Radar

- Added the AI Native Radar shadow loop behind `AI_NATIVE_RADAR_ENABLED=false`, with structure transcripts, hypothesis generation, verifier gates, isolated run storage, and fallback behavior that leaves the old Radar path untouched.
- Rebuilt the active Radar surface around trend deduction, A/B/C complete classification, structural templates, key boundaries, and position-aware coaching.
- Added position linkage to Radar, including holding state, PnL context, nearest risk line, stop/trailing structure boundaries, and empty/holding mode separation.
- Added realtime data source planning and contracts for QMT read-only bridge, TDX local 1-minute display, and future `data_mode=realtime_preview`.
- Added Kline `1分` display mode. It prefers QMT when available, falls back to TDX local 1-minute files, and is explicitly labeled display/replay only.

### Data Sources

- Added `qmt_bridge` read-only FastAPI skeleton with fake and lazy `xtdata` providers.
- Added CT-OS QMT bridge client, `/api/data/qmt/health`, `/api/data/qmt/klines/{symbol}`, and `/api/data/qmt/stream-probe/{symbol}`.
- Added `qmt_lake.db` support in `server/db/kline_lake.py` for closed QMT minute bars.
- Added TDX local `.lc1` 1-minute parser and `/api/data/tdx/minute/*` endpoints.
- Mounted and validated local TDX 1-minute files through `TDX_VIPDOC`, with UI fallback labels.
- Normalized persisted symbol aliases across positions, trades, watchlist, Chan, price, and Radar flows.

### Tests

- Added AI Native Radar transcript, verifier, memory, API, isolation, migration, and old Radar regression coverage.
- Added QMT bridge contract tests.
- Added TDX local 1-minute parser and API tests.
- Added/updated symbol, position, trade, Radar, K-line lake, and historical Radar regression coverage.

### Architecture

- Radar frontend active path now uses `/api/radar/{symbol}` instead of `/api/chan/matrix/v2/{symbol}`.
- `/api/chan/matrix/v2/{symbol}` is frozen as a compatibility endpoint. New product behavior should be added to Radar, scanner, rotation, or dedicated engine contracts.
- Scanner, RotationCompass, BehaviorReport, Push/Alerts, and Coach/Event Log now use dedicated contracts instead of consuming matrix fields.
- Future QMT execution is reserved behind Execution Intent, Risk Gate, Windows QMT Agent, QMT Adapter, and Execution Audit Log. Phase 1/2 remain trading-coach only and do not execute orders.
