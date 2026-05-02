import { useState } from 'react'
import AINativeRadarCard from './AINativeRadarCard.jsx'
import { formatPrice } from './radarAdapter.js'
import { useRadarData } from './useRadarData.js'
import './RadarPanel.css'

const SCENARIO_TONE = {
  A: 'confirm',
  B: 'maintain',
  C: 'invalidate',
}

export default function RadarPanel({ symbol, refreshToken = 0 }) {
  const { radar, loading, error, refresh } = useRadarData(symbol, refreshToken)
  const [activeTab, setActiveTab] = useState('ai')

  if (loading && !radar) return <RadarShell><RadarSkeleton /></RadarShell>
  if (error && !radar) {
    return (
      <RadarShell>
        <div className="radar-error">
          <strong>雷达加载失败</strong>
          <span>{error}</span>
          <button onClick={refresh}>重试</button>
        </div>
      </RadarShell>
    )
  }
  if (!radar) {
    return (
      <RadarShell>
        <div className="radar-empty">等待雷达数据</div>
      </RadarShell>
    )
  }

  return (
    <RadarShell>
      <RadarHeader radar={radar} onRefresh={refresh} />
      <DataHealthStrip items={radar.dataHealth} />
      {error && <div className="radar-inline-warning">{error}</div>}
      <div className="radar-tabs" role="tablist" aria-label="雷达视图">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'ai'}
          className={activeTab === 'ai' ? 'is-active' : ''}
          onClick={() => setActiveTab('ai')}
        >
          AI 推演
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'structure'}
          className={activeTab === 'structure' ? 'is-active' : ''}
          onClick={() => setActiveTab('structure')}
        >
          结构雷达
        </button>
      </div>
      {activeTab === 'ai' ? (
        <AINativeRadarCard symbol={symbol} mode={radar.mode} />
      ) : (
        <>
          <PositionCoachCard coach={radar.coachAction} context={radar.positionContext} />
          <RadarSummary radar={radar} />
          <ScenarioGrid scenarios={radar.scenarios} currentId={radar.raw.currentScenarioId} />
          <ConfirmationPanel confirmation={radar.confirmation} />
          <PatternPanel patterns={radar.patterns} transition={radar.transition} />
          <TriggerPlaybook items={radar.triggerPlaybook} fallback={radar.nextWatch} />
          <KeyObservationPanel observations={radar.keyObservations} />
          <StructureFacts centerNesting={radar.centerNesting} atoms={radar.atoms} />
          <LevelAtomStrip atoms={radar.atoms} />
          <DataNotes radar={radar} />
        </>
      )}
    </RadarShell>
  )
}

function RadarShell({ children }) {
  return (
    <section className="radar-panel" aria-label="走势推演雷达">
      {children}
    </section>
  )
}

function RadarHeader({ radar, onRefresh }) {
  const freshness = radar.dataNotes || {}
  return (
    <header className="radar-header">
      <div>
        <div className="radar-kicker">走势推演</div>
        <div className="radar-symbol">
          <span>{radar.symbol}</span>
          <small>{radar.mode}</small>
        </div>
      </div>
      <div className="radar-header-actions">
        <span className={`radar-freshness ${freshness.is_stale ? 'is-stale' : ''}`}>
          {freshness.is_stale ? '数据过期' : '结构有效'}
        </span>
        <button type="button" className="radar-icon-button" onClick={onRefresh} title="刷新雷达">
          ↻
        </button>
      </div>
    </header>
  )
}

function PositionCoachCard({ coach, context }) {
  if (!coach?.summary) return null
  const pnl = context?.pnlPct
  return (
    <section className={`radar-position-coach radar-position-coach--${coach.tone}`}>
      <div className="radar-position-coach-head">
        <div>
          <span>持仓联动</span>
          <strong>{coach.label || '观察'}</strong>
        </div>
        <div className="radar-position-badges">
          <em>{context?.label || coach.positionLabel || '空仓'}</em>
          {pnl !== null && pnl !== undefined && Number.isFinite(Number(pnl)) && (
            <em className={Number(pnl) >= 0 ? 'is-profit' : 'is-loss'}>{Number(pnl).toFixed(2)}%</em>
          )}
        </div>
      </div>
      <p>{coach.summary}</p>
      <div className="radar-position-focus">{coach.focus}</div>
      <PositionPriceNote context={context} />
      <NearestRiskLine line={coach.nearestRiskLine} />
      <CoachBoundaryRow boundaries={coach.boundaries} />
    </section>
  )
}

function NearestRiskLine({ line }) {
  if (!line?.value) return null
  const distance = Number(line.distance_pct)
  const distanceText = Number.isFinite(distance) ? `${distance.toFixed(2)}%` : '--'
  return (
    <div className="radar-nearest-risk-line">
      <span>最近风险线</span>
      <strong>{line.label} {formatPrice(line.value)}</strong>
      <em>距离 {distanceText}</em>
    </div>
  )
}

function PositionPriceNote({ context }) {
  if (!context?.priceSource || context.priceSource === 'structure') return null
  return (
    <>
      <div className="radar-position-price-note">
        实时价 {formatPrice(context.currentPrice)}
        {context.quoteTime ? ` · ${context.quoteTime}` : ''}
        {context.structurePrice ? ` · 结构价 ${formatPrice(context.structurePrice)}` : ''}
      </div>
      {context.isRealtimeDesynced && (
        <div className="radar-position-desync-note">
          {context.realtimeNote || '实时价与正式结构价偏离，主推演仍按已闭合K线切片判定。'}
        </div>
      )}
    </>
  )
}

function CoachBoundaryRow({ boundaries }) {
  const list = (boundaries || []).slice(0, 3)
  if (!list.length) return null
  return (
    <div className="radar-position-boundaries">
      {list.map((item, index) => (
        <span key={`${item.type}-${index}`}>
          {item.label} {item.level}{item.field} {formatPrice(item.value)}
        </span>
      ))}
    </div>
  )
}

function RadarSummary({ radar }) {
  const tone = toneFromRisk(radar.raw.riskLevel)
  return (
    <div className={`radar-summary radar-summary--${tone}`}>
      <div className="radar-summary-top">
        <div>
          <div className="radar-summary-label">当前主推演</div>
          <h2>{radar.summary}</h2>
        </div>
        <div className="radar-risk-stack">
          <MetricPill label="风险" value={radar.labels.risk} tone={tone} />
          <MetricPill label="动作" value={radar.labels.action} tone="gold" />
        </div>
      </div>
      <div className="radar-meta-row">
        <span>{radar.labels.path}</span>
        <span>{radar.labels.phase}</span>
        <span>{radar.labels.confidence}</span>
      </div>
      {radar.raw.intradayOverlay?.is_provisional && (
        <div className="radar-intraday-overlay-note">
          盘中价 {formatPrice(radar.raw.intradayOverlay.price)} 已临时重判为 {radar.raw.intradayOverlay.scenario_id} 路径，等待分钟K线闭合确认。
        </div>
      )}
    </div>
  )
}

function DataHealthStrip({ items }) {
  const list = (items || []).slice(0, 5)
  if (!list.length) return null
  return (
    <div className="radar-data-health" aria-label="雷达数据健康">
      <span className="radar-data-health-label">数据</span>
      {list.map((item) => (
        <div key={item.level} className={`radar-data-health-item ${item.isStale ? 'is-stale' : ''}`}>
          <span>{item.level}</span>
          <strong>{compactTime(item.lastBarAt)}</strong>
        </div>
      ))}
    </div>
  )
}

function ConfirmationPanel({ confirmation }) {
  if (!confirmation) return null
  const progress = Math.max(0, Math.min(100, Math.round((confirmation.progress || 0) * 100)))
  return (
    <CollapsibleSection title="执行确认" meta={confirmation.label}>
      <div className={`radar-confirmation radar-confirmation--${confirmation.tone}`}>
        <div className="radar-confirmation-head">
          <strong>{confirmation.label}</strong>
          <span>{progress}%</span>
        </div>
        <div className="radar-progress-track" aria-label={`确认进度 ${progress}%`}>
          <div className="radar-progress-fill" style={{ width: `${progress}%` }} />
        </div>
        <p>{confirmation.meaning || '等待结构边界触发。'}</p>
        <div className="radar-confirmation-grid">
          <MiniBoundaryList label="已触发" items={confirmation.matched} />
          <MiniBoundaryList label="待确认" items={confirmation.unmatched} />
        </div>
      </div>
    </CollapsibleSection>
  )
}

function MiniBoundaryList({ label, items }) {
  const list = (items || []).slice(0, 3)
  return (
    <div className="radar-mini-boundaries">
      <span>{label}</span>
      {list.length > 0 ? list.map((item, index) => (
        <strong key={`${label}-${index}`}>{item.level}{item.field} {formatPrice(item.value)}</strong>
      )) : <em>无</em>}
    </div>
  )
}

function PatternPanel({ patterns, transition }) {
  const primary = patterns?.[0]
  const hasTransition = transition && transition.status && transition.status !== 'UNCHANGED'
  if (!primary && !hasTransition) return null
  return (
    <CollapsibleSection title="结构模板" meta={primary?.confidence || transition?.status || ''}>
      <div className="radar-pattern-card">
        {primary && (
          <>
            <div className="radar-pattern-head">
              <strong>{primary.name}</strong>
              <span>{primary.code}</span>
            </div>
            <div className="radar-pattern-evidence">
              {(primary.evidence || []).slice(0, 4).map((item, index) => (
                <div key={`${primary.code}-${index}`}>
                  <span>{item.level_role || item.level}{item.field}</span>
                  <strong>{formatEvidenceValue(item.value)}</strong>
                  <em>{item.meaning}</em>
                </div>
              ))}
            </div>
          </>
        )}
        {hasTransition && (
          <div className="radar-transition">
            <span>{transition.from || 'UNKNOWN'} → {transition.to || 'UNKNOWN'}</span>
            <strong>{transition.status}</strong>
            <p>{transition.meaning}</p>
          </div>
        )}
      </div>
    </CollapsibleSection>
  )
}

function TriggerPlaybook({ items, fallback }) {
  const list = items?.length
    ? items
    : (fallback || []).map((item, index) => ({
        id: `fallback-${index}`,
        path: '',
        title: '继续观察',
        tone: 'neutral',
        condition: item,
        then: '',
        boundary: {},
      }))

  return (
    <CollapsibleSection title="接下来如果发生" meta={`${list?.length || 0} 条`}>
      <div className="radar-trigger-list">
        {(list || []).slice(0, 5).map((item, index) => (
          <article key={item.id || `${item.condition}-${index}`} className={`radar-trigger-card radar-trigger-card--${item.tone}`}>
            <div className="radar-trigger-index">{index + 1}.</div>
            <div className="radar-trigger-body">
              <div className="radar-trigger-head">
                <strong>{item.condition}</strong>
                {item.path && <span>进入 {item.path}</span>}
              </div>
              <p>{item.then || item.title}</p>
              {item.boundary?.source_label && <em>{item.boundary.source_label}</em>}
            </div>
          </article>
        ))}
        {(!list || list.length === 0) && <div className="radar-muted-row">暂无明确触发条件</div>}
      </div>
    </CollapsibleSection>
  )
}

function KeyObservationPanel({ observations }) {
  const list = observations || []
  if (!list.length) return null
  return (
    <CollapsibleSection title="关键观察位" meta={`${list.length} 条`}>
      <div className="radar-observation-list">
        {list.slice(0, 4).map((item) => (
          <article key={item.id} className={`radar-observation radar-observation--${item.tone}`}>
            <div className="radar-observation-main">
              <span>{item.label}</span>
              <strong>{item.value}</strong>
            </div>
            <p>{item.meaning}</p>
            {(item.source || item.time) && (
              <em>{[item.source, item.time].filter(Boolean).join(' · ')}</em>
            )}
          </article>
        ))}
      </div>
    </CollapsibleSection>
  )
}

function ScenarioGrid({ scenarios, currentId }) {
  return (
    <section className="radar-section">
      <SectionTitle title="A/B/C 完全分类" meta={`当前 ${currentId || 'B'}`} />
      <div className="radar-scenario-grid">
        {(scenarios || []).map((scenario) => (
          <article
            key={scenario.id}
            className={`radar-scenario radar-scenario--${SCENARIO_TONE[scenario.id] || 'neutral'} ${scenario.id === currentId ? 'is-current' : ''}`}
          >
            <div className="radar-scenario-head">
              <span className="radar-scenario-id">{scenario.id}</span>
              <strong>{scenario.name}</strong>
              <small>{scenario.state}</small>
            </div>
            <p>{scenario.meaning}</p>
            <ScenarioTrigger scenario={scenario} />
          </article>
        ))}
      </div>
    </section>
  )
}

function ScenarioTrigger({ scenario }) {
  const triggers = scenario.triggerIf || []
  if (!triggers.length) return null
  const [first, ...rest] = triggers
  return (
    <div className="radar-scenario-trigger">
      <span>{first}</span>
      {rest.length > 0 && (
        <details>
          <summary>{rest.length} 条备用条件</summary>
          <ul>
            {rest.slice(0, 3).map((trigger, index) => (
              <li key={`${scenario.id}-extra-${index}`}>{trigger}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

function StructureFacts({ centerNesting, atoms }) {
  return (
    <CollapsibleSection title="结构事实" meta="中枢 / 动能">
      <div className="radar-fact-stack">
        {(centerNesting || []).slice(0, 2).map(item => (
          <div key={item.key} className="radar-fact-row">
            <span>{item.parentLevel}→{item.childLevel}</span>
            <strong>{item.label}</strong>
            <em>gap {formatSigned(item.gapToParentZg)}</em>
          </div>
        ))}
        {(atoms || []).map(atom => (
          <div key={`${atom.role}-fact`} className="radar-fact-row">
            <span>{atom.roleLabel}</span>
            <strong>{atom.leaveReturn?.label || '未知'}</strong>
            <em>{momentumText(atom.momentum)}</em>
          </div>
        ))}
      </div>
    </CollapsibleSection>
  )
}

function LevelAtomStrip({ atoms }) {
  return (
    <CollapsibleSection title="级别原子" meta="L0 / L1 / L2">
      <div className="radar-atom-strip">
        {(atoms || []).map(atom => (
          <div key={atom.role} className={`radar-atom radar-atom--${atom.state?.toLowerCase?.() || 'unknown'}`}>
            <div className="radar-atom-head">
              <span>{atom.roleLabel}</span>
              <strong>{atom.level}</strong>
            </div>
            <div className="radar-atom-price">{formatPrice(atom.price)}</div>
            <div className="radar-atom-state">{atom.stateLabel}</div>
            <div className="radar-atom-center">
              {formatPrice(atom.center?.zd)} - {formatPrice(atom.center?.zg)}
            </div>
            <div className="radar-atom-extra">
              <span>{atom.leaveReturn?.label || '未知'}</span>
              <span>{momentumText(atom.momentum)}</span>
            </div>
          </div>
        ))}
      </div>
    </CollapsibleSection>
  )
}

function CollapsibleSection({ title, meta, children }) {
  return (
    <details className="radar-section radar-collapsible">
      <summary className="radar-collapsible-summary">
        <h3>{title}</h3>
        <span>{meta}</span>
      </summary>
      <div className="radar-collapsible-body">
        {children}
      </div>
    </details>
  )
}

function DataNotes({ radar }) {
  const notes = radar.dataNotes || {}
  return (
    <footer className="radar-data-notes">
      <span>{radar.structureConfig?.label || '正式结构'}</span>
      <span>{notes.source || 'unknown'}</span>
      {notes.last_bar_at && <span>{notes.last_bar_at}</span>}
      <span>{radar.disclaimer}</span>
    </footer>
  )
}

function MetricPill({ label, value, tone }) {
  return (
    <div className={`radar-pill radar-pill--${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function SectionTitle({ title, meta }) {
  return (
    <div className="radar-section-title">
      <h3>{title}</h3>
      {meta && <span>{meta}</span>}
    </div>
  )
}

function RadarSkeleton() {
  return (
    <div className="radar-skeleton-stack">
      <div className="radar-loading-note">
        <strong>结构计算中</strong>
        <span>正在读取 CChan 多级别结构，首次加载可能需要几十秒。</span>
      </div>
      <div className="radar-skeleton radar-skeleton--h32" />
      <div className="radar-skeleton radar-skeleton--h96" />
      <div className="radar-skeleton radar-skeleton--h160" />
      <div className="radar-skeleton radar-skeleton--h120" />
    </div>
  )
}

function toneFromRisk(risk) {
  if (risk === 'HIGH') return 'danger'
  if (risk === 'MEDIUM_HIGH') return 'warning'
  return 'neutral'
}

function formatEvidenceValue(value) {
  if (typeof value === 'number') return formatPrice(value)
  if (value === null || value === undefined || value === '') return '--'
  return String(value)
}

function formatSigned(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '--'
  if (num > 0) return `+${num.toFixed(2)}`
  return num.toFixed(2)
}

function compactTime(value) {
  if (!value) return '--'
  const text = String(value)
  if (text.includes(' ')) return text.slice(5, 16)
  return text.slice(5)
}

function momentumText(momentum) {
  if (!momentum || !momentum.direction) return '动能未知'
  const ratio = Number(momentum.area_ratio)
  const ratioText = Number.isFinite(ratio) && ratio > 0 ? `${Math.round(ratio * 100)}%` : '--'
  return `${momentum.label || '动能'} ${ratioText}`
}
