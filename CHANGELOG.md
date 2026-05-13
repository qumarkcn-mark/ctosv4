# Changelog

## Unreleased

### AI Native V5

- Added the CZSC-only AI Structure pipeline: universe resolution, snapshot jobs, user-scoped structure context, scenario branches, chat answers, chart evidence, reminders, and outcome memory.
- Removed legacy `chan.py` / old radar runtime paths from the default app. V5 does not keep fallback, shadow, or comparison entry points.
- Removed old scanner, rotation, playbook, sand-table, multiverse, old AI Native fusion/agent/observation, and stop/reduce training code paths.
- Removed legacy frontend radar/Kline/chan/scanner/AI-training surfaces from the default bundle.
- Updated docs, config, database schema, and tests around the V5 CZSC-only product direction.

### Verification

- `npm run build`
- `venv/bin/python -m pytest tests -q`
