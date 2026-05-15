# Changelog

## Unreleased

### AI Native V5

- Added the CZSC-only AI Structure pipeline: universe resolution, snapshot jobs, user-scoped structure context, scenario branches, chat answers, chart evidence, reminders, and outcome memory.
- Wired the real data loop from watchlist/positions K-line sync into CZSC snapshot jobs and user-scoped AI structure context jobs.
- Added multi-user follow-up context dispatch when a shared symbol snapshot becomes fresh.
- Added startup recovery for CZSC snapshot jobs that previously failed because the CZSC dependency was unavailable.
- Added AI Structure universe priority scheduling so held symbols, recently chatted symbols, and watchlist symbols are warmed in that order.
- Polished AI Structure workspace status copy for stale, no-data, failed, CZSC-unavailable, and pending states.
- Added the AI Structure reminder loop: user-scoped reminder listing, price-monitor triggering, coach-event logging, and right-side reminder status chips.
- Added mistake-only AI Structure memory: only ignored invalidations enter daily chat memory, capped to a tiny warning context.
- Added automatic scenario outcome settlement when AI Structure reminders trigger, without marking user behavior as a mistake until follow-up confirms it.
- Added triggered reminder follow-up: users can mark reminders as handled, continue watching, or ignored; ignored invalidations now feed mistake memory.
- Added scheduled AI Structure outcome settlement worker for due `same_day` / `next_day` / `3d` / `5d` branch reviews.
- Added AI Structure outcome review API so Web and miniprogram clients can render user-scoped branch review timelines with mistake memory.
- Added the AI Structure outcome timeline to the right-side coach panel, including recent branch reviews and mistake-memory warnings.
- Added review-aware AI Structure chat answers for questions like “我上次错在哪里？”, grounded in outcome timeline and mistake memory.
- Added right-side AI Structure chat session restore so follow-up questions continue the latest user-scoped coaching conversation.
- Added session-aware AI Structure follow-up understanding for ellipsis questions like “那跌破呢？” and “那帮我盯一下”.
- Added guarded AI Structure chat degradation for stale contexts and out-of-scope questions such as target price, stock recommendations, or fundamental buy/sell conclusions.
- Added no-context AI Structure chat degradation so first-time questions return a coachable data-status answer instead of a raw 404.
- Added a shared AI Structure workspace bootstrap API for Web and miniprogram clients to load universe, context status, branches, reminders, and outcome memory in one CZSC-only read model.
- Wired the Web AI Structure workspace startup path to the shared bootstrap API, reusing the read model for status, reminders, outcome memory, and AI pool health.
- Added client profiles and include filters to the AI Structure workspace bootstrap contract so Web, miniprogram, worker, and reminder callers can reuse the same API without overfetching.
- Added the V5 Kline Workspace foundation: standalone candlestick chart, period switching, MA/BOLL/MACD/RSI/VOL controls, current-price line, manual sync, CZSC structure overlays, momentum context overlays, and AI answer evidence overlays.
- Added read-only `structure-view` and `momentum-context` APIs so Web, miniprogram, workers, reminders, and future review surfaces can reuse persisted CZSC snapshots without blocking page requests.
- Replaced the miniprogram stock detail legacy Chan matrix entry with a compact V5 AI Structure status surface backed by symbol-focused `workspace/bootstrap`.
- Added a user-scoped AI Structure outcome review feed for miniprogram/background clients, driven by positions/recent_chat/watchlist with compact per-symbol memory summaries.
- Added V5 outcome and mistake-memory review timeline to the Review Training page.
- Added the AI Structure background context contract: fundamentals, sector, fund flow, and market context remain context-only while CZSC lines stay the decision boundary.
- Clarified that background context must not appear as radar status, structure pipeline state, Kline evidence, or reminder triggers; it is reserved for future selection and position background use.
- Added a price-monitor regression test proving V5 AI Structure reminders are scanned and pushed even when no normal position stop-loss rows exist.
- Removed legacy `chan.py` / old radar runtime paths from the default app. V5 does not keep fallback, shadow, or comparison entry points.
- Removed old scanner, rotation, playbook, sand-table, multiverse, old AI Native fusion/agent/observation, and stop/reduce training code paths.
- Removed legacy frontend radar/Kline/chan/scanner/AI-training surfaces from the default bundle.
- Updated docs, config, database schema, and tests around the V5 CZSC-only product direction.

### Verification

- `npm run build`
- `.venv312/bin/python -m pytest tests -q`
- `PYTHONPATH=. venv/bin/python -m pytest tests/test_ai_structure_*.py tests/test_czsc_adapter_contract.py -q`
- `npm run build`
- `PYTHONPATH=. venv/bin/python -m pytest tests/test_ai_structure_reminder_bridge.py -q`
- `PYTHONPATH=. venv/bin/python -m pytest tests/test_ai_structure_outcome_worker.py tests/test_ai_structure_outcome_settlement.py -q`
- `PYTHONPATH=. venv/bin/python -m pytest tests/test_ai_structure_no_legacy_calls.py tests/test_ai_structure_outcome_settlement.py -q`
- Playwright screenshot smoke for AI Structure outcome timeline at desktop and mobile widths.
- `PYTHONPATH=. venv/bin/python -m pytest tests/test_ai_structure_chat_api.py -q`
- `PYTHONPATH=. venv/bin/python -m pytest tests/test_ai_structure_chat_api.py tests/test_ai_structure_auth_isolation.py -q`
- Playwright QA smoke for AI Structure workspace: ask -> chart evidence -> reminder create -> real price-monitor trigger -> ack ignored -> mistake memory.
- `PYTHONPATH=. venv/bin/python -m pytest tests/test_price_monitor_alerts.py tests/test_ai_structure_reminder_bridge.py -q`
- Playwright QA smoke for Review Training page V5 outcome timeline and existing behavior report.
- `PYTHONPATH=. venv/bin/python -m pytest tests/test_ai_structure_context_service.py tests/test_ai_structure_chat_api.py -q`
- `PYTHONPATH=. venv/bin/python -m pytest tests/test_ai_structure_universe.py tests/test_watchlist_data_sync.py -q`
- Isolated V5 data-loop smoke: `/api/data/sync-klines/{symbol}` -> CZSC snapshots -> AI structure contexts -> chat -> chart evidence.
- `npm run build`
- `PYTHONPATH=. venv/bin/python -m pytest tests/test_ai_structure_chart_context.py tests/test_ai_structure_chat_api.py tests/test_ai_structure_structure_view.py tests/test_ai_structure_momentum_context.py tests/test_ai_structure_no_legacy_calls.py -q`
- Playwright Kline Workspace QA smoke for drag/zoom boundaries, CZSC overlays, momentum overlays, and AI evidence layer independence.
