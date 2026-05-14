import { useCallback, useEffect, useState } from 'react'
import { API_BASE } from '../config.js'
import { apiJson } from '../api/client.js'
import { readLastViewedSymbol } from '../utils/symbolStorage.js'
import BehaviorReport from './BehaviorReport.jsx'
import './ReviewTrainingPage.css'

export default function ReviewTrainingPage({ activeSymbol, activeSymbolName }) {
  const stored = readLastViewedSymbol()
  const symbol = activeSymbol || stored.symbol
  const symbolName = activeSymbolName || stored.name

  return (
    <div className="review-training-page">
      <header className="review-training-header">
        <div>
          <h2>复盘训练</h2>
          <p>盘后复盘纪律偏差，训练计划内交易反应。</p>
        </div>
      </header>

      <section className="review-training-body review-training-body--ai-review">
        <V5OutcomeReview symbol={symbol} symbolName={symbolName} />
      </section>

      <section className="review-training-body review-training-body--behavior">
        <BehaviorReport />
      </section>
    </div>
  )
}

function V5OutcomeReview({ symbol, symbolName }) {
  const [review, setReview] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const items = review?.items || []
  const stats = review?.memory?.stats || {}
  const warnings = review?.memory?.profile?.active_warnings || []

  const loadReview = useCallback(async () => {
    if (!symbol) return
    setLoading(true)
    setError('')
    try {
      const json = await apiJson(`${API_BASE}/ai-structure/outcomes/${encodeURIComponent(symbol)}?limit=12`)
      setReview(json.data)
    } catch (err) {
      setReview(null)
      setError(err?.message || '结构复盘读取失败')
    } finally {
      setLoading(false)
    }
  }, [symbol])

  useEffect(() => {
    loadReview()
  }, [loadReview])

  return (
    <div className="ai-review-panel">
      <div className="ai-review-toolbar">
        <div>
          <h3>AI 结构复盘</h3>
          <p>{symbolName || symbol} · {symbol} 的分支结果和纪律记忆，只记录已发生的触发、失效和用户 follow-up。</p>
        </div>
        <div className="ai-review-actions">
          <button type="button" onClick={loadReview} disabled={loading || !symbol}>
            {loading ? '刷新中' : '刷新'}
          </button>
        </div>
      </div>

      <div className="ai-review-summary">
        <Metric label="复盘次数" value={stats.total_outcomes ?? items.length} />
        <Metric label="触发" value={stats.triggered ?? 0} />
        <Metric label="失效" value={stats.invalidated ?? 0} />
        <Metric label="纪律问题" value={stats.mistake_count_30d ?? 0} />
        <Metric label="执行率" value={formatRate(stats.plan_follow_rate)} />
        <div className={`ai-review-readiness ${warnings.length ? '' : 'is-ready'}`}>
          <span>当前记忆</span>
          <strong>{warnings[0]?.text || '暂无需要进入日常问答的纪律偏差记忆。'}</strong>
        </div>
      </div>

      {error && <div className="ai-review-error">{error}</div>}
      {!error && !loading && !items.length && (
        <div className="ai-review-empty">还没有结构分支复盘。等提醒触发或 outcome worker 结算后，这里会显示纪律证据。</div>
      )}

      {!!items.length && (
        <div className="ai-review-list">
          {items.map((item) => (
            <OutcomeCard key={item.outcome_id} item={item} />
          ))}
        </div>
      )}
    </div>
  )
}

function Metric({ label, value }) {
  return (
    <div className="ai-review-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function OutcomeCard({ item }) {
  const branch = item.branch || {}
  return (
    <article className={`ai-review-card ${item.is_mistake ? 'ai-review-card--fallback' : ''}`}>
      <div className="ai-review-card-head">
        <div>
          <span>{formatTime(item.checked_at)}</span>
          <strong>{outcomeLabel(item)}</strong>
        </div>
        <div className="ai-review-badges">
          <em>{branchTypeLabel(branch.branch_type)}</em>
          <em>{item.settlement_window || 'manual'}</em>
          {item.is_mistake && <em>纪律偏差</em>}
        </div>
      </div>
      <div className="ai-review-meta">
        <span>触发线 {formatPrice(item.trigger_price)}</span>
        <span>触发价 {formatPrice(item.triggered_price)}</span>
        <span>失效价 {formatPrice(item.invalidated_price)}</span>
        <span>{followedLabel(item.user_followed_plan)}</span>
      </div>
      <div className="ai-review-outcome">
        <span>复盘结论</span>
        <p>{outcomeText(item)}</p>
      </div>
    </article>
  )
}

function outcomeLabel(item) {
  if (item.is_mistake) return '结构失效后未处理'
  if (item.outcome === 'triggered') return '观察分支已触发'
  if (item.outcome === 'invalidated') return '观察分支已失效'
  if (item.outcome === 'expired') return '观察分支过期'
  return '仍在观察'
}

function outcomeText(item) {
  if (item.is_mistake) {
    return `这条分支在 ${formatPrice(item.invalidated_price || item.trigger_price)} 附近失效后没有按计划处理，已进入纪律记忆。`
  }
  if (item.outcome === 'invalidated') {
    return `分支在 ${formatPrice(item.invalidated_price || item.trigger_price)} 附近失效。只有用户确认未处理时，才会进入 mistake memory。`
  }
  if (item.outcome === 'triggered') {
    return `分支在 ${formatPrice(item.triggered_price || item.trigger_price)} 附近触发，只作为复盘证据，不代表交易指令。`
  }
  if (item.outcome === 'expired') {
    return '观察窗口到期未触发，后续需要重新等待新的结构上下文。'
  }
  return '该分支仍在观察中，等待价格触发或失效后再复盘。'
}

function branchTypeLabel(type) {
  if (type === 'observe_breakout') return '突破观察'
  if (type === 'invalidation_watch') return '失效观察'
  if (type === 'holding_defense') return '持仓防守'
  return type || '结构分支'
}

function followedLabel(value) {
  if (value === true) return '已按计划处理'
  if (value === false) return '未按计划处理'
  return '未评价执行'
}

function formatRate(value) {
  if (value === null || value === undefined) return '--'
  return `${Math.round(Number(value) * 100)}%`
}

function formatPrice(value) {
  const n = Number(value || 0)
  return n > 0 ? n.toFixed(2) : '--'
}

function formatTime(value) {
  if (!value) return ''
  return String(value).slice(0, 16).replace('T', ' ')
}
