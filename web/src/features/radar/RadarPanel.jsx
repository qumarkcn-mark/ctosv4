import { useEffect, useState } from 'react'
import AINativeFusionCard from './AINativeFusionCard.jsx'
import AINativeRadarCard from './AINativeRadarCard.jsx'
import { formatPrice } from './radarAdapter.js'
import { useRadarData } from './useRadarData.js'
import './RadarPanel.css'

const SCENARIO_TONE = {
  A: 'confirm',
  B: 'maintain',
  C: 'invalidate',
}

const EXPERT_MODE_STORAGE_KEY = 'ctos.expert_mode'

export default function RadarPanel({ symbol, refreshToken = 0 }) {
  const { radar, loading, error, profile, setProfile, refresh } = useRadarData(symbol, refreshToken)
  const [activeTab, setActiveTab] = useState('ai')
  const [expertMode, setExpertMode] = useState(() => readExpertModePreference())
  const [aiDeductionReport, setAiDeductionReport] = useState(null)
  const signalCode = radar?.signal?.code || ''
  const structureFingerprint = radar ? getStructureFingerprint(radar) : ''
  const structureBlocker = radar ? getStructureBlocker(radar, error) : null

  useEffect(() => {
    const syncExpertMode = (event) => {
      if (typeof event?.detail?.expertMode === 'boolean') {
        setExpertMode(event.detail.expertMode)
        return
      }
      setExpertMode(readExpertModePreference())
    }
    window.addEventListener('storage', syncExpertMode)
    window.addEventListener('ctos:expert-mode-change', syncExpertMode)
    return () => {
      window.removeEventListener('storage', syncExpertMode)
      window.removeEventListener('ctos:expert-mode-change', syncExpertMode)
    }
  }, [])

  useEffect(() => {
    setAiDeductionReport(null)
  }, [symbol, radar?.mode, signalCode])

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
      <RadarHeader
        radar={radar}
        loading={loading}
        onRefresh={refresh}
      />
      <StructureRuntimeStrip
        radar={radar}
        loading={loading}
        profile={profile}
        onProfileChange={setProfile}
      />
      <DataHealthStrip items={radar.dataHealth} />
      {(structureBlocker || error) && (
        <RadarInlineWarning warning={structureBlocker} fallback={error} />
      )}
      <div className="radar-tabs" role="tablist" aria-label="雷达视图">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'ai'}
          className={activeTab === 'ai' ? 'is-active' : ''}
          onClick={() => setActiveTab('ai')}
        >
          生成教练判断
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'structure'}
          className={activeTab === 'structure' ? 'is-active' : ''}
          onClick={() => setActiveTab('structure')}
        >
          结构详情
        </button>
      </div>
      {activeTab === 'ai' ? (
        <>
          <AINativeRadarCard
            symbol={symbol}
            mode={radar.mode}
            signalCode={signalCode}
            structureFingerprint={structureFingerprint}
            disabled={Boolean(structureBlocker)}
            disabledReason={structureBlocker?.action || ''}
            onReportChange={setAiDeductionReport}
          />
          {aiDeductionReport && !structureBlocker ? (
            <AINativeFusionCard
              symbol={symbol}
              mode={radar.mode}
              signalCode={signalCode}
              structureFingerprint={structureFingerprint}
            />
          ) : (
            <FusionLockedHint disabledReason={structureBlocker?.action || ''} />
          )}
        </>
      ) : (
        <>
          <PositionCoachCard coach={radar.coachAction} context={radar.positionContext} />
          <SignalCard signal={radar.signal} loading={loading} expertMode={expertMode} />
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

function RadarInlineWarning({ warning, fallback }) {
  if (!warning) {
    return <div className="radar-inline-warning">{fallback}</div>
  }
  return (
    <div className="radar-inline-warning">
      <strong>{warning.title}</strong>
      <span>{warning.body}</span>
    </div>
  )
}

function FusionLockedHint({ disabledReason = '' }) {
  const blocked = Boolean(disabledReason)
  return (
    <section className="radar-fusion-locked">
      <div>
        <span>综合判断</span>
        <strong>{blocked ? '等待正式结构数据' : '先完成 AI 推演'}</strong>
      </div>
      <p>
        {blocked
          ? disabledReason
          : 'AI 推演生成当前定位和路径分类后，再在这里做持仓、结构和预测参考的综合判断。'}
      </p>
    </section>
  )
}

function getStructureBlocker(radar, warningText) {
  const staleReason = String(radar?.freshness?.stale_reason || radar?.dataNotes?.stale_reason || '').toUpperCase()
  const rawWarning = String(radar?.loadWarning || warningText || '')
  const lowerWarning = rawWarning.toLowerCase()
  const noData = staleReason === 'NO_DATA'
    || lowerWarning.includes('no usable kline data')
    || lowerWarning.includes('usable kline')
  const emptyStructure = radar?.status === 'empty' && !radar?.structureKernel?.levels?.length
  if (!noData && !emptyStructure) return null
  return {
    title: '正式结构数据暂不可用',
    body: '当前没有可用于雷达推演的 BaoStock 正式 K 线。K 线图仍可查看预览数据；请先同步数据，或稍后刷新雷达。仅供参考，不构成投资建议。',
    action: '正式结构数据暂不可用，先同步数据或刷新雷达后再生成推演。',
  }
}

function getStructureFingerprint(radar) {
  return String(
    radar?.diagnostics?.structure_fingerprint
    || radar?.structureKernel?.structure_fingerprint
    || radar?.raw?.structure_fingerprint
    || ''
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

function StructureRuntimeStrip({ radar, loading, profile, onProfileChange }) {
  const diagnostics = radar?.diagnostics || {}
  const kernel = radar?.structureKernel || {}
  const requestedProfile = String(diagnostics.requested_profile || profile || 'auto').toLowerCase()
  const resolvedProfile = String(diagnostics.resolved_profile || diagnostics.structure_profile || kernel.profile || 'fast').toLowerCase()
  const profileLabel = resolvedProfile.toUpperCase()
  const levels = diagnostics.structure_levels?.length
    ? diagnostics.structure_levels
    : (kernel.levels || [])
  const source = diagnostics.structure_persistent_cache_hit
    ? '持久缓存'
    : diagnostics.structure_cache_hit
      ? '内存缓存'
      : '实时计算'
  const ms = formatMs(diagnostics.structure_ms)
  const fingerprint = String(diagnostics.structure_fingerprint || kernel.structure_fingerprint || '')
  const date = radar?.dataNotes?.last_bar_at || radar?.freshness?.last_bar_at || ''
  const upgradeReason = diagnostics.upgrade_reason

  return (
    <details className={`radar-runtime-details ${loading ? 'is-loading' : ''}`}>
      <summary>
        <span>{loading ? '刷新中' : profileLabel}</span>
        <span>{levels.length ? levels.join('/') : 'levels --'}</span>
        {date && <span>数据 {formatCompactDate(date)}</span>}
        <span>{ms}</span>
        {upgradeReason && <em>{upgradeReasonLabel(upgradeReason)}</em>}
      </summary>
      <div className="radar-runtime-strip">
        <div className="radar-profile-toggle" aria-label="结构计算档位">
          {['auto', 'fast', 'full'].map((item) => (
            <button
              key={item}
              type="button"
              className={requestedProfile === item ? 'is-active' : ''}
              onClick={() => onProfileChange(item)}
              disabled={loading && requestedProfile !== item}
              title={profileTitle(item)}
            >
              {item.toUpperCase()}
            </button>
          ))}
        </div>
        <span>请求 {requestedProfile.toUpperCase()}</span>
        <span>实际 {profileLabel}</span>
        <span>{source}</span>
        {fingerprint && <em title={fingerprint}>fp {fingerprint.slice(0, 8)}</em>}
      </div>
    </details>
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

function SignalCard({ signal, loading, expertMode = false }) {
  const state = loading && (!signal || signal.state === 'empty') ? 'loading' : (signal?.state || 'empty')
  const isStale = state === 'stale'
  const isError = state === 'error'
  const isPartial = state === 'partial' || (signal?.code && !signal?.action)

  if (state === 'loading') {
    return (
      <section className="radar-signal-card radar-signal-card--loading" aria-label="语义信号">
        <div className="radar-signal-skeleton is-wide" />
        <div className="radar-signal-skeleton" />
      </section>
    )
  }

  const displayAction = isStale
    ? '等待刷新确认'
    : isPartial
      ? '信号已识别，操作规则待确认'
      : signal?.action || '继续观察'
  const resonance = signal?.resonance || []
  const visibleResonance = resonance.slice(0, 3)
  const hiddenCount = Math.max(0, resonance.length - visibleResonance.length)
  const signalLabel = expertMode && signal?.labelExpert
    ? signal.labelExpert
    : signal?.labelPlain
  const showKronosHint = !['empty', 'stale', 'error', 'loading'].includes(state)
    && Boolean(signal?.kronosTimeline || signal?.kronosEnvelope)

  return (
    <section className={`radar-signal-card radar-signal-card--${state}`} aria-label="语义信号">
      <div className="radar-signal-head">
        <span>Signal V2</span>
        <em>{signal?.boundaryState || state}</em>
      </div>
      <strong className="radar-signal-action">{displayAction}</strong>
      <p>{isError ? (signal?.labelPlain || '语义层不可用，保留结构雷达判断') : signalLabel}</p>
      {signal?.code && (
        <button
          type="button"
          className="radar-signal-code"
          title={signal.labelExpert || signal.code}
          onClick={() => copySignalCode(signal.code)}
        >
          {signal.code}
        </button>
      )}
      <div className="radar-signal-metrics">
        <SignalMetric label="Key" value={formatPrice(signal?.keyPrice)} />
        <SignalMetric label="Stop" value={formatPrice(signal?.stopLossPrice)} tone="danger" />
        <SignalMetric label="R:R" value={formatRatio(signal?.riskRewardRatio)} tone="gold" />
      </div>
      {(visibleResonance.length > 0 || hiddenCount > 0) && (
        <div className="radar-signal-resonance" aria-label="多级别共振">
          {visibleResonance.map((item) => (
            <span key={item.code} title={item.labelExpert || item.labelPlain || item.code}>
              {item.code}
            </span>
          ))}
          {hiddenCount > 0 && <span>+{hiddenCount}</span>}
        </div>
      )}
      {showKronosHint && <KronosSignalHint timeline={signal.kronosTimeline} envelope={signal.kronosEnvelope} />}
      <small>{signal?.disclaimer || '仅供参考，不构成投资建议'}</small>
    </section>
  )
}

function KronosSignalHint({ timeline, envelope }) {
  if (!timeline && !envelope) return null
  const validationText = kronosValidationText(envelope?.validation)
  const validationTone = envelope?.validationTone || 'neutral'

  return (
    <div className={`radar-signal-kronos radar-signal-kronos--${validationTone}`} aria-label="Kronos 预测参考">
      {timeline && (
        <div className="radar-signal-kronos-row">
          <span>时间线</span>
          <strong>{kronosTimelineText(timeline)}</strong>
        </div>
      )}
      {timeline?.predictedFenxing?.price && (
        <div className="radar-signal-kronos-row">
          <span>分型候选</span>
          <strong>{kronosFenxingText(timeline.predictedFenxing)}</strong>
        </div>
      )}
      {envelope && (
        <div className="radar-signal-kronos-row">
          <span>信封</span>
          <strong>今日预测执行区间参考 {formatPrice(envelope.low)}-{formatPrice(envelope.high)}</strong>
        </div>
      )}
      {validationText && (
        <div className="radar-signal-kronos-note">{validationText}</div>
      )}
    </div>
  )
}

function kronosTimelineText(timeline) {
  const bars = timeline?.estimatedConfirmationBars
  if (Number.isFinite(Number(bars)) && Number(bars) > 0) {
    return `预测确认约 ${Number(bars)} 根`
  }
  return '预测确认窗口待补足'
}

function kronosFenxingText(fenxing) {
  const type = fenxing?.type ? `${fenxing.type} ` : ''
  return `${type}${formatPrice(fenxing?.price)}`
}

function kronosValidationText(validation) {
  if (!validation) return ''
  if (validation.startsWith('CONFLICT')) return '预测区间与执行点存在偏差，需降低参考权重'
  if (validation.startsWith('WARNING')) return '执行点接近预测区间边缘，需谨慎参考'
  return ''
}

function SignalMetric({ label, value, tone = 'neutral' }) {
  return (
    <div className={`radar-signal-metric radar-signal-metric--${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
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

function copySignalCode(code) {
  if (!code || typeof navigator === 'undefined' || !navigator.clipboard?.writeText) return
  navigator.clipboard.writeText(code).catch(() => {})
}

function readExpertModePreference() {
  if (typeof localStorage === 'undefined') return false
  try {
    return localStorage.getItem(EXPERT_MODE_STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

function formatRatio(value) {
  const num = Number(value)
  if (!Number.isFinite(num) || num <= 0) return '--'
  return num.toFixed(2)
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
        <span>默认读取 FAST 结构；如需周线/60/15 分完整链路，可切到 FULL。</span>
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

function formatMs(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '--'
  if (num >= 1000) return `${(num / 1000).toFixed(num >= 10000 ? 0 : 1)}s`
  return `${Math.round(num)}ms`
}

function formatCompactDate(value) {
  if (!value) return ''
  const text = String(value)
  if (text.includes('T')) return text.slice(5, 16).replace('T', ' ')
  if (text.includes(' ')) return text.slice(5, 16)
  return text.slice(5)
}

function profileTitle(profile) {
  if (profile === 'auto') return '自动结构：先跑 day / 30 / 5，必要时升级完整链路'
  if (profile === 'full') return '深度结构：周线 / 日线 / 60 / 30 / 15 / 5'
  return '快速结构：日线 / 30分 / 5分'
}

function upgradeReasonLabel(reason) {
  const text = String(reason || '')
  if (text.includes('FULL_FAILED')) return '深度雷达不可用'
  const labels = {
    FAST_CONFLICT: '已升级：结构冲突',
    LOW_CONFIDENCE: '已升级：证据不足',
    RISK_LINE_NEAR: '已升级：接近风险线',
  }
  return labels[text] || '已升级深度雷达'
}

function momentumText(momentum) {
  if (!momentum || !momentum.direction) return '动能未知'
  const ratio = Number(momentum.area_ratio)
  const ratioText = Number.isFinite(ratio) && ratio > 0 ? `${Math.round(ratio * 100)}%` : '--'
  return `${momentum.label || '动能'} ${ratioText}`
}
