import { useEffect, useMemo, useState } from 'react'
import PlaybookItemRow from '../components/PlaybookItemRow.jsx'
import PlanResponseButtons from '../components/PlanResponseButtons.jsx'
import './DailyPlaybook.css'

const API = ''
const REQUEST_TIMEOUT_MS = 12000

async function fetchJson(url, options = {}) {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const resp = await fetch(url, { ...options, signal: controller.signal })
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}))
      throw new Error(data.detail || options.errorMessage || '请求失败')
    }
    return await resp.json()
  } catch (err) {
    if (err.name === 'AbortError') throw new Error('后端响应超时，请稍后重试')
    throw err
  } finally {
    window.clearTimeout(timer)
  }
}

function conditionSummary(item) {
  const conditions = item.trigger?.conditions || []
  if (!conditions.length) return '暂无明确触发条件，进入 Radar 复核结构。'
  return conditions
    .slice(0, 5)
    .map((condition) => `${condition.label || condition.condition_id}: ${condition.status}`)
    .join(' / ')
}

function stopReference(item) {
  const ref = item.trigger?.stop_reference
  if (!ref?.value) return '—'
  return `${ref.level || ''}${ref.field || ''} ${Number(ref.value).toFixed(2)}`
}

const SOURCE_LABEL = {
  positions: '持仓',
  scanner: '机会池',
  watchlist: '自选股',
  unknown: '来源待定',
}

const QUEUE_SECTIONS = [
  {
    id: 'action',
    title: '需处理',
    hint: '条件触发或需要用户今天确认的事件。',
    match: (item) => item.status === 'TRIGGERED' && !item.response,
  },
  {
    id: 'watching',
    title: '观察中',
    hint: '计划内观察项，未触发前不行动。',
    match: (item) => item.status === 'WATCHING' && !item.response,
  },
  {
    id: 'review',
    title: '数据复核',
    hint: '结构失败或数据过期，只能人工复核。',
    match: (item) => (item.status === 'STALE' || item.status === 'ENGINE_ERROR') && !item.response,
  },
  {
    id: 'done',
    title: '已响应',
    hint: '已记录选择，盘后进入复盘。',
    match: (item) => Boolean(item.response) || ['EXECUTED', 'IGNORED', 'INVALIDATED'].includes(item.status),
  },
]

function eventType(item) {
  if (item.status === 'STALE' || item.status === 'ENGINE_ERROR') return '数据复核'
  if (item.status === 'TRIGGERED') return '条件触发'
  if (item.source === 'positions' || item.mode === 'HOLDING') return '持仓防线'
  return '观察机会'
}

function nextStep(item) {
  if (!item) return '—'
  const aiNative = item.trigger?.ai_native
  if (aiNative?.next_focus) return aiNative.next_focus
  if (item.status === 'ENGINE_ERROR') return '结构计算失败，先重试或去雷达查看错误。'
  if (item.status === 'STALE') return '数据过期，只做人工复核，不触发行动。'
  if (item.status === 'TRIGGERED') return '先去雷达复核结构，再在券商 App 或 QMT 手动处理。'
  if (item.mode === 'HOLDING') return '盯住失效条件和止损参考，盘中只响应计划内变化。'
  return '保持观察，条件未触发前不做动作。'
}

function aiNativePriorityLabel(priority) {
  return {
    HIGH: '高优先',
    MEDIUM_HIGH: '偏高',
    MEDIUM: '常规',
  }[priority] || '常规'
}

function formatPlaybookPrice(value) {
  const num = Number(value)
  if (!Number.isFinite(num) || num <= 0) return '—'
  return num >= 100 ? num.toFixed(2) : num.toFixed(3).replace(/0$/, '').replace(/0$/, '')
}

function formatPlaybookPct(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  return `${num >= 0 ? '+' : ''}${num.toFixed(1)}%`
}

function freshnessText(item) {
  const freshness = item?.invalidation?.freshness || item?.radar_snapshot?.freshness || {}
  if (!freshness || Object.keys(freshness).length === 0) return '未返回 freshness'
  if (freshness.is_stale) return freshness.stale_reason || '数据过期'
  return '结构有效'
}

function sourceDetail(item) {
  const source = item?.source || (item?.mode === 'HOLDING' ? 'positions' : 'unknown')
  const meta = item?.source_json || {}
  if (source === 'positions') {
    const quantity = meta.position?.quantity
    const avgCost = meta.position?.avg_cost
    if (quantity && avgCost) return `${quantity}股 · 成本 ${Number(avgCost).toFixed(2)}`
    return item?.source ? '来自当前持仓' : '来自当前持仓（旧计划未记录详细来源）'
  }
  if (source === 'scanner') {
    const strategy = meta.scanner?.strategy
    const score = meta.scanner?.score
    return [strategy, score != null ? `评分 ${Math.round(Number(score))}` : null].filter(Boolean).join(' · ') || '来自机会池候选'
  }
  if (source === 'watchlist') {
    return meta.watchlist?.group_name ? `分组：${meta.watchlist.group_name}` : '来自自选股'
  }
  return '旧计划未记录详细来源'
}

function itemSource(item) {
  if (item?.source) return item.source
  if (item?.mode === 'HOLDING') return 'positions'
  return 'unknown'
}

export default function DailyPlaybook({ onViewInChan, onOpenRotation }) {
  const [data, setData] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [reporting, setReporting] = useState(false)
  const [responding, setResponding] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const load = async (silent = false) => {
    if (!silent) setLoading(true)
    setError(null)
    try {
      const json = await fetchJson('/api/playbook/today', { errorMessage: '今日作战加载失败' })
      setData(json.data)
      setSelectedId((current) => current || json.data?.items?.[0]?.id || null)
    } catch (err) {
      setError(err.message)
    } finally {
      if (!silent) setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const id = window.setInterval(() => load(true), 30000)
    return () => window.clearInterval(id)
  }, [])

  const items = data?.items || []
  const groupedItems = useMemo(() => {
    const assigned = new Set()
    const sections = QUEUE_SECTIONS.map((section) => {
      const sectionItems = items.filter((item) => {
        if (assigned.has(item.id) || !section.match(item)) return false
        assigned.add(item.id)
        return true
      })
      return { ...section, items: sectionItems }
    })
    const overflow = items.filter((item) => !assigned.has(item.id))
    if (overflow.length) {
      sections.splice(1, 0, {
        id: 'other',
        title: '其他',
        hint: '未归类状态，保留给人工检查。',
        items: overflow,
      })
    }
    return sections
  }, [items])

  const firstQueueItem = useMemo(
    () => groupedItems.find((section) => section.items.length > 0)?.items[0] || null,
    [groupedItems]
  )

  const selectedItem = useMemo(() => {
    return items.find((item) => item.id === selectedId) || firstQueueItem
  }, [items, selectedId, firstQueueItem])
  const selectedAiNative = selectedItem?.trigger?.ai_native

  const metrics = data?.metrics || {}
  const report = data?.report
  const stale = data?.freshness?.is_stale
  const queueStats = useMemo(() => {
    const staleCount = items.filter((item) => item.status === 'STALE' || item.status === 'ENGINE_ERROR').length
    const respondedCount = items.filter((item) => item.response || ['EXECUTED', 'IGNORED', 'INVALIDATED'].includes(item.status)).length
    return {
      total: items.length,
      stale: staleCount,
      responded: respondedCount,
      unplanned: metrics.unplanned_trades || 0,
    }
  }, [items, metrics.unplanned_trades])

  const generate = async () => {
    setGenerating(true)
    setError(null)
    setNotice(null)
    try {
      const json = await fetchJson('/api/playbook/today/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: 1, sources: ['positions', 'scanner', 'watchlist'], max_items: 8 }),
        errorMessage: '生成今日作战失败',
      })
      setData(json.data)
      setSelectedId(json.data?.items?.[0]?.id || null)
      setNotice('今日作战计划已生成。')
    } catch (err) {
      setError(err.message)
    } finally {
      setGenerating(false)
    }
  }

  const generateReport = async () => {
    setReporting(true)
    setError(null)
    setNotice(null)
    try {
      const json = await fetchJson('/api/playbook/today/report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: 1 }),
        errorMessage: '生成盘后报告失败',
      })
      setData((current) => current ? { ...current, status: 'REVIEWED', report: json.data } : current)
      setNotice('盘后作战报告已生成。')
    } catch (err) {
      setError(err.message)
    } finally {
      setReporting(false)
    }
  }

  const respond = async (response) => {
    if (!selectedItem) return
    setResponding(true)
    setError(null)
    try {
      await fetchJson(`/api/playbook/items/${selectedItem.id}/response`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ response }),
        errorMessage: '记录响应失败',
      })
      await load(true)
      setNotice('响应已记录，盘后复盘会纳入这次选择。')
    } catch (err) {
      setError(err.message)
    } finally {
      setResponding(false)
    }
  }

  if (loading && !data) {
    return (
      <div className="daily-playbook-page">
        <div className="playbook-loading">正在读取今日作战...</div>
      </div>
    )
  }

  return (
    <div className="daily-playbook-page">
      <header className="playbook-header">
        <div className="playbook-title-group">
          <h2>作战台</h2>
          <p>盘前定计划，盘中只响应计划内事件，盘后复盘纪律偏差。</p>
          <div className="playbook-risk-note">所有计划仅供参考，不构成投资建议。</div>
        </div>
        <div className="playbook-actions">
          {onOpenRotation && (
            <button type="button" onClick={onOpenRotation} disabled={loading || generating}>
              持仓比较
            </button>
          )}
          <button type="button" onClick={() => load(false)} disabled={loading || generating}>
            刷新
          </button>
          <button type="button" onClick={generateReport} disabled={reporting || loading || items.length === 0}>
            {reporting ? '生成中...' : '盘后报告'}
          </button>
          <button type="button" onClick={generate} disabled={generating}>
            {generating ? '生成中...' : items.length ? '重新读取计划' : '生成今日作战计划'}
          </button>
        </div>
      </header>

      {error && <div className="playbook-error">{error}</div>}
      {notice && <div className="playbook-notice">{notice}</div>}
      {stale && (
        <div className="playbook-stale">
          部分标的数据过期或结构失败，系统不会把它们当作行动触发，只保留人工复核入口。
        </div>
      )}

      <section className="playbook-metrics">
        <div><span>{queueStats.total}</span><strong>队列事件</strong></div>
        <div><span>{queueStats.stale}</span><strong>需复核</strong></div>
        <div><span>{queueStats.responded}</span><strong>已响应</strong></div>
        <div><span>{queueStats.unplanned}</span><strong>计划外交易</strong></div>
      </section>

      {report && (
        <section className={`playbook-report${report.persisted ? ' is-persisted' : ''}`}>
          <div className="playbook-report-head">
            <div>
              <span>盘后报告</span>
              <strong>{report.review_focus || '等待盘后复盘'}</strong>
            </div>
            <em>{report.persisted ? '已保存' : '实时预览'}</em>
          </div>
          <div className="playbook-report-grid">
            <div><span>{report.summary?.triggered_items || 0}</span><strong>触发项</strong></div>
            <div><span>{report.summary?.responded_items || 0}</span><strong>已响应</strong></div>
            <div><span>{report.summary?.high_priority_items || 0}</span><strong>高优先</strong></div>
            <div><span>{formatRate(report.ai_settlement?.match_rate)}</span><strong>AI命中</strong></div>
            <div><span>{report.summary?.unplanned_trades || 0}</span><strong>计划外</strong></div>
          </div>
          <div className="playbook-report-body">
            <p>{pathDistributionText(report.ai_path_distribution)}</p>
            <p>{settlementText(report.ai_settlement)}</p>
            {(report.discipline_flags || []).length > 0 && (
              <div className="playbook-report-flags">
                {report.discipline_flags.map((flag) => <span key={flag}>{flag}</span>)}
              </div>
            )}
            {Object.keys(report.ai_settlement?.tag_counts || {}).length > 0 && (
              <div className="playbook-report-tags">
                {Object.entries(report.ai_settlement.tag_counts).map(([tag, count]) => (
                  <span key={tag}>{settlementTagLabel(tag)} {count}</span>
                ))}
              </div>
            )}
            {Object.keys(report.ai_settlement?.quality_counts || {}).length > 0 && (
              <div className="playbook-report-tags playbook-report-tags--quality">
                {Object.entries(report.ai_settlement.quality_counts).map(([quality, count]) => (
                  <span key={quality}>{sampleQualityLabel(quality)} {count}</span>
                ))}
              </div>
            )}
          </div>
        </section>
      )}

      {items.length === 0 ? (
        <section className="playbook-empty">
          <h3>今天还没有作战计划</h3>
          <p>先从持仓、今日机会和自选股里生成最多 8 个观察项。少一点，盯得住。</p>
          <button type="button" onClick={generate} disabled={generating}>
            {generating ? '生成中...' : '生成今日作战计划'}
          </button>
        </section>
      ) : (
        <div className="playbook-layout">
          <section className="playbook-list" aria-label="今日作战列表">
            {groupedItems.map((section) => (
              <div key={section.id} className={`playbook-queue-section playbook-queue-section--${section.id}`}>
                <div className="playbook-queue-head">
                  <div>
                    <h3>{section.title}</h3>
                    <p>{section.hint}</p>
                  </div>
                  <span>{section.items.length}</span>
                </div>
                {section.items.length === 0 ? (
                  <div className="playbook-queue-empty">暂无{section.title}事项</div>
                ) : (
                  <div className="playbook-queue-items">
                    {section.items.map((item) => (
                      <PlaybookItemRow
                        key={item.id}
                        item={item}
                        active={selectedItem?.id === item.id}
                        onSelect={(next) => setSelectedId(next.id)}
                        onViewInChan={onViewInChan}
                      />
                    ))}
                  </div>
                )}
              </div>
            ))}
          </section>

          <aside className="playbook-detail">
            {selectedItem ? (
              <>
                <div className="playbook-detail-head">
                  <div>
                    <span className="playbook-detail-event">{eventType(selectedItem)}</span>
                    <span className="playbook-detail-symbol mono">{selectedItem.symbol}</span>
                    {selectedItem.name && <span className="playbook-detail-name">{selectedItem.name}</span>}
                  </div>
                  <span className={`playbook-detail-status status-${String(selectedItem.status).toLowerCase()}`}>
                    {selectedItem.status}
                  </span>
                </div>

                <div className="playbook-detail-block">
                  <h4>队列原因</h4>
                  <p>{conditionSummary(selectedItem)}</p>
                </div>

                <div className="playbook-detail-block">
                  <h4>下一步</h4>
                  <p>{nextStep(selectedItem)}</p>
                </div>

                {selectedAiNative && (
                  <div className={`playbook-ai-native playbook-ai-native--${String(selectedAiNative.priority || 'MEDIUM').toLowerCase()}`}>
                    <div className="playbook-ai-native-head">
                      <span>AI 作战焦点</span>
                      <strong>{selectedAiNative.primary_name || '等待'} {Number(selectedAiNative.primary_score || 0)}</strong>
                      <em>{aiNativePriorityLabel(selectedAiNative.priority)}</em>
                    </div>
                    <p>{selectedAiNative.primary_reason || selectedAiNative.next_focus || '等待结构边界确认。'}</p>
                    {selectedAiNative.nearest_risk_line && (
                      <div className="playbook-ai-native-risk">
                        <span>{selectedAiNative.nearest_risk_line.label || selectedAiNative.nearest_risk_line.type || '最近边界'}</span>
                        <strong>{formatPlaybookPrice(selectedAiNative.nearest_risk_line.price)}</strong>
                        <em>{formatPlaybookPct(selectedAiNative.nearest_risk_line.distance_pct)}</em>
                      </div>
                    )}
                  </div>
                )}

                <div className="playbook-detail-grid">
                  <div>
                    <strong>失效条件</strong>
                    <span>{selectedItem.invalidation?.invalid_if || '—'}</span>
                  </div>
                  <div>
                    <strong>止损参考</strong>
                    <span>{stopReference(selectedItem)}</span>
                  </div>
                  <div>
                    <strong>数据状态</strong>
                    <span>{freshnessText(selectedItem)}</span>
                  </div>
                  <div>
                    <strong>来源</strong>
                    <span>{SOURCE_LABEL[itemSource(selectedItem)] || SOURCE_LABEL.unknown} · {sourceDetail(selectedItem)}</span>
                  </div>
                </div>

                {selectedItem.response && (
                  <div className="playbook-response-note">
                    已记录：{selectedItem.response.response}
                  </div>
                )}

                <PlanResponseButtons disabled={responding} onRespond={respond} />

                <button
                  type="button"
                  className="playbook-view-radar"
                  onClick={() => onViewInChan?.(selectedItem.symbol, selectedItem.name)}
                >
                  去雷达复核
                </button>
              </>
            ) : (
              <div className="playbook-empty-detail">选择一个作战项查看计划。</div>
            )}
          </aside>
        </div>
      )}
    </div>
  )
}

function pathDistributionText(distribution = {}) {
  const entries = Object.entries(distribution || {}).filter(([, count]) => Number(count) > 0)
  if (!entries.length) return '暂无 AI 路径评分样本。'
  const label = { A: '向上确认', B: '区间观察', C: '转弱失效', D: '停止推演', UNKNOWN: '未知' }
  return entries.map(([path, count]) => `${label[path] || path} ${count}`).join(' · ')
}

function settlementText(settlement = {}) {
  const reviewed = Number(settlement.reviewed_runs || 0)
  if (!reviewed) return 'AI 自动结算暂无已复盘样本。'
  return `AI 自动结算 ${reviewed} 条，命中 ${settlement.matched_runs || 0}，未兑现 ${settlement.wrong_runs || 0}，平均分 ${Number(settlement.average_replay_score || 0).toFixed(1)}。`
}

function formatRate(value) {
  const num = Number(value)
  if (!Number.isFinite(num) || num <= 0) return '—'
  return `${Math.round(num * 100)}%`
}

function settlementTagLabel(tag) {
  return {
    MATCHED: '命中',
    OVER_OPTIMISTIC: '偏乐观',
    OVER_PESSIMISTIC: '偏悲观',
    RANGE_BROKEN: '区间打破',
    REPEATED_DIVERGENCE_RISK: '背了又背',
    RISK_LINE_EFFECTIVE: '风险线有效',
    RISK_LINE_TESTED: '风险线测试',
  }[tag] || tag
}

function sampleQualityLabel(quality) {
  return {
    HIGH: '高质量样本',
    MEDIUM: '中质量样本',
    LOW: '低质量样本',
    UNKNOWN: '未分层样本',
  }[quality] || quality
}
