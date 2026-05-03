import { useCallback, useEffect, useMemo, useState } from 'react'
import { API_BASE } from '../config.js'

const HYPOTHESIS_OPTIONS = [
  { id: 'A', label: 'A 向上确认' },
  { id: 'B', label: 'B 区间观察' },
  { id: 'C', label: 'C 转弱失效' },
  { id: 'D', label: 'D 停止推演' },
  { id: 'UNKNOWN', label: '未知' },
]

const STATUS_OPTIONS = [
  { id: '', label: '全部样本' },
  { id: 'PENDING', label: '待复盘' },
  { id: 'REVIEWED', label: '已复盘' },
]

export default function AIReviewPanel() {
  const [runs, setRuns] = useState([])
  const [summary, setSummary] = useState(null)
  const [status, setStatus] = useState('PENDING')
  const [symbol, setSymbol] = useState('')
  const [loading, setLoading] = useState(false)
  const [settling, setSettling] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  const loadData = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams({ user_id: '1', limit: '50' })
      if (status) params.set('replay_status', status)
      if (symbol.trim()) params.set('symbol', symbol.trim())

      const [runsRes, summaryRes] = await Promise.all([
        fetch(`${API_BASE}/agent/ai-native-radar/runs?${params.toString()}`),
        fetch(`${API_BASE}/agent/ai-native-radar/observation-summary?user_id=1`),
      ])
      const runsJson = await runsRes.json()
      const summaryJson = await summaryRes.json()
      if (!runsRes.ok || runsJson.status !== 'success') {
        throw new Error(runsJson?.detail || 'AI 推演样本加载失败')
      }
      if (!summaryRes.ok || summaryJson.status !== 'success') {
        throw new Error(summaryJson?.detail || 'AI 观察汇总加载失败')
      }
      setRuns(runsJson.data || [])
      setSummary(summaryJson.data || null)
    } catch (err) {
      setError(err?.message || 'AI 复盘加载失败')
    } finally {
      setLoading(false)
    }
  }, [status, symbol])

  useEffect(() => {
    loadData()
  }, [loadData])

  const pendingCount = useMemo(
    () => runs.filter((run) => run.replay_status === 'PENDING').length,
    [runs]
  )

  const autoSettle = async () => {
    setSettling(true)
    setNotice('')
    setError('')
    try {
      const res = await fetch(`${API_BASE}/agent/ai-native-radar/auto-settle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: 1, limit: 20, force: true }),
      })
      const json = await res.json()
      if (!res.ok || json.status !== 'success') {
        throw new Error(json?.detail || 'AI 自动结算失败')
      }
      const data = json.data || {}
      setNotice(`自动结算 ${data.settled || 0}/${data.checked || 0} 条，失败 ${data.failed?.length || 0} 条`)
      await loadData()
    } catch (err) {
      setError(err?.message || 'AI 自动结算失败')
    } finally {
      setSettling(false)
    }
  }

  return (
    <div className="ai-review-panel">
      <header className="ai-review-toolbar">
        <div>
          <h3>AI Native 推演复盘</h3>
          <p>把推演样本结算成学习样本，让评分系统知道哪里看对、哪里看偏。</p>
        </div>
        <div className="ai-review-actions">
          <button type="button" onClick={autoSettle} disabled={settling}>
            {settling ? '结算中' : '自动结算'}
          </button>
          <button type="button" onClick={loadData} disabled={loading}>
            {loading ? '刷新中' : '刷新'}
          </button>
        </div>
      </header>

      <SummaryStrip summary={summary} pendingCount={pendingCount} />

      <div className="ai-review-filters">
        <div className="segmented-control" role="tablist" aria-label="AI 复盘状态">
          {STATUS_OPTIONS.map((item) => (
            <button
              key={item.id || 'all'}
              type="button"
              className={status === item.id ? 'is-active' : ''}
              onClick={() => setStatus(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <input
          value={symbol}
          onChange={(event) => setSymbol(event.target.value)}
          placeholder="股票代码筛选"
        />
      </div>

      {notice && <div className="ai-review-notice">{notice}</div>}
      {error && <div className="ai-review-error">{error}</div>}

      <div className="ai-review-list">
        {loading && !runs.length && <div className="ai-review-empty">正在加载 AI 推演样本...</div>}
        {!loading && !runs.length && <div className="ai-review-empty">暂无符合条件的 AI 推演样本。</div>}
        {runs.map((run) => (
          <RunReviewCard key={run.id} run={run} onReviewed={loadData} />
        ))}
      </div>
    </div>
  )
}

function SummaryStrip({ summary, pendingCount }) {
  const reviewed = summary?.reviewed_runs ?? 0
  const total = summary?.total_runs ?? 0
  return (
    <section className="ai-review-summary">
      <Metric label="总样本" value={total} />
      <Metric label="待复盘" value={pendingCount} />
      <Metric label="已复盘" value={reviewed} />
      <Metric label="均分" value={formatNumber(summary?.avg_replay_score)} />
      <Metric label="Fallback" value={formatPercent(summary?.fallback_rate)} />
      <div className={`ai-review-readiness ${summary?.ready_for_ui_beta ? 'is-ready' : ''}`}>
        <span>{summary?.ready_for_ui_beta ? '稳定可用' : '继续校准'}</span>
        <strong>{summary?.readiness_reason || '等待样本积累'}</strong>
      </div>
    </section>
  )
}

function Metric({ label, value }) {
  return (
    <div className="ai-review-metric">
      <span>{label}</span>
      <strong>{value ?? '--'}</strong>
    </div>
  )
}

function RunReviewCard({ run, onReviewed }) {
  const [actual, setActual] = useState(run.current_hypothesis || 'UNKNOWN')
  const [quality, setQuality] = useState(8)
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const reviewed = run.replay_status === 'REVIEWED'
  const outcome = run.outcome || {}
  const route = run.model_route || {}

  const submitReview = async () => {
    setSaving(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/agent/ai-native-radar/runs/${run.id}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: 1,
          actual_hypothesis: actual,
          quality_score: Number(quality),
          notes,
          outcome_path: actual,
          reviewer: 'human',
        }),
      })
      const json = await res.json()
      if (!res.ok || json.status !== 'success') {
        throw new Error(json?.detail || '复盘保存失败')
      }
      setNotes('')
      await onReviewed()
    } catch (err) {
      setError(err?.message || '复盘保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <article className={`ai-review-card ai-review-card--${String(run.gate_status || 'UNKNOWN').toLowerCase()}`}>
      <div className="ai-review-card-head">
        <div>
          <span>{run.symbol} · RUN {run.id}</span>
          <strong>{hypothesisLabel(run.current_hypothesis)} · {run.gate_status}</strong>
        </div>
        <div className="ai-review-badges">
          <em>{run.replay_status}</em>
          <em>{run.gate_score ?? '--'} 分</em>
          {route.tier && <em>{modelTierLabel(route.tier)}</em>}
        </div>
      </div>

      <p className="ai-review-diagnosis">{run.diagnosis || '暂无诊断摘要。'}</p>

      <div className="ai-review-meta">
        <span>{formatTime(run.created_at)}</span>
        <span>{run.model_name || 'model unknown'}</span>
        {run.violation_codes?.length ? <span>{run.violation_codes.join(' / ')}</span> : <span>无门禁违规</span>}
      </div>

      {reviewed ? (
        <div className="ai-review-outcome">
          <span>复盘结果</span>
          <strong>{hypothesisLabel(outcome.actual_hypothesis)} · {outcome.matched === false ? '未兑现' : '兑现/可接受'}</strong>
          <em>{run.replay_score ?? '--'} 分</em>
          {outcome.sample_quality && <p>样本质量 {outcome.sample_quality} · 权重 {outcome.learning_weight ?? '--'} · {outcome.sample_quality_reason}</p>}
          {outcome.notes && <p>{outcome.notes}</p>}
        </div>
      ) : (
        <div className="ai-review-form">
          <label>
            实际路径
            <select value={actual} onChange={(event) => setActual(event.target.value)}>
              {HYPOTHESIS_OPTIONS.map((item) => (
                <option key={item.id} value={item.id}>{item.label}</option>
              ))}
            </select>
          </label>
          <label>
            样本质量
            <input
              type="number"
              min="0"
              max="10"
              value={quality}
              onChange={(event) => setQuality(event.target.value)}
            />
          </label>
          <label className="ai-review-notes">
            复盘备注
            <input
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              placeholder="例如：背了又背、压力位未突破、结构数据不足"
            />
          </label>
          <button type="button" onClick={submitReview} disabled={saving}>
            {saving ? '保存中' : '保存复盘'}
          </button>
        </div>
      )}
      {error && <div className="ai-review-card-error">{error}</div>}
    </article>
  )
}

function hypothesisLabel(value) {
  return {
    A: 'A 向上确认',
    B: 'B 区间观察',
    C: 'C 转弱失效',
    D: 'D 停止推演',
    UNKNOWN: '未知',
  }[value] || value || '未知'
}

function modelTierLabel(tier) {
  return {
    simple: 'Flash',
    hard: 'Pro High',
    calibration: 'Pro Max',
  }[tier] || tier
}

function formatNumber(value) {
  const num = Number(value)
  return Number.isFinite(num) ? num.toFixed(1) : '--'
}

function formatPercent(value) {
  const num = Number(value)
  return Number.isFinite(num) ? `${(num * 100).toFixed(0)}%` : '--'
}

function formatTime(value) {
  if (!value) return '--'
  return String(value).replace('T', ' ').slice(0, 16)
}
