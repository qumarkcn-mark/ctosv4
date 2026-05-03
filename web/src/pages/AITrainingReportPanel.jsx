import { useCallback, useEffect, useMemo, useState } from 'react'
import { API_BASE } from '../config.js'

const STATUS_OPTIONS = [
  { id: '', label: '全部' },
  { id: 'WAITING', label: '等待结算' },
  { id: 'SETTLED', label: '已评分' },
]

export default function AITrainingReportPanel() {
  const [report, setReport] = useState(null)
  const [trainingStatus, setTrainingStatus] = useState(null)
  const [symbolInput, setSymbolInput] = useState('')
  const [symbolFilter, setSymbolFilter] = useState('')
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)
  const [statusLoading, setStatusLoading] = useState(false)
  const [runMode, setRunMode] = useState('')
  const [error, setError] = useState('')
  const [statusError, setStatusError] = useState('')
  const [runNotice, setRunNotice] = useState('')

  const loadStatus = useCallback(async () => {
    setStatusLoading(true)
    setStatusError('')
    try {
      const res = await fetch(`${API_BASE}/agent/stop-reduce/training-status?user_id=1`)
      const json = await res.json()
      if (!res.ok || json.status !== 'success') {
        throw new Error(json?.detail || 'AI 训练状态加载失败')
      }
      setTrainingStatus(json.data)
    } catch (err) {
      setStatusError(err?.message || 'AI 训练状态加载失败')
    } finally {
      setStatusLoading(false)
    }
  }, [])

  const loadReport = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams({ user_id: '1', limit: '80' })
      if (symbolFilter) params.set('symbol', symbolFilter)
      const res = await fetch(`${API_BASE}/agent/stop-reduce/training-report?${params.toString()}`)
      const json = await res.json()
      if (!res.ok || json.status !== 'success') {
        throw new Error(json?.detail || 'AI 训练报告加载失败')
      }
      setReport(json.data)
    } catch (err) {
      setError(err?.message || 'AI 训练报告加载失败')
    } finally {
      setLoading(false)
    }
  }, [symbolFilter])

  useEffect(() => {
    loadReport()
  }, [loadReport])

  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  const runTraining = async (mode) => {
    setRunMode(mode)
    setRunNotice('')
    setStatusError('')
    try {
      const res = await fetch(`${API_BASE}/agent/stop-reduce/run-daily`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: 1,
          symbol: symbolFilter || null,
          mode,
          limit: 20,
          settlement_limit: 5,
        }),
      })
      const json = await res.json()
      if (!res.ok || json.status !== 'success') {
        throw new Error(json?.detail || 'AI 训练触发失败')
      }
      const summary = json.data?.summary || {}
      setRunNotice(`完成 ${modeLabel(mode)}：计划 ${summary.plans_saved ?? 0} / 意图 ${summary.intents_enqueued ?? 0} / 结算 ${summary.intents_settled ?? 0}`)
      await Promise.all([loadStatus(), loadReport()])
    } catch (err) {
      setStatusError(err?.message || 'AI 训练触发失败')
    } finally {
      setRunMode('')
    }
  }

  const applyFilter = (event) => {
    event.preventDefault()
    const nextSymbol = symbolInput.trim()
    if (nextSymbol === symbolFilter) {
      loadReport()
      return
    }
    setSymbolFilter(nextSymbol)
  }

  const intents = useMemo(() => {
    const rows = report?.intents || []
    if (!status) return rows
    return rows.filter((item) => item.settlement_status === status)
  }, [report, status])

  const overview = report?.overview || {}
  const empty = !loading && !error && report && !overview.plans && !overview.intents

  return (
    <div className="ai-training-panel">
      <header className="ai-training-toolbar">
        <div>
          <h3>AI 训练报告</h3>
          <p>跟踪每日持仓计划、影子调仓、结算评分和错误记忆。</p>
        </div>
        <form className="ai-training-actions" onSubmit={applyFilter}>
          <input
            value={symbolInput}
            onChange={(event) => setSymbolInput(event.target.value)}
            placeholder="股票代码"
            aria-label="股票代码筛选"
          />
          <button type="submit" disabled={loading}>
            {loading ? '刷新中' : '刷新'}
          </button>
        </form>
      </header>

      <div className="ai-training-disclaimer">
        {report?.disclaimer || '仅供参考，不构成投资建议'}
      </div>

      <TrainingControl
        status={trainingStatus}
        loading={statusLoading}
        running={runMode}
        error={statusError}
        notice={runNotice}
        onRefresh={loadStatus}
        onRun={runTraining}
      />

      <div className="ai-training-desktop-notice">
        请在桌面端查看 AI 训练报告，以保证计划、评分和机器明细可以完整扫读。
      </div>

      {error && (
        <div className="ai-training-error">
          <span>{error}</span>
          <button type="button" onClick={loadReport}>重试</button>
        </div>
      )}

      {loading && !report && <SkeletonReport />}

      {empty && (
        <section className="ai-training-empty">
          <strong>还没有训练样本</strong>
          <span>等待 daily runner 生成持仓计划后，这里会显示影子调仓和复盘评分。</span>
        </section>
      )}

      {report && !error && (
        <>
          <OverviewStrip overview={overview} />

          <div className="ai-training-filters">
            <div className="segmented-control" role="tablist" aria-label="AI 训练结算状态">
              {STATUS_OPTIONS.map((item) => (
                <button
                  key={item.id || 'all'}
                  type="button"
                  className={status === item.id ? 'is-active' : ''}
                  onClick={() => setStatus(item.id)}
                  aria-selected={status === item.id}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <span>{intents.length} 条意图</span>
          </div>

          <main className="ai-training-layout">
            <section className="ai-training-mainline" aria-label="计划和影子意图">
              <SectionTitle label="计划与影子意图" value={`${report.plans?.length || 0} / ${intents.length}`} />
              <PlanTimeline plans={report.plans || []} intents={intents} />
            </section>

            <aside className="ai-training-side" aria-label="评分和记忆">
              <ScorePanel intents={intents} />
              <MemoryPanel cases={report.case_memory || []} calibration={report.calibration || []} />
            </aside>
          </main>

          <MachineRows intents={intents} />
        </>
      )}
    </div>
  )
}

function TrainingControl({ status, loading, running, error, notice, onRefresh, onRun }) {
  const todayRun = status?.today_run
  const latestRun = status?.latest_run
  const counts = status?.today_counts || {}
  const scheduler = status?.scheduler || {}
  return (
    <section className="ai-training-control" aria-label="AI 训练控制">
      <div className="ai-training-control-status">
        <div>
          <span>今日状态</span>
          <strong>{loading ? '同步中' : todayRun ? runStatusLabel(todayRun.status) : '未运行'}</strong>
        </div>
        <div>
          <span>最近运行</span>
          <strong>{latestRun ? `${latestRun.trigger} · ${runStatusLabel(latestRun.status)}` : '--'}</strong>
        </div>
        <div>
          <span>今日产物</span>
          <strong>{counts.plans ?? 0}/{counts.intents ?? 0}/{counts.scores ?? 0}</strong>
        </div>
        <div>
          <span>自动窗口</span>
          <strong>{scheduler.enabled ? `${scheduler.start}-${scheduler.end}` : '关闭'}</strong>
        </div>
      </div>
      <div className="ai-training-control-actions">
        <button type="button" onClick={() => onRun('FULL')} disabled={!!running}>
          {running === 'FULL' ? '训练中' : '立即训练'}
        </button>
        <button type="button" onClick={() => onRun('SETTLEMENT')} disabled={!!running}>
          {running === 'SETTLEMENT' ? '结算中' : '只结算'}
        </button>
        <button type="button" onClick={onRefresh} disabled={loading || !!running}>
          状态刷新
        </button>
      </div>
      {(error || notice) && (
        <div className={`ai-training-control-message ${error ? 'is-error' : ''}`}>
          {error || notice}
        </div>
      )}
    </section>
  )
}

function OverviewStrip({ overview }) {
  return (
    <section className="ai-training-overview">
      <Metric label="计划" value={overview.plans ?? 0} />
      <Metric label="警戒计划" value={overview.alert_plans ?? 0} tone={overview.alert_plans ? 'warn' : ''} />
      <Metric label="影子意图" value={overview.intents ?? 0} />
      <Metric label="已评分" value={overview.settled ?? 0} tone="clear" />
      <Metric label="等待结算" value={overview.waiting ?? 0} tone={overview.waiting ? 'wait' : ''} />
      <Metric label="错误记忆" value={overview.case_memory_writes ?? 0} tone={overview.case_memory_writes ? 'risk' : ''} />
      <Metric label="均分" value={formatNumber(overview.avg_final_score)} />
    </section>
  )
}

function Metric({ label, value, tone = '' }) {
  return (
    <div className={`ai-training-metric ${tone ? `ai-training-metric--${tone}` : ''}`}>
      <span>{label}</span>
      <strong>{value ?? '--'}</strong>
    </div>
  )
}

function SectionTitle({ label, value }) {
  return (
    <div className="ai-training-section-title">
      <strong>{label}</strong>
      <span>{value}</span>
    </div>
  )
}

function PlanTimeline({ plans, intents }) {
  if (!plans.length && !intents.length) {
    return <div className="ai-training-muted">暂无计划或影子意图。</div>
  }
  return (
    <div className="ai-training-timeline">
      {plans.map((plan) => (
        <article key={plan.plan_id} className={`ai-training-plan ai-training-plan--${plan.plan_status?.toLowerCase()}`}>
          <div>
            <span>{plan.trade_date} · {plan.symbol}</span>
            <strong>{statusLabel(plan.plan_status)}</strong>
          </div>
          <p>{plan.current_script || '暂无计划说明。'}</p>
          <div className="ai-training-row-meta">
            <span>目标 {formatPct(plan.target_weight_pct)}</span>
            <span>上限 {formatPct(plan.max_position_pct)}</span>
            <span>防守 {formatPrice(plan.defense_line)}</span>
            <span>修复 {formatPrice(plan.repair_line)}</span>
          </div>
          {!!plan.observation_focus?.length && (
            <ul>
              {plan.observation_focus.slice(0, 3).map((item) => <li key={item}>{item}</li>)}
            </ul>
          )}
        </article>
      ))}

      {intents.map((intent) => (
        <article key={intent.intent_id} className={`ai-training-intent ai-training-intent--${intent.settlement_status?.toLowerCase()}`}>
          <div>
            <span>{formatDateTime(intent.as_of)} · {intent.symbol}</span>
            <strong>{actionLabel(intent.action)} → {intent.settlement_status}</strong>
          </div>
          <p>{intent.reason?.technical || intent.reason?.fundamental || '暂无结构化理由。'}</p>
          <div className="ai-training-row-meta">
            <span>当前 {formatPct(intent.current_weight_pct)}</span>
            <span>目标 {formatPct(intent.target_weight_pct)}</span>
            <span>{intent.quantity_policy || 'policy --'}</span>
          </div>
          {intent.score && (
            <div className="ai-training-score-inline">
              <span>Final {intent.score.final_score}</span>
              <span>{intent.score.settlement_window}</span>
              <span>{(intent.score.tags || []).join(' / ') || 'NO_TAG'}</span>
            </div>
          )}
        </article>
      ))}
    </div>
  )
}

function ScorePanel({ intents }) {
  const settled = intents.filter((item) => item.score)
  return (
    <section className="ai-training-panel-box">
      <SectionTitle label="结算评分" value={settled.length} />
      {!settled.length && <div className="ai-training-muted">暂无已评分意图，等待 T+N 收盘价。</div>}
      {settled.slice(0, 4).map((intent) => (
        <div key={intent.intent_id} className="ai-training-score-row">
          <div>
            <strong>{intent.symbol}</strong>
            <span>{intent.score.notes || '暂无评分说明。'}</span>
          </div>
          <b>{intent.score.final_score}</b>
        </div>
      ))}
    </section>
  )
}

function MemoryPanel({ cases, calibration }) {
  return (
    <section className="ai-training-panel-box">
      <SectionTitle label="错误记忆 / 校准" value={`${cases.length} / ${calibration.length}`} />
      {!cases.length && !calibration.length && (
        <div className="ai-training-muted">暂无高价值错误写入，Case Memory 保持稀疏。</div>
      )}
      {cases.slice(0, 3).map((item) => (
        <div key={item.case_id} className="ai-training-memory-row">
          <span>{item.case_key}</span>
          <strong>{item.mistake_type}</strong>
          <p>{item.lesson || item.outcome}</p>
        </div>
      ))}
      {calibration.slice(0, 3).map((item) => (
        <div key={item.calibration_key} className="ai-training-calibration-row">
          <span>{item.calibration_key}</span>
          <strong>{item.mistake_count}/{item.total_count} 错误</strong>
        </div>
      ))}
    </section>
  )
}

function MachineRows({ intents }) {
  return (
    <section className="ai-training-machine">
      <SectionTitle label="机器明细" value={intents.length} />
      <div className="ai-training-table" role="table" aria-label="AI 训练机器明细">
        <div className="ai-training-table-head" role="row">
          <span>Symbol</span>
          <span>Action</span>
          <span>Status</span>
          <span>Score</span>
          <span>Intent</span>
        </div>
        {intents.map((intent) => (
          <div className="ai-training-table-row" role="row" key={intent.intent_id}>
            <span>{intent.symbol}</span>
            <span>{intent.action}</span>
            <span>{intent.settlement_status}</span>
            <span>{intent.score?.final_score ?? '--'}</span>
            <span title={intent.intent_id}>{intent.intent_id}</span>
          </div>
        ))}
        {!intents.length && <div className="ai-training-muted">暂无机器明细。</div>}
      </div>
    </section>
  )
}

function SkeletonReport() {
  return (
    <div className="ai-training-skeleton" aria-label="AI 训练报告加载中">
      {Array.from({ length: 6 }).map((_, index) => <span key={index} />)}
    </div>
  )
}

function statusLabel(status) {
  return {
    HOLD: '维持',
    WATCH: '观察',
    REDUCE_ALERT: '减仓警戒',
    EXIT_ALERT: '退出警戒',
  }[status] || status || '--'
}

function actionLabel(action) {
  return {
    HOLD: '持有',
    WATCH_EXIT: '观察退出',
    REDUCE: '减仓',
    EXIT: '退出',
  }[action] || action || '--'
}

function runStatusLabel(status) {
  return {
    RUNNING: '运行中',
    SUCCESS: '成功',
    FAILED: '失败',
  }[status] || status || '--'
}

function modeLabel(mode) {
  return {
    FULL: '训练',
    SETTLEMENT: '结算',
    MONITOR: '计划',
  }[mode] || mode
}

function formatNumber(value) {
  if (value === null || value === undefined) return '--'
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(1).replace(/\\.0$/, '') : '--'
}

function formatPct(value) {
  if (value === null || value === undefined) return '--'
  const number = Number(value)
  return Number.isFinite(number) ? `${number.toFixed(1).replace(/\\.0$/, '')}%` : '--'
}

function formatPrice(value) {
  if (value === null || value === undefined || Number(value) <= 0) return '--'
  return Number(value).toFixed(2)
}

function formatDateTime(value) {
  return String(value || '').replace('T', ' ').slice(0, 16) || '--'
}
