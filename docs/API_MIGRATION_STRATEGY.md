# API Migration Strategy

> Created: 2026-04-25
> Scope: `/api/chan/matrix/v2/{symbol}` -> `/api/radar/{symbol}`

This document defines how CT-OS migrates from the legacy Chan matrix endpoint to
the new Radar contract without breaking the existing UI.

## Migration Principle

`/api/radar/{symbol}` is the product contract going forward.

`/api/chan/matrix/v2/{symbol}` is compatibility only. It may remain available
while old pages or tests depend on its shape, but new product behavior should be
implemented through structure/decision engines and exposed through Radar.

## Version Ownership

| Endpoint | Status | Owner | Purpose |
|---|---|---|---|
| `/api/radar/{symbol}` | Current | API + engines | Formal Radar contract for Phase 1/2 |
| `/api/chan/matrix/v2/{symbol}` | Compatibility | API adapter | Preserve old TRadar/Chan behavior during migration |
| `/api/chan/detail/{symbol}` | Existing detail | API adapter | K-line/structure detail view until ChanView migrates |

Rules:

- New fields should be added to `/api/radar/{symbol}` first.
- Legacy `/api/chan/matrix/v2` should only receive compatibility fixes.
- No new frontend page should depend on `/api/chan/matrix/v2`.
- Radar formal structure must come from `server.engines.structure.chan_adapter`.
- Radar decision fields must come from `server.engines.decision.*`.
- AI narrative must not be mixed into Structure or Decision fields.

## Migration Phases

### Phase 0: Contract Lock

Status: complete.

- `docs/RADAR_API_CONTRACT.md` defines Radar response shape.
- `/api/chan/matrix/v2` has characterization tests.
- `/api/radar/{symbol}` has contract tests.
- `docs/DATA_SOURCE_CONTRACT.md` fixes BaoStock/Tencent/TDX authority boundaries.

### Phase 1: Backend Source Switch

Status: complete.

- Radar normal path consumes `chan_adapter`.
- Radar no longer falls back to legacy matrix decision input.
- Radar returns stable error/freshness envelope when adapter fails.
- Structure derived facts are split into pure modules under `server/engines/structure/`.
- Decision rules are split into pure modules under `server/engines/decision/`.

### Phase 2: Frontend Switch

Status: complete for active Radar UI path.

- `TRadarV2` now fetches `/api/radar/{symbol}`.
- `TRadarV2` uses a frontend adapter to preserve its existing internal display shape.
- Visual redesign is out of scope for this migration step.

Follow-up polish:

- ChanView K-line detail should migrate from old mixed fields to `chan_adapter` detail fields.
- UI should display Radar `data_source` and `freshness` explicitly.
- Empty/Holding mode display should remain mutually exclusive.

### Phase 3: Legacy Freeze

Status: active.

- Freeze `/api/chan/matrix/v2` behavior with tests only.
- Do not add strategy, alert, scanner, rotation, or behavior-coach logic to it.
- Add deprecation notes in API docs once all active UI paths use Radar/detail contracts.

### Phase 4: Compatibility Removal

Status: future.

Removal is allowed only after:

- No frontend imports call `/api/chan/matrix/v2`.
- Scanner/Rotation/Behavior pages consume Radar or dedicated engine contracts.
- Contract tests cover the replacement behavior.
- User-visible regression testing passes.

## `/api/chan/matrix/v2` Field Ownership

| Field | Current owner | New owner / target | Classification | Migration note |
|---|---|---|---|---|
| `symbol` | API | Domain `normalize_symbol` | API/domain | Preserve canonical `sh.600519` shape |
| `matrix_a` | legacy matrix | `structure.levels` + `structure.systems.short_term` | UI compatibility | Replace with Radar `levels day/30/5` |
| `matrix_b` | legacy matrix | `structure.levels` + `structure.systems.swing` | UI compatibility | Replace with Radar `levels day/60/15` |
| `week` | legacy matrix | `structure.levels.week` | Structure | Must come from `chan_adapter` |
| `interval_nesting_a` | legacy matrix | `structure.systems.short_term.interval_nesting` | Structure derived | Now derived in `nesting.py` |
| `interval_nesting_b` | legacy matrix | `structure.systems.swing.interval_nesting` | Structure derived | Now derived in `nesting.py` |
| `forward_analysis_a` | legacy matrix | `plans` / `entry_plan` / `holding_plan` | Decision/UI compatibility | Do not add new rules here |
| `forward_analysis_b` | legacy matrix | `plans` / secondary system context | Decision/UI compatibility | Future Rotation may use dedicated contract |
| `strategy_classification` | legacy matrix | `strategy` | Decision | Should follow Strategy Contract |
| `entry_checklist` | API helper | `entry_plan.conditions` | Decision | Now from `entry_planner.py` in Radar |
| `holding_status` | API helper | `holding_plan.legacy_status` / future normalized fields | Decision | Now from `holding_manager.py` |
| `holding_stage_v2` | legacy `chan_service` | future `holding_plan.stage` details | Decision compatibility | Must be replaced before legacy removal |
| `stop_atr_check` | legacy `chan_service` | `entry_plan.risk.stop_check` | Decision/risk | Now from `risk_sizing.py` in Radar |
| `targets` | legacy `chan_service` | `entry_plan.targets` | Decision/target | Now from `target_planner.py` in Radar |
| `position_sizing` | legacy `chan_service` | `entry_plan.position_sizing` | Decision/risk | Now from `risk_sizing.py` in Radar |
| `reward_ratio` | API helper | `entry_plan.reward_ratio` | Decision/risk | Now from `target_planner.py` in Radar |
| `data_freshness` | API helper | `freshness` | Data/freshness | Radar uses adapter freshness |
| `error` | API | Radar error envelope | API | Stable envelope required |

## Structure Field Rules

The following fields are formal structure or derived structure facts:

- `bis`
- `segs`
- `bi_zhongshus`
- `seg_zhongshus`
- `bsps`
- `state`
- `zoushi_type`
- `patterns`
- `classifications`
- `div_info`
- `interval_nesting`

Formal basic structure must be traceable to `chan_adapter` and `server/vendor/chan_py`.

Derived structure facts must live under `server/engines/structure/` and must not
call API, DB, LLM, or push code.

## Decision Field Rules

The following fields are decision facts:

- `strategy`
- `entry_plan`
- `holding_plan`
- `plans`
- `entry_checklist`
- `holding_status`
- `targets`
- `position_sizing`
- `reward_ratio`
- `stop_atr_check`

Decision facts must live under `server/engines/decision/` and consume only
domain inputs plus structure facts. They must not call `chan_service.py`.

## AI / Narrative Rules

Narrative is not part of the matrix migration. It belongs to a future
coach/narrative layer.

AI may consume:

- `structure`
- `strategy`
- `entry_plan`
- `holding_plan`
- `plans`
- `freshness`

AI must not create:

- new structure facts
- trigger prices
- stop prices
- target prices
- position sizing
- execution intents

## Compatibility Test Requirements

Before removing or changing `/api/chan/matrix/v2`, keep tests for:

- empty mode legacy entry fields
- holding mode legacy holding fields
- engine error stable envelope
- Radar empty/holding mutual exclusion
- Radar adapter source metadata
- Radar stale/error freshness envelope

Current coverage:

- `tests/test_chan_matrix_v2_contract.py`
- `tests/test_radar_api.py`
- `tests/test_chan_adapter_contract.py`
- `tests/test_derived_facts.py`
- `tests/test_structure_modules.py`
- `tests/test_entry_planner.py`
- `tests/test_holding_manager.py`
- `tests/test_target_risk_planners.py`

## Removal Checklist

`/api/chan/matrix/v2` can be deprecated when all items are true:

- [x] No active frontend fetches `/api/chan/matrix/v2`.
- [x] ChanView has a Radar/detail compatible data adapter.
- [x] RotationCompass uses a dedicated planner contract.
- [x] Scanner no longer consumes matrix fields directly.
- [x] BehaviorReport no longer consumes market structure from matrix fields.
- [x] QA confirms empty and holding workflows through Radar.
- [x] Changelog documents the deprecation.

2026-04-26 status:

- `web/src/components/TRadarV2.jsx` fetches `/api/radar/{symbol}`.
- `web/src/pages/ChanView.jsx` composes `KlineChart` detail + `TRadarV2`; it does not call `/api/chan/matrix/v2`.
- `RotationCompass` consumes `/api/rotation/compass`.
- `Scanner` consumes `/api/scan/*`.
- `BehaviorReport` consumes `/api/behavior/report`.
- Regression run passed with `139 passed`; frontend `npm run build` passed in the same migration window.
