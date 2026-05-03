# AI Stop / Reduce Shadow Training V1

> Created: 2026-05-02
> Scope: AI shadow training for stop-loss and reduce-position decisions
> Status: Draft for `/plan-eng-review`
> Safety: Coach-only. No live trading. Trading-related content is for reference only and does not constitute investment advice.

交易相关内容仅供参考，不构成投资建议。

## Product Positioning

AI Stop / Reduce Shadow Training V1 is not a paper trading game and not an auto-trading system.

It is a shadow training loop for AI decision quality:

```text
Radar / Position Context
        ↓
Technical Agent judges structure risk
        ↓
Fundamental Tagger applies low-frequency hard constraints
        ↓
Rebalance Agent creates a stop/reduce intent
        ↓
Shadow Execution executes the intent in a paper account
        ↓
Scoring Agent settles Outcome + Process
        ↓
Only mistakes and high-value disagreements enter Case Memory
        ↓
Similar future setups receive a short calibration summary
```

The first version teaches the AI one narrow skill:

```text
When a holding is losing money or structure weakens,
should the AI reduce / exit instead of finding reasons to keep holding?
```

This matches CT-OS positioning: trading coach, not trading robot.

## V1 Goals

V1 trains stop-loss and reduce-position decisions only.

In scope:

- Current holdings and watchlist only.
- Actions: `REDUCE`, `EXIT`, `HOLD`, `WATCH_EXIT`.
- Technical context from existing AI Native Radar / Radar position context.
- Fundamental context as low-frequency tags and hard constraints only.
- Shadow execution through existing paper execution primitives.
- Scoring on two dimensions: Outcome and Process.
- Case Memory stores lessons, not a complete diary.

Out of scope:

- No `ADD` or buy-side training.
- No full-market stock selection.
- No live QMT execution.
- No automatic prompt or rule mutation.
- No full fundamental research agent in V1.
- No broad portfolio optimizer.
- No UI-first implementation before the backend loop works.

## Existing Code To Reuse

V1 should build on existing modules instead of creating a second simulator.

Relevant existing areas:

- `server/engines/ai_native/` — AI Native Radar transcript, scoring, replay evaluation, case memory, calibration.
- `server/engines/decision/position_coach.py` — position-aware structure coaching.
- `server/engines/execution/paper_models.py` — `PaperAccount`, `PaperIntent`, `PaperFill`, `PaperRiskConfig`.
- `server/engines/execution/paper_adapter.py` — paper risk checks and next-bar fill simulation.
- `server/engines/execution/paper_replay.py` — replay harness and paper metrics.
- `server/engines/execution/paper_store.py` — SQLite persistence for paper accounts, intents, fills, and replay runs.
- `server/services/fundamental_service.py` — current MVP fundamental/fallback analysis.

Do not create another paper account or fill model unless `/plan-eng-review` finds a hard blocker.

## Agent Boundaries

### Technical Agent

The Technical Agent answers:

```text
Does the current structure require reduce / exit / watch-exit?
```

It consumes:

- Multi-level Radar structure.
- Chan center / BSP / divergence evidence.
- ATR or structure risk lines.
- Position state, cost, PnL, weight.
- Freshness and data quality warnings.
- Retrieved calibration summary for similar prior mistakes.

It does not consume fundamental reports directly.

Recommended output:

```json
{
  "technical_bias": "REDUCE",
  "risk_state": "STRUCTURE_BREAKDOWN",
  "confidence": 0.72,
  "stop_line": 18.2,
  "invalidation_line": 18.2,
  "repair_line": 19.4,
  "evidence": [
    "30m 跌破 ZD",
    "5m 修复失败",
    "当前价贴近持仓风险线"
  ],
  "disclaimer": "仅供参考，不构成投资建议"
}
```

### Fundamental Tagger

V1 fundamental analysis is intentionally narrow.

It provides hard constraints, not free-form trading advice:

| Conviction | V1 Rule |
|---|---|
| `支持` | May allow higher holding tolerance, but must never override technical stop-loss |
| `中性` | Do not influence short-term direction; only preserve default caps |
| `回避` | `max_position_pct = 0`; forbid `ADD`; `HOLD` must degrade to `WATCH_EXIT`, `REDUCE`, or `EXIT` |

The fundamental layer is allowed to reduce risk. It is not allowed to cancel a triggered technical stop-loss.

V1 should use the existing `fundamental_service.py` verdict vocabulary: `支持` / `中性` / `回避`. Do not introduce a separate four-level `STRONG` / `NEUTRAL` / `WEAK` / `AVOID` enum in V1. If V2 gets richer fundamental data, split `回避` into weak vs avoid later.

Recommended output:

```json
{
  "conviction": "中性",
  "max_position_pct": 12,
  "red_flags": [],
  "source": "fundamental_service.v1",
  "as_of": "2026-05-02"
}
```

### Rebalance Agent

The Rebalance Agent synthesizes the final shadow decision.

It only creates an intent. It must not execute, mutate the paper account, or write fills.

Recommended contract:

```json
{
  "intent_type": "STOP_REDUCE",
  "intent_id": "stop_reduce:1:sh.603893:2026-05-02T10:30:00+08:00",
  "idempotency_key": "1:sh.603893:stop_reduce:2026-05-02T10:30:00+08:00:run_123:close_below_stop",
  "user_id": 1,
  "symbol": "sh.603893",
  "action": "REDUCE",
  "current_weight_pct": 18,
  "target_weight_pct": 10,
  "quantity_policy": "reduce_to_target",
  "conditions": {
    "activate_if": [
      {
        "condition_id": "close_below_stop",
        "source": "daily_close",
        "field": "close",
        "op": "<=",
        "value": 18.2,
        "valid_on": "2026-05-02"
      }
    ],
    "cancel_if": [
      {
        "condition_id": "close_above_repair",
        "source": "daily_close",
        "field": "close",
        "op": ">=",
        "value": 19.4,
        "valid_on": "2026-05-02"
      }
    ]
  },
  "reason": {
    "technical": "30m 破位且 5m 修复失败",
    "fundamental": "中性，不提供继续重仓理由",
    "position": "亏损持仓，风险线已触发"
  },
  "evidence_refs": {
    "technical_run_id": 123,
    "radar_snapshot_id": "optional",
    "fundamental_snapshot_id": "optional",
    "calibration_summary_id": "optional"
  },
  "disclaimer": "仅供参考，不构成投资建议"
}
```

Idempotency key rule:

```text
user_id:symbol:intent_type:as_of_minute:technical_run_id:primary_condition_id
```

The timestamp must be precise to the minute. If an AI Native Radar run id exists, include it. This prevents one same-day morning breakdown from incorrectly deduping a separate afternoon breakdown.

Condition rule:

```text
V1 conditions must be structured predicates, not free-form Chinese strings.
Only closed daily bars can activate or cancel a stop/reduce intent in V1.
Raw natural-language condition text can be stored for display, but scoring and execution must use structured conditions.
```

Recommended evaluator:

```text
StopReduceConditionEvaluator
  input: RebalanceIntent.conditions + daily close row
  output: ACTIVATED / CANCELLED / WAITING / EXPIRED / DATA_MISSING
```

This evaluator should be callable from both offline replay scripts and a future post-close worker.

Allowed V1 actions:

| Action | Meaning |
|---|---|
| `HOLD` | Keep holding; no paper intent should be generated |
| `WATCH_EXIT` | No fill yet; record watch condition and future settlement target |
| `REDUCE` | Reduce to a lower target weight |
| `EXIT` | Reduce target weight to zero, subject to paper risk checks |

### Shadow Execution Agent

The Shadow Execution Agent consumes approved rebalance intents and maps them to existing paper execution contracts.

It owns:

- Quantity calculation from `target_weight_pct`.
- `PaperIntent` creation.
- Paper risk checks.
- Paper fill simulation.
- Persistence through `paper_store`.

It must respect:

- T+1 sellable quantity.
- Available quantity.
- Protected base quantity.
- Slippage and fees.
- Limit up / limit down.
- Zero volume / missing next bar.
- Max trades per day.
- Max single order amount.
- Duplicate idempotency key.

It must not reinterpret the Rebalance Agent's reasoning.

### Scoring Agent

V1 scoring has only two dimensions.

#### Outcome Score

Outcome Score asks:

```text
Did the action improve the shadow account outcome over the settlement window?
```

V1 settlement windows:

- `T+1`
- `T+3`
- `T+5`

Examples:

- AI reduced and price continued down: positive.
- AI held and price continued below stop: negative.
- AI reduced and price quickly repaired above `repair_line`: possible early-reduce penalty.
- AI exited after fundamental `回避` and price fell further: positive.

#### Process Score

Process Score asks:

```text
Did the AI follow the explicit rules and risk boundaries?
```

Checks:

- Did it respect stop / invalidation lines?
- Did it avoid holding heavy after `fundamental=回避`?
- Did it avoid averaging down under `回避`?
- Did it act before confirmation when the intent required confirmation?
- Did it preserve a clear cancel condition?

Risk Score, Timing Score, and Explanation Score are intentionally deferred. They require deeper bar-by-bar attribution and evidence verification.

Recommended score payload:

```json
{
  "score_id": "score:stop_reduce:1:sh.603893:2026-05-07",
  "intent_id": "stop_reduce:1:sh.603893:2026-05-02T10:30:00+08:00",
  "outcome_score": 82,
  "process_score": 70,
  "final_score": 76,
  "settlement_window": "T+5",
  "settlement_source": "kline_lake.day",
  "settlement_prices": [
    {"date": "2026-05-04", "close": 17.9},
    {"date": "2026-05-06", "close": 17.4},
    {"date": "2026-05-11", "close": 17.1}
  ],
  "tags": [
    "REDUCE_WAS_CORRECT",
    "STOP_LINE_RESPECTED",
    "TECHNICAL_SIGNAL_VALID"
  ],
  "lesson_candidate": false,
  "notes": "减仓后 5 日内继续下跌，影子账户避免亏损扩大。"
}
```

Settlement price rule:

```text
Use `server/db/kline_lake.py` daily `klines` as the source of T+1/T+3/T+5 close prices.
Persist the exact settlement price path into the score payload for reproducibility.
Do not add a separate `daily_close_snapshots` table in V1 unless `/plan-eng-review` finds kline_lake unavailable for the target symbols.
```

## Case Memory Policy

Principle:

```text
Case Memory records lessons, not a diary.
```

Do not store every normal correct decision in retrieval memory. Ordinary correct samples should update aggregate stats only.

Store only:

1. Clear mistakes.
2. High-loss mistakes.
3. Discipline violations.
4. High-value agent disagreements.
5. Human-marked learning samples.

Examples that should enter Case Memory:

- AI chose `HOLD`, stop/invalidation broke, price continued down.
- AI chose `REDUCE`, price repaired quickly and the reduce was clearly premature.
- Fundamental was `回避`, but final intent remained heavy `HOLD`.
- Technical Agent wanted `REDUCE`, Portfolio/Fundamental allowed it, but Rebalance Agent chose `HOLD` and loss expanded.
- User manually marks the case as worth learning.

Recommended memory item:

```json
{
  "case_id": "case:stop_reduce:1:sh.603893:2026-05-07",
  "case_key": "holding:loss:structure_breakdown:near_stop",
  "symbol": "sh.603893",
  "mistake_type": "AI_HELD_AFTER_STOP_BROKEN",
  "original_action": "HOLD",
  "better_action": "REDUCE",
  "outcome": "5日继续下跌 6.1%",
  "loss_delta_pct": -3.2,
  "lesson": "亏损持仓跌破结构防线后，不应把可能修复当成继续持有理由。",
  "context_hint": "同类结构下 REDUCE 权重应高于 HOLD_WATCH。",
  "created_at": "2026-05-07T15:30:00+08:00"
}
```

## Calibration And Retrieval

V1 learning is retrieval-based. It must not automatically mutate code, thresholds, or prompts.

Three layers:

```text
1. Immutable Case Memory
   Store selected mistakes and high-value disagreements.

2. Aggregated Calibration
   Summarize repeated error patterns.

3. Runtime Retrieval Injection
   Inject a short summary when a similar setup appears.
```

Recommended aggregate stats:

```json
{
  "calibration_key": "holding:loss:structure_breakdown:near_stop",
  "total_count": 27,
  "mistake_count": 9,
  "avg_loss_if_hold_pct": -3.2,
  "avg_benefit_if_reduce_pct": 1.8,
  "latest_mistake_case_id": "case:stop_reduce:1:sh.603893:2026-05-07",
  "updated_at": "2026-05-07T15:30:00+08:00"
}
```

Runtime injection must be short:

```text
相似历史错误：
过去 9 次亏损持仓跌破结构防线后继续 HOLD，7 次亏损扩大，平均多亏 3.2%。
最近一次错误：AI 等待修复，但 5 日内继续下跌 6.1%。
教训：跌破防线后不要把“可能修复”当成持仓理由。
```

This keeps context small and focused.

## Suggested Data Model

Exact schema should be finalized in `/plan-eng-review`, but V1 likely needs:

```text
ai_rebalance_runs
  One analysis run per symbol / as_of.

ai_holding_plans
  Daily parent object. One plan per user / symbol / trade_date.
  plan_id must stay stable for that daily key, so child intents keep their source_plan_id link across intraday plan refreshes.

ai_rebalance_intents
  Child stop/reduce action intent generated only when the daily holding plan reaches REDUCE_ALERT or EXIT_ALERT.

paper_intents / paper_fills / paper_accounts
  Existing paper execution tables. Reuse.

ai_stop_reduce_scores
  Outcome + Process settlement result, including settlement_prices_json for replayability.

ai_case_memory
  Sparse mistake and high-value disagreement memory.

ai_calibration_stats
  Aggregated calibration by case_key.

fundamental_snapshots
  Low-frequency conviction and red flag snapshots.
```

V1 should not create `daily_close_snapshots` by default. Daily close data already belongs to `kline_lake.klines`; score rows should snapshot the exact closes they consumed.

Every score and memory item must trace back to:

- Original Radar / AI Native context.
- Fundamental snapshot.
- Rebalance intent.
- Paper execution result.
- Settlement price path.

## First Implementation Phases

### Phase 1: Contracts And Storage

- Define `RebalanceIntent` domain model.
- Define stop/reduce score payload.
- Define case memory payload and insertion policy.
- Define structured condition predicates and `StopReduceConditionEvaluator`.
- Add migrations for new V1 tables.
- Add focused unit tests for contract validation and idempotency.

### Phase 2: Offline Shadow Loop

- Build a script that runs stop/reduce analysis over historical holdings or selected symbols.
- Convert `REDUCE` / `EXIT` into `PaperIntent`.
- Reuse paper execution and store results.
- Settle `T+1`, `T+3`, `T+5` outcomes.
- Start with offline replay over recent historical holding fragments, not realtime market signals.
- Use `kline_lake.day` close prices for settlement and persist the consumed close path in each score row.

### Phase 2.5: Daily Automation Loop

V1 daily runner:

```bash
python -m server.workers.stop_reduce_daily --user-id 1 --limit 20 --output reports/stop_reduce_daily.md
```

Execution order:

```text
1. stop_reduce_monitor
   Generate/update daily holding plans for current positions.
   Enqueue child rebalance intents only for REDUCE_ALERT / EXIT_ALERT.

2. stop_reduce_settlement
   Load pending intents.
   If enough future daily close bars exist, run shadow execution and score.
   If not enough bars exist, leave the intent in WAITING.

3. Markdown report
   Summarize plans, enqueued intents, settled intents, and Case Memory writes.
```

This runner is still coach-only. It does not place broker orders.

### Phase 3: Memory And Calibration

- Insert only mistakes and high-value disagreements into Case Memory.
- Aggregate `case_key` stats.
- Retrieve matching memory for future runs.
- Inject a short calibration summary into Rebalance Agent context.

### Phase 4: Evaluation

- Run 20-50 samples.
- Compare decisions with and without calibration injection.
- Confirm whether calibration changes `HOLD` vs `REDUCE` behavior in explainable ways.

### Phase 5: Minimal UI

Only after the backend loop works:

- Show latest AI shadow stop/reduce intents.
- Show shadow execution result.
- Show score and learned lesson.
- Show whether a similar historical mistake influenced the current decision.

## Success Criteria

V1 succeeds when:

1. Every AI stop/reduce decision has a traceable intent.
2. Every shadow execution respects realistic paper constraints.
3. Every settlement explains why the decision was good or bad.
4. Only high-value mistakes enter Case Memory.
5. Similar future setups retrieve those mistakes.
6. Calibration injection changes future `HOLD` / `REDUCE` decisions in a traceable way.

V1 fails if it only produces a pretty paper PnL chart but cannot explain what the AI learned.

## Recommended Next Step

Run `/plan-eng-review` before implementation.

The engineering review should lock:

- `RebalanceIntent` contract.
- Shadow execution mapping to `PaperIntent`.
- Stop/reduce settlement algorithm.
- Case Memory schema and insertion thresholds.
- Calibration retrieval key design.
- Minimum test matrix.

## Plan Eng Review Result

Reviewed on 2026-05-02 for branch `codex/ai-native-radar-observation`.

Status: `CLEAR`

Key decisions locked:

1. `idempotency_key` must include minute-level `as_of`, AI/Radar run id when available, and primary condition id.
2. Settlement should reuse `kline_lake.day` close prices and persist the consumed settlement path inside the score payload.
3. `confirm_if` / `cancel_if` must be structured predicates evaluated by `StopReduceConditionEvaluator`, not free-form display text.
4. V1 fundamental verdicts should use the existing `fundamental_service.py` vocabulary: `支持` / `中性` / `回避`.
5. Offline replay over historical holding fragments should run before realtime signal integration.

Test plan artifact:

```text
~/.gstack/projects/ct-os-v4/markqu-codex-ai-native-radar-observation-eng-review-test-plan-20260502-154011.md
```
