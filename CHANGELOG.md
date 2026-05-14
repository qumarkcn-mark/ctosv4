# Changelog

## Unreleased

### AI Native V5

- Added the CZSC-only AI Structure pipeline: universe resolution, snapshot jobs, user-scoped structure context, scenario branches, chat answers, chart evidence, reminders, and outcome memory.
- Wired the real data loop from watchlist/positions K-line sync into CZSC snapshot jobs and user-scoped AI structure context jobs.
- Added multi-user follow-up context dispatch when a shared symbol snapshot becomes fresh.
- Added startup recovery for CZSC snapshot jobs that previously failed because the CZSC dependency was unavailable.
- Added the AI Structure reminder loop: user-scoped reminder listing, price-monitor triggering, coach-event logging, and right-side reminder status chips.
- Added mistake-only AI Structure memory: only ignored invalidations enter daily chat memory, capped to a tiny warning context.
- Added automatic scenario outcome settlement when AI Structure reminders trigger, without marking user behavior as a mistake until follow-up confirms it.
- Removed legacy `chan.py` / old radar runtime paths from the default app. V5 does not keep fallback, shadow, or comparison entry points.
- Removed old scanner, rotation, playbook, sand-table, multiverse, old AI Native fusion/agent/observation, and stop/reduce training code paths.
- Removed legacy frontend radar/Kline/chan/scanner/AI-training surfaces from the default bundle.
- Updated docs, config, database schema, and tests around the V5 CZSC-only product direction.

### Verification

- `npm run build`
- `.venv312/bin/python -m pytest tests -q`
- `PYTHONPATH=. venv/bin/python -m pytest tests/test_ai_structure_*.py tests/test_czsc_adapter_contract.py -q`
- `npm run build`
- Isolated V5 data-loop smoke: `/api/data/sync-klines/{symbol}` -> CZSC snapshots -> AI structure contexts -> chat -> chart evidence.
