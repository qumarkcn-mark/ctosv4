import { useEffect, useMemo, useState } from 'react'
import { API_BASE } from '../config.js'
import './RotationCompass.css'

const MODE_LABEL = {
  HOLDING: '持仓',
  CANDIDATE: '候选',
}

function price(value) {
  if (value === null || value === undefined) return '--'
  return Number(value).toFixed(2)
}

function pct(value) {
  if (value === null || value === undefined) return '--'
  return `${Number(value).toFixed(2)}%`
}

function toneFromScore(score) {
  if (score >= 75) return 'strong'
  if (score >= 55) return 'watch'
  if (score >= 35) return 'neutral'
  return 'weak'
}

function PlanList({ plans }) {
  return (
    <div className="rc-plan-list">
      {(plans || []).map((plan) => (
        <section key={plan.name} className="rc-plan">
          <div className="rc-plan-marker">{plan.name}</div>
          <div className="rc-plan-body">
            <div className="rc-plan-title">{plan.title}</div>
            <dl>
              <div>
                <dt>触发条件</dt>
                <dd>{plan.condition}</dd>
              </div>
              <div>
                <dt>结构论据</dt>
                <dd>{plan.structure_evidence}</dd>
              </div>
              <div>
                <dt>仓位动作</dt>
                <dd>{plan.position_action}</dd>
              </div>
              <div>
                <dt>雷达复核</dt>
                <dd>{plan.radar_check}</dd>
              </div>
            </dl>
          </div>
        </section>
      ))}
    </div>
  )
}

function SymbolCard({ item, expanded, onToggle, onViewInChan }) {
  const summary = item.structure_summary || {}
  const tone = toneFromScore(summary.sort_score || item.sort_score || 0)
  const isHolding = item.mode === 'HOLDING'

  return (
    <article className={`rc-card rc-card--${tone}`}>
      <button type="button" className="rc-card-main" onClick={onToggle} aria-expanded={expanded}>
        <div className="rc-symbol-block">
          <div className="rc-symbol-line">
            <span className="rc-symbol">{item.symbol}</span>
            {item.name && <span className="rc-name">{item.name}</span>}
            <span className="rc-mode">{MODE_LABEL[item.mode] || item.category || item.mode}</span>
          </div>
          <div className="rc-symbol-sub">
            {isHolding && item.quantity ? `${item.quantity}股 · 成本 ${price(item.avg_cost)}` : `现价 ${price(summary.price || item.price)}`}
          </div>
        </div>

        <div className="rc-state-block">
          <strong>{summary.state_label || item.state_label || '待定位'}</strong>
          <span>{summary.zoushi_type || item.zoushi_type || '走势待确认'}</span>
        </div>

        <div className="rc-defense-block">
          <span>防线 {price(summary.stop_loss)}</span>
          <strong>{pct(summary.distance_pct)}</strong>
        </div>

        <div className="rc-expand-indicator">{expanded ? '收起' : '预案'}</div>
      </button>

      {expanded && (
        <div className="rc-card-detail">
          {summary.error && <div className="rc-card-error">{summary.error}</div>}
          <div className="rc-node-row">
            <span>当前节点</span>
            <strong>{summary.lifecycle_node || item.lifecycle_node || '--'}</strong>
          </div>
          <PlanList plans={item.plans} />
          <div className="rc-card-actions">
            {onViewInChan && (
              <button type="button" onClick={() => onViewInChan(item.symbol, item.name || item.symbol)}>
                看盘
              </button>
            )}
          </div>
        </div>
      )}
    </article>
  )
}

function SymbolColumn({ title, items, emptyText, expanded, setExpanded, onViewInChan }) {
  return (
    <section className="rc-column">
      <div className="rc-column-head">
        <h3>{title}</h3>
        <span>{items.length}</span>
      </div>
      {items.length === 0 ? (
        <div className="rc-empty">{emptyText}</div>
      ) : (
        <div className="rc-card-stack">
          {items.map((item) => (
            <SymbolCard
              key={`${item.mode}-${item.symbol}`}
              item={item}
              expanded={expanded === `${item.mode}-${item.symbol}`}
              onToggle={() => {
                const key = `${item.mode}-${item.symbol}`
                setExpanded((current) => (current === key ? null : key))
              }}
              onViewInChan={onViewInChan}
            />
          ))}
        </div>
      )}
    </section>
  )
}

export default function RotationCompass({ onViewInChan }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [loadedAt, setLoadedAt] = useState(null)
  const [expanded, setExpanded] = useState(null)

  const load = () => {
    setLoading(true)
    setError(null)
    fetch(`${API_BASE}/rotation/compass`)
      .then((response) => {
        if (!response.ok) throw new Error('调仓罗盘加载失败')
        return response.json()
      })
      .then((json) => {
        if (json.status !== 'success') throw new Error(json.message || '接口异常')
        setData(json.data)
        setLoadedAt(new Date())
      })
      .catch((err) => setError(err.message || '加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
  }, [])

  const comparison = data?.comparison || {}
  const holdings = data?.holdings || []
  const candidates = data?.candidates || []
  const summaryCards = useMemo(() => ([
    { label: '持仓', value: comparison.holdings_count ?? holdings.length },
    { label: '候选', value: comparison.candidates_count ?? candidates.length },
    { label: '最强持仓', value: comparison.strongest_holding?.symbol || '--' },
    { label: '最强候选', value: comparison.strongest_candidate?.symbol || '--' },
  ]), [comparison, holdings.length, candidates.length])

  if (loading) {
    return (
      <div className="rc-loading">
        <div className="spinner" />
        <div>正在对照持仓与候选</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="rc-error">
        <div>加载失败：{error}</div>
        <button type="button" onClick={load}>重试</button>
      </div>
    )
  }

  if (!data) return null

  return (
    <div className="rotation-compass">
      <header className="rc-header">
        <div className="rc-title-group">
          <h2>调仓罗盘</h2>
          <div className="rc-subtitle">
            持仓与候选横向比较
            {loadedAt && (
              <span> · 更新于 {loadedAt.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
            )}
          </div>
          <div className="rc-risk-note">{data.risk_disclaimer}</div>
        </div>
        <button className="rc-refresh" type="button" onClick={load} disabled={loading}>
          重新对照
        </button>
      </header>

      <div className="rc-summary-bar">
        {summaryCards.map((card) => (
          <div key={card.label} className="summary-stat">
            <div className="stat-val">{card.value}</div>
            <div className="stat-lbl">{card.label}</div>
          </div>
        ))}
      </div>

      <div className="rc-focus">{comparison.focus || '比较结构清晰度、风险防线和触发条件。'}</div>

      <div className="rc-columns">
        <SymbolColumn
          title="现有持仓"
          items={holdings}
          emptyText="当前空仓。"
          expanded={expanded}
          setExpanded={setExpanded}
          onViewInChan={onViewInChan}
        />
        <SymbolColumn
          title="观察候选"
          items={candidates}
          emptyText="观察库暂无候选。"
          expanded={expanded}
          setExpanded={setExpanded}
          onViewInChan={onViewInChan}
        />
      </div>

      <footer className="rc-footer-note">
        分数只用于排序和强弱底色，预案只给条件与复核路径，不替用户拍板。
      </footer>
    </div>
  )
}
