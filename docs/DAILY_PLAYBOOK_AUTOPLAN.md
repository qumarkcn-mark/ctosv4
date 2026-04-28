# 今日作战台 + 计划内复盘 Autoplan

> Created: 2026-04-26
> Branch: codex/architecture-radar-migration
> Commit: b313114
> Status: APPROVED PREMISES, READY FOR ENG PLAN

## Product Premises

User confirmed these premises on 2026-04-26:

1. CT-OS remains a trading coach, not an auto-trading system.
2. The first success metric is discipline improvement, not prediction accuracy.
3. The next product surface should prioritize a daily playbook over more single-symbol Radar detail.
4. Every recorded trade must carry a plan relationship so review has teeth.

## Plan Summary

Build a daily execution-coach loop around existing Radar plans and coach events.

The user starts the day with a short watch list, explicit trigger prices, invalidation
conditions, and one-click handoff into Radar. During the session, CT-OS records
deterministic plan triggers and user responses. After the session, Behavior Report
shows whether trades followed the plan, ignored alerts, or came from emotion.

This is not QMT execution. It records and coaches.

## What Already Exists

| Need | Existing Code | Reuse Decision |
|---|---|---|
| Single-symbol structure and plans | `server/api/radar.py`, `server/engines/decision/radar_planner.py` | Reuse as source of candidate plans. Do not duplicate structure rules. |
| Structured coach event log | `server/engines/coach/event_log.py`, `coach_events`, `strategy_triggers`, `alert_deliveries` | Extend with playbook event types and metadata. |
| Trade creation and position recalculation | `server/api/trades.py`, `server/services/position_calc.py` | Add plan relationship fields to trades. Keep recalc untouched. |
| Entry thesis memory | `server/services/entry_thesis.py`, `positions.entry_thesis_json` | Link plan facts into BUY thesis when available. |
| Scanner candidate list | `server/api/scanner.py`, `web/src/pages/Scanner.jsx` | Feed playbook suggestions, but do not make Scanner the playbook itself. |
| Radar UI | `web/src/components/TRadarV2.jsx`, `web/src/pages/ChanView.jsx` | Add plan context handoff. Avoid burying daily work in the right sidebar. |
| Behavior report | `server/api/behavior.py`, `web/src/pages/BehaviorReport.jsx` | Add discipline metrics for plan adherence. |

## Not In Scope

| Item | Reason |
|---|---|
| QMT integration or order execution | Violates current coach-only premise. Save for private Phase 3 after dry-run risk gate. |
| Real-time quote-as-execution-price | Existing contracts forbid using BaoStock/Tencent as execution price. |
| Full strategy rewrite | Radar already emits plans. First wire the loop, then improve rules from observed misses. |
| 1-minute playbook triggers | Prior learning: BaoStock does not support 1-minute data. First version uses day/30/5. |
| Social sharing, public signals, leaderboards | Not useful for one trader's discipline loop. |

## CEO Review

### Premise Challenge

| Premise | Verdict | Risk If Wrong | Mitigation |
|---|---|---|---|
| Discipline beats prediction as next wedge | Accepted | Product may feel less exciting than new signals | Make the first screen immediately useful before market open. |
| Daily playbook should sit above Radar | Accepted | Duplicate Scanner/Radar navigation | Keep playbook as orchestration, not a new analysis engine. |
| Mandatory plan relationship on trades is acceptable | Accepted | Extra friction during fast manual entry | Default to "计划外" only when no plan is selected, keep one-tap choices. |
| No execution integration now | Accepted | User still manually trades in broker app | Correct for current product positioning and risk boundary. |

### Dream State Delta

```text
CURRENT
  Radar explains one symbol.
  Scanner finds candidates.
  Trades record what happened.
  Behavior Report scores broad habits.

THIS PLAN
  Morning: shortlist + trigger map.
  Intraday: plan triggers + user responses.
  Trade entry: plan relationship recorded.
  Evening: planned vs unplanned review.

12-MONTH IDEAL
  CT-OS knows the user's recurring failure modes.
  It pre-commits the user to a few high-quality setups.
  It interrupts only high-signal deviations.
  It proves, with personal history, which behaviors cost money.
```

### Implementation Alternatives

| Approach | Effort | Pros | Cons | Decision |
|---|---:|---|---|---|
| Add playbook as a first-class API/page | Medium | Clear user workflow, clean data model | More files touched | Chosen. Complete enough to become daily habit. |
| Stuff playbook into Radar sidebar | Low | Fast | Daily workflow buried in single-symbol view | Rejected. Wrong hierarchy. |
| Only add trade tags to Behavior Report | Low | Small backend change | No morning/trigger loop, weak behavior change | Rejected. Postmortem without precommitment is too late. |

### Scope Decisions

| Decision | Classification | Principle | Rationale | Rejected |
|---|---|---|---|---|
| Create daily playbook as separate page/API | Auto | Completeness | The user needs a day-level cockpit, not another Radar panel. | Radar-only implementation |
| Reuse Radar contract for per-symbol plan generation | Auto | DRY | Structure and strategy rules already live there. | Duplicate strategy rules |
| Store plan relationships on trades and events | Auto | Explicit | Review must query without parsing prose. | Only `reason_text` |
| Keep alerts as candidates/events first | Auto | Safety | Coach-only boundary remains intact. | Push-first implementation |

## Design Review

Design system note: `DESIGN.md` was not found at the project root, `web/`, or `docs/`.
This is a project-rule gap. Before implementation, either add `DESIGN.md` or explicitly
approve using the existing app styles in `App.css`, `Scanner.css`, `ChanView.css`, and
`TRadarV2.css`.

### Information Hierarchy

The new first screen should be operational, not decorative.

1. Top band: trading day, data freshness, market session state, total open plans.
2. Primary table: today's symbols with current state, trigger price, plan status, and next action.
3. Right detail panel or drawer: selected symbol's Radar-derived plan, invalidation condition, and response buttons.
4. Bottom review strip: today's actions so far, planned/unplanned counts.

### Required UI States

| State | Required Behavior |
|---|---|
| No playbook today | Show "生成今日作战计划" and candidate sources: positions, scanner, watchlist. |
| Partial data stale | Show stale badge per symbol, block action alerts, still allow manual review. |
| Radar engine error | Keep symbol row with error state and "去雷达复核". |
| Triggered plan | Highlight row, expose response buttons: 已执行 / 忽略 / 继续观察 / 标记失效. |
| Trade recorded without plan | Mark as 计划外 and ask user to classify reason. |
| Market closed | Disable intraday trigger copy, emphasize review. |

### UX Decision

The page should be named `今日作战`, replacing or sitting before `今日机会`.
`今日机会` is discovery. `今日作战` is execution discipline. If both remain, navigation
order should be `交易看板 / 今日作战 / 今日机会 / 缠论看盘 / 调仓罗盘 / 行为分析 / 模拟训练`.

This is a taste decision but recommended: trading starts with plan, not opportunities.

## Engineering Review

### Proposed Backend Shape

Add a small playbook domain, not a new analysis engine.

```text
Radar API
  -> Playbook Builder
       reads positions, watchlist/scanner candidates, Radar plans
       writes daily_playbooks + daily_playbook_items
       records coach_events
  -> Playbook API
       GET /api/playbook/today
       POST /api/playbook/today/generate
       POST /api/playbook/items/{id}/response
       POST /api/playbook/trades/{trade_id}/classify
  -> Behavior API
       adds plan adherence metrics
```

### Data Model

```sql
CREATE TABLE daily_playbooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    trade_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    generated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    source_json TEXT,
    summary_json TEXT,
    UNIQUE(user_id, trade_date)
);

CREATE TABLE daily_playbook_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playbook_id INTEGER NOT NULL REFERENCES daily_playbooks(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT,
    mode TEXT NOT NULL,
    plan_id TEXT,
    strategy_id TEXT,
    status TEXT NOT NULL DEFAULT 'WATCHING',
    trigger_json TEXT,
    invalidation_json TEXT,
    radar_snapshot_json TEXT,
    response_json TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

Add columns to `trades`:

```sql
ALTER TABLE trades ADD COLUMN playbook_item_id INTEGER;
ALTER TABLE trades ADD COLUMN plan_relationship TEXT DEFAULT 'UNKNOWN';
ALTER TABLE trades ADD COLUMN discipline_tag TEXT;
ALTER TABLE trades ADD COLUMN coach_event_id TEXT;
```

Allowed `plan_relationship`:

```text
PLANNED
UNPLANNED
EMOTIONAL
AFTER_ALERT
IGNORED_ALERT
UNKNOWN
```

### API Contracts

`GET /api/playbook/today?user_id=1`

Returns:

```json
{
  "status": "success",
  "data": {
    "trade_date": "2026-04-26",
    "status": "OPEN",
    "freshness": {},
    "items": [],
    "metrics": {
      "planned_trades": 0,
      "unplanned_trades": 0,
      "triggered_items": 0,
      "ignored_triggers": 0
    },
    "disclaimer": "仅供参考，不构成投资建议"
  }
}
```

`POST /api/playbook/today/generate`

Inputs:

```json
{
  "user_id": 1,
  "sources": ["positions", "scanner", "watchlist"],
  "max_items": 8
}
```

Rules:

- Include current holdings first.
- Include scanner `ready` candidates next.
- Cap first version at 8 symbols.
- For each symbol, call Radar or reuse a fresh Radar snapshot.
- Do not generate action alerts when Radar freshness is stale.

`POST /api/playbook/items/{id}/response`

Allowed responses:

```text
ACKNOWLEDGED
EXECUTED
IGNORED
CONTINUE_WATCHING
INVALIDATED
```

Every response writes `USER_MARKED_ACTION` through `record_coach_event`.

### Frontend Components

```text
web/src/pages/DailyPlaybook.jsx
web/src/pages/DailyPlaybook.css
web/src/components/PlaybookItemRow.jsx
web/src/components/PlanResponseButtons.jsx
```

`App.jsx` adds a new nav tab. Existing Scanner remains discovery.

### Behavior Metrics

Add to behavior report:

| Metric | Meaning |
|---|---|
| plan_adherence_rate | planned trades / total trades with known relationship |
| unplanned_loss_amount | realized loss from unplanned trades |
| alert_follow_rate | executed or acknowledged alerts / triggered alerts |
| ignored_alert_outcome | later outcome of ignored triggers |
| emotional_trade_rate | trades tagged EMOTIONAL / total trades |

The first version can compute from `trades` plus `coach_events` without outcome attribution.
Outcome attribution can arrive later once pair matching is stable.

### Failure Modes Registry

| Failure Mode | Severity | Rescue |
|---|---|---|
| Radar engine slow for 8 symbols | High | Generate holdings first, cap max_items, cache snapshots by date/symbol. |
| Stale structure creates false confidence | High | Per-item stale badge, block action alert status, keep manual review only. |
| User skips plan tagging because it is annoying | High | One-tap defaults in trade form, allow later classification. |
| Duplicate daily playbooks | Medium | Unique `(user_id, trade_date)`, idempotent generate endpoint. |
| Behavior report overclaims causality | Medium | Label metrics as discipline evidence, not strategy proof. |
| UI becomes another noisy dashboard | High | Show only today's active items, cap at 8, hide deep structure until row expansion. |

### Test Diagram

| Path | Test Type | Required Test |
|---|---|---|
| Generate empty playbook | API unit/integration | Creates one playbook for today, idempotent on second call. |
| Generate from positions | API integration | Holding symbols become HOLDING items before scanner candidates. |
| Radar stale item | API unit | Item status marks stale and does not create action alert. |
| User response | API integration | Writes `USER_MARKED_ACTION` and updates item response. |
| Trade with plan | API integration | `create_trade` stores relationship and event reference. |
| Trade without plan | API integration | Defaults to `UNKNOWN` or explicit `UNPLANNED`, never crashes. |
| Behavior metrics | Service test | Planned/unplanned counts computed from fixtures. |
| UI empty state | Frontend/browser QA | User can generate today's playbook. |
| UI trigger response | Frontend/browser QA | Response buttons update row state without layout shift. |

### Suggested Test Commands

```bash
./venv/bin/pytest tests/test_playbook_api.py tests/test_trades_entry_thesis.py tests/test_coach_event_log.py -v
cd web && npm run build
```

## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| 1 | CEO | Prioritize daily playbook over deeper Radar details | Auto | Completeness | It closes the real 실盘 loop: plan, trigger, response, review. | More Radar panels |
| 2 | CEO | Keep QMT execution out of scope | Auto | Safety | Current product promise is coach-only. | Execution candidate work |
| 3 | Design | Add a dedicated `今日作战` surface | Taste | Explicit | Day-level work deserves day-level hierarchy. | Hide inside Radar |
| 4 | Eng | Reuse Radar plans as inputs | Auto | DRY | Avoid strategy duplication and data-source drift. | New playbook strategy engine |
| 5 | Eng | Extend trades with plan relationship | Auto | Completeness | Behavior review needs queryable facts. | Reason text parsing |
| 6 | Eng | Use coach events for responses | Auto | DRY | Existing append-only audit log already fits. | New response-only log |

## Cross-Phase Themes

1. Discipline loop beats signal expansion.
2. The plan must be queryable, not prose-only.
3. UI must reduce intraday choices, not add another place to stare.
4. Data freshness must be visible at the item level.

## Open Taste Decision

Navigation: replace `今日机会` with `今日作战`, or keep both.

Recommendation: keep both for now, but move `今日作战` before `今日机会`.
The user still needs discovery, but execution discipline should come first.

## Implementation Sequence

1. Add database migrations and domain helpers for daily playbooks.
2. Add `server/api/playbook.py` and register router.
3. Extend `TradeCreate` and DB insert with plan relationship fields.
4. Add behavior service metrics for plan adherence.
5. Add `DailyPlaybook.jsx` page and route/nav entry.
6. Add focused tests.
7. Run `/review`, `/qa`, then `/ship`.

## Review Verdict

DONE_WITH_CONCERNS.

The plan is strategically sound and fits existing architecture. The main concern is
the missing `DESIGN.md`, which project rules require for UI decisions. Resolve that
before implementation or explicitly approve reusing the current app's visual system.
