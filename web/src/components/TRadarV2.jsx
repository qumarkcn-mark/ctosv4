import { useState, useEffect } from 'react'
import { API_BASE } from '../config.js'
import './TRadar.css'

const STATE_CONFIG = {
  THIRD_BUY_CONFIRMED: { label: '三买确立', color: '#22c55e', emoji: '🟢' },
  THIRD_SELL_CONFIRMED: { label: '三卖确立', color: '#ef4444', emoji: '🛑' },
  WAITING_FOR_PULLBACK: { label: '等待回踩', color: '#3b82f6', emoji: '🔵' },
  IN_CENTER_OSC: { label: '中枢震荡', color: '#3b82f6', emoji: '🔵' },
  DOWNWARD_LEAVING: { label: '向下离开', color: '#ef4444', emoji: '🔴' },
  UPWARD_LEAVING: { label: '向上离开', color: '#22c55e', emoji: '🟢' },
  TREND_EXTENDING: { label: '构建中', color: '#f59e0b', emoji: '🟡' },
  LIMBO: { label: '中阴阶段', color: '#f97316', emoji: '🟠' },
  FAKE_BREAK: { label: '假突破', color: '#f59e0b', emoji: '⚠️' },
  SMALL_TO_BIG: { label: '小转大', color: '#a855f7', emoji: '🔮' },
  CONFIRMED_BREAK: { label: '三买确立', color: '#06b6d4', emoji: '🚀' },
  UNKNOWN: { label: '数据不足', color: '#666', emoji: '⚪' },
}

const LEVEL_NAMES = {
  day: '日线', week: '周线', m60: '60分钟', m30: '30分钟', m15: '15分钟', m5: '5分钟',
}

const DECISION_STYLE = {
  buy:  { color: '#22c55e', icon: '📈' },
  sell: { color: '#ef4444', icon: '📉' },
  wait: { color: '#f59e0b', icon: '⏳' },
}

// ═══════════════════════════════════════════════════════════════
// 读盘引擎 V4.5：完全分类 + 结构事实 + 监控价位
// 忠于缠论原文：不预测，只分类，为每种可能准备预案
// ═══════════════════════════════════════════════════════════════
function readBoard(matrix, week, nestingData) {
  if (!matrix || matrix.length < 2) return null
  const [l1, l2, l3] = matrix

  // ── ① 结构事实：纯客观描述各级别走势类型 ──
  const describeZoushi = (item) => {
    const name = LEVEL_NAMES[item.level] || item.level
    const zt = item.zoushi_type
    if (!zt || zt.type === '数据不足') return `${name}：数据不足`
    let desc = `${name}：${zt.type}`
    if (zt.zs_count > 0) desc += `(${zt.zs_count}个中枢)`
    if (item.zd > 0 && item.zg > 0) {
      desc += ` 中枢${item.zd.toFixed(2)}-${item.zg.toFixed(2)}`
    }
    return desc
  }

  // 周线背景
  let weekContext = ''
  let weekBearish = false
  if (week) {
    const wp = (week.patterns || []).join(' ')
    if (week.is_near_historical_high) weekContext = '周线历史新高区域'
    else if (week.has_top_fractal) weekContext = '周线顶分型'
    else if (week.has_bottom_fractal) weekContext = '周线底分型'
    else {
      const wzt = week.zoushi_type?.type || ''
      weekContext = `周线${wzt || '—'}`
    }
    if (wp.includes('顶背驰') || wp.includes('1卖')) weekBearish = true
  }

  const structure = {
    weekContext,
    weekBearish,
    levels: matrix.map(item => ({
      name: LEVEL_NAMES[item.level] || item.level,
      desc: describeZoushi(item),
      zoushiType: item.zoushi_type?.type || '数据不足',
      completion: item.zoushi_type?.completion || '',
    })),
  }

  // ── ② 级别统御：大级别否决权 ──
  let veto = null
  const l1Type = l1.zoushi_type?.type || ''
  const l1Patterns = (l1.patterns || []).join(' ')

  if (l1Type === '下跌趋势' && !l1Patterns.includes('底背驰')) {
    veto = '日线下跌趋势未完成（无底背驰），次级别反弹属于卖点机会，不参与做多'
  }
  if (weekBearish && l1Type !== '上涨趋势') {
    veto = (veto ? veto + '；' : '') + '周线顶背驰压制'
  }

  // ── ③ 完全分类：优先使用次级别(l2)的分类 ──
  let classifications = (l2.classifications || []).map(c => ({ ...c }))

  // 标记当前所在分类
  const l2Price = l2.price || 0
  const l2Zg = l2.zg || 0
  const l2Zd = l2.zd || 0
  const l2Type = l2.zoushi_type?.type || ''
  const l2Patterns = (l2.patterns || []).join(' ')

  classifications = classifications.map(c => {
    let highlighted = false
    // 判断当前价格落入哪个分类
    if (l2Type === '盘整') {
      if (c.id === 'A' && l2Price > l2Zg) highlighted = true
      else if (c.id === 'B' && l2Price >= l2Zd && l2Price <= l2Zg) highlighted = true
      else if (c.id === 'C' && l2Price < l2Zd) highlighted = true
    } else if (l2Type === '上涨趋势') {
      const hasDiv = l2Patterns?.includes('背驰')
      if (c.id === 'A' && !hasDiv) highlighted = true
      else if (c.id === 'B' && hasDiv) highlighted = true
    } else if (l2Type === '下跌趋势') {
      const hasDiv = l2Patterns?.includes('背驰')
      if (c.id === 'A' && !hasDiv) highlighted = true
      else if (c.id === 'B' && hasDiv) highlighted = true
    } else if (l2Type === '构建中') {
      highlighted = c.id === 'A'
    }

    // 大级别否决
    if (veto && (c.action?.includes('入场') || c.action?.includes('买'))) {
      return { ...c, highlighted, vetoed: true, vetoReason: veto }
    }
    return { ...c, highlighted }
  })


  // ── ④ 监控价位 ──
  const watchPrices = []

  // 次级别中枢边界
  if (l2Zg > 0 && l2Zd > 0) {
    watchPrices.push({ price: l2Zg, label: `${LEVEL_NAMES[l2.level]}ZG`, role: '突破/回踩分界' })
    watchPrices.push({ price: l2Zd, label: `${LEVEL_NAMES[l2.level]}ZD`, role: '支撑/破位分界' })
  }
  // 大级别中枢边界
  if (l1.zg > 0 && l1.zd > 0 && l1.level !== l2.level) {
    watchPrices.push({ price: l1.zg, label: `${LEVEL_NAMES[l1.level]}ZG`, role: '大级别压力' })
    watchPrices.push({ price: l1.zd, label: `${LEVEL_NAMES[l1.level]}ZD`, role: '大级别支撑' })
  }
  // 近期极值
  if (l2.ex_support > 0 && !watchPrices.find(w => Math.abs(w.price - l2.ex_support) < 0.01)) {
    watchPrices.push({ price: l2.ex_support, label: '近期支撑', role: '短期极低点' })
  }

  // 去重 + 排序
  const dedupedPrices = []
  const seen = new Set()
  for (const wp of watchPrices) {
    const key = wp.price.toFixed(2)
    if (!seen.has(key)) { seen.add(key); dedupedPrices.push(wp) }
  }
  dedupedPrices.sort((a, b) => b.price - a.price)

  // ── ⑤ 区间套 ──
  const intervalNesting = nestingData || null

  return { structure, classifications, watchPrices: dedupedPrices, veto, intervalNesting }
}


export default function TRadarV2({ symbol }) {
  const [mode, setMode] = useState('A')
  const [collapsed, setCollapsed] = useState(false)
  const [showMatrix, setShowMatrix] = useState(false)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  // AI 推演
  const [deducing, setDeducing] = useState(false)
  const [aiReport, setAiReport] = useState(null)

  // 历史
  const [showHistory, setShowHistory] = useState(false)
  const [historyList, setHistoryList] = useState([])
  const [activeHistoryId, setActiveHistoryId] = useState(null)
  const [historyTimestamp, setHistoryTimestamp] = useState(null)

  const fetchCurrentMatrix = () => {
    if (!symbol) return
    setLoading(true)
    fetch(`${API_BASE}/chan/matrix/${symbol}`)
      .then(r => r.json())
      .then(json => { setData(json.data); setLoading(false) })
      .catch(() => setLoading(false))
  }

  useEffect(() => {
    setAiReport(null)
    setActiveHistoryId(null)
    setHistoryTimestamp(null)
    setShowHistory(false)
    fetchCurrentMatrix()
  }, [symbol])

  const matrix = data ? (mode === 'A' ? data.matrix_a : data.matrix_b) : []
  const nestingData = data ? (mode === 'A' ? data.interval_nesting_a : data.interval_nesting_b) : null
  const board = readBoard(matrix, data?.week, nestingData)

  // AI 拟人化推演
  const handleAIDeduce = async () => {
    if (!symbol) return
    setDeducing(true)
    try {
      const res = await fetch(`${API_BASE}/agent/radar_deduce`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, mode })
      })
      const result = await res.json()
      if (result.status === 'success') setAiReport(result.data)
    } catch (e) {
      console.error('AI Deduction failed', e)
    } finally {
      setDeducing(false)
    }
  }

  // 历史复盘
  const handleToggleHistory = async () => {
    if (!showHistory) {
      try {
        const res = await fetch(`${API_BASE}/agent/radar_history/${symbol}`)
        const json = await res.json()
        if (json.status === 'success') setHistoryList(json.data)
      } catch (e) {}
    }
    setShowHistory(!showHistory)
  }

  const loadHistorySnapshot = (h) => {
    setActiveHistoryId(h.id)
    setHistoryTimestamp(h.created_at)
    setData(h.matrix_data)
    const deduction = typeof h.deduction_process === 'string'
      ? JSON.parse(h.deduction_process) : h.deduction_process
    setAiReport(deduction)
    setShowHistory(false)
  }

  const handleBackToCurrent = () => {
    setActiveHistoryId(null)
    setHistoryTimestamp(null)
    setAiReport(null)
    fetchCurrentMatrix()
  }

  return (
    <div className={`tradar-v2 ${collapsed ? 'collapsed' : ''}`}>
      {/* Header */}
      <div className="tradar-v2-header" onClick={() => setCollapsed(!collapsed)}>
        <span className="tradar-v2-icon">🔮</span>
        <span className="tradar-v2-title">推演雷达</span>
        <div className="tradar-v2-tabs" onClick={e => e.stopPropagation()}>
          <button className={`tv2-tab ${mode === 'A' ? 'active' : ''}`} onClick={() => setMode('A')}>短线</button>
          <button className={`tv2-tab ${mode === 'B' ? 'active' : ''}`} onClick={() => setMode('B')}>波段</button>
        </div>
        <span className="tradar-collapse-icon">{collapsed ? '▶' : '▼'}</span>
      </div>

      {!collapsed && (
        <div className="tradar-v2-body">
          {loading && <div className="tradar-loading">推算中...</div>}

          {/* ═══ 结构事实 + 完全分类 + 监控价位 ═══ */}
          {!loading && board && (
            <>
              {/* ── 结构事实 + 关键价位（合并） ── */}
              <div className="board-structure">
                {board.structure.weekContext && (
                  <div className="structure-week">{board.structure.weekContext}</div>
                )}
                {board.structure.levels.map((lv, i) => {
                  // 找到属于这个级别的监控价位
                  const lvPrices = board.watchPrices.filter(wp => wp.label.includes(lv.name))
                  return (
                    <div key={i} className="structure-level">
                      <span className="structure-name">{lv.name}</span>
                      <span className={`structure-type structure-type--${lv.zoushiType === '上涨趋势' ? 'up' : lv.zoushiType === '下跌趋势' ? 'down' : 'neutral'}`}>
                        {lv.zoushiType}
                      </span>
                      {lvPrices.length > 0 && (
                        <span className="structure-prices">
                          {lvPrices.map((wp, j) => (
                            <span key={j} className={`structure-price-tag ${wp.label.includes('ZG') ? 'up' : 'down'}`}>
                              {wp.label.replace(lv.name, '')}:{wp.price.toFixed(2)}
                            </span>
                          ))}
                        </span>
                      )}
                    </div>
                  )
                })}
              </div>

              {/* ── 大级别否决 ── */}
              {board.veto && (
                <div className="board-veto">
                  <span className="veto-icon">⛔</span>
                  <span className="veto-text">{board.veto}</span>
                </div>
              )}

              {/* ── 区间套 ── */}
              {board.intervalNesting && board.intervalNesting.depth >= 2 && (
                <div className={`nesting-banner nesting-depth-${board.intervalNesting.depth}`}>
                  <span className="nesting-icon">{board.intervalNesting.direction === 'bottom' ? '🟢' : '🔴'}</span>
                  <span className="nesting-label">{board.intervalNesting.label}</span>
                  <span className="nesting-levels">
                    {board.intervalNesting.levels.map(l => LEVEL_NAMES[l.level] || l.level).join(' → ')}
                  </span>
                </div>
              )}

              {/* ── 完全分类 ── */}
              <div className="board-classifications">
                <div className="classifications-title">🔀 完全分类</div>
                {board.classifications.map(c => (
                  <div
                    key={c.id}
                    className={`classification-card ${c.highlighted ? 'highlighted' : ''} ${c.vetoed ? 'vetoed' : ''}`}
                  >
                    <div className="cls-header">
                      <span className="cls-id">{c.id}</span>
                      <span className="cls-name">{c.name}</span>
                      {c.highlighted && <span className="cls-current">← 当下</span>}
                    </div>
                    <div className="cls-condition">
                      <span className="cls-label">条件</span>
                      <span className="cls-text">{c.condition}</span>
                    </div>
                    <div className="cls-action-row">
                      <div className="cls-action">
                        <span className="cls-label">操作</span>
                        <span className="cls-text">{c.action}</span>
                      </div>
                      {c.stopLoss && (
                        <div className="cls-stoploss">
                          <span className="cls-sl-price">{c.stopLoss}</span>
                          {c.stopReason && <span className="cls-sl-reason">({c.stopReason})</span>}
                        </div>
                      )}
                    </div>
                    {c.vetoed && (
                      <div className="cls-veto-overlay">⛔ {c.vetoReason}</div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}

          {/* ═══ AI 深度看盘 ═══ */}
          {!loading && matrix.length > 0 && (
            <div className="tradar-v2-ai-section">
              {!aiReport ? (
                <div className="tradar-ai-actions">
                  <button className="tradar-ai-btn" onClick={handleAIDeduce} disabled={deducing}>
                    {deducing ? '🧠 推演中...' : '🧠 AI 深度看盘'}
                  </button>
                  <button
                    className={`tradar-history-btn ${showHistory ? 'active' : ''}`}
                    onClick={handleToggleHistory}
                  >
                    🕰️ {showHistory ? '收起' : '复盘'}
                  </button>
                </div>
              ) : (
                <div className="thinking-report">
                  {activeHistoryId && (
                    <div className="report-history-banner">
                      <span>⚠️ 历史快照 ({historyTimestamp})</span>
                      <button onClick={handleBackToCurrent}>返回实时</button>
                    </div>
                  )}

                  {/* 思维过程 */}
                  <div className="thinking-timeline">
                    {(aiReport.thinking || []).map((step, idx) => (
                      <div key={idx} className="thinking-step">
                        <div className="thinking-connector">
                          <span className="thinking-icon">{step.icon}</span>
                          {idx < (aiReport.thinking?.length || 0) - 1 && <div className="thinking-line" />}
                        </div>
                        <div className="thinking-content">
                          <span className="thinking-level">{LEVEL_NAMES[step.level] || step.level}</span>
                          <span className="thinking-say">{step.say}</span>
                        </div>
                      </div>
                    ))}
                  </div>

                  {aiReport.position && (
                    <div className="thinking-position">📍 {aiReport.position}</div>
                  )}

                  {/* AI 的完全分类（如果AI输出了） */}
                  {aiReport.classifications && aiReport.classifications.length > 0 && (
                    <div className="ai-classifications">
                      <div className="classifications-title">🤖 AI 完全分类</div>
                      {aiReport.classifications.map((c, idx) => (
                        <div key={idx} className={`classification-card ${c.is_current ? 'highlighted' : ''}`}>
                          <div className="cls-header">
                            <span className="cls-id">{c.id}</span>
                            <span className="cls-name">{c.name}</span>
                            {c.is_current && <span className="cls-current">← 当下</span>}
                          </div>
                          <div className="cls-condition">
                            <span className="cls-label">条件</span>
                            <span className="cls-text">{c.condition}</span>
                          </div>
                          <div className="cls-action-row">
                            <div className="cls-action">
                              <span className="cls-label">操作</span>
                              <span className="cls-text">{c.action}</span>
                            </div>
                            {c.stopLoss && (
                              <div className="cls-stoploss">
                                <span className="cls-sl-reason">{c.stopLoss}</span>
                              </div>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* 兼容老的 decisions 格式 */}
                  {aiReport.decisions && aiReport.decisions.length > 0 && (
                    <div className="decision-tree">
                      <div className="decision-tree-title">接下来盯什么</div>
                      {aiReport.decisions.map((d, idx) => {
                        const style = DECISION_STYLE[d.type] || DECISION_STYLE.wait
                        return (
                          <div key={idx} className="decision-branch" style={{ '--branch-color': style.color }}>
                            <div className="decision-if">
                              <span className="decision-keyword">如果</span>
                              <span className="decision-condition">{d.if || d.if_}</span>
                            </div>
                            <div className="decision-then">
                              <span className="decision-icon">{style.icon}</span>
                              <span className="decision-action">{d.then}</span>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}

                  {aiReport.red_line && aiReport.red_line !== 'N/A' && (
                    <div className="red-line-banner">🔴 止损红线: {aiReport.red_line}</div>
                  )}

                  {/* AI 监控价位 */}
                  {aiReport.watch_prices && aiReport.watch_prices.length > 0 && (
                    <div className="board-watch-prices" style={{ marginTop: 8 }}>
                      <div className="watch-title">🤖 AI 关键价位</div>
                      {aiReport.watch_prices.map((wp, i) => (
                        <div key={i} className="watch-item">
                          <span className="watch-dot up">●</span>
                          <span className="watch-price mono">{wp.price}</span>
                          <span className="watch-role">{wp.role}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  <div className="tradar-v2-actions-footer">
                    <button className="tradar-ai-refresh" onClick={() => setAiReport(null)}>收起分析</button>
                  </div>
                </div>
              )}

              {showHistory && (
                <div className="tradar-history-list">
                  {historyList.length === 0 ? (
                    <div className="history-empty">暂无历史推演</div>
                  ) : (
                    historyList.map(h => (
                      <div key={h.id} className="history-item" onClick={() => loadHistorySnapshot(h)}>
                        <span className="history-date">{h.created_at?.slice(5, 16)}</span>
                        <span className="history-summary" title={h.summary}>{h.summary}</span>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          )}

          {/* ═══ 原始矩阵（折叠） ═══ */}
          {!loading && matrix.length > 0 && (
            <div className="matrix-collapsible">
              <div className="matrix-toggle" onClick={() => setShowMatrix(!showMatrix)}>
                <span>{showMatrix ? '▾' : '▸'} 详细矩阵</span>
              </div>
              {showMatrix && (
                <div className="matrix-rows">
                  {matrix.map((item) => {
                    const cfg = STATE_CONFIG[item.state] || STATE_CONFIG.UNKNOWN
                    return (
                      <div key={item.level} className="tradar-level">
                        <span className="level-dot">{cfg.emoji}</span>
                        <span className="level-name">{LEVEL_NAMES[item.level] || item.level}</span>
                        <span className="level-state" style={{ color: cfg.color }}>{cfg.label}</span>
                        {item.zd > 0 && (
                          <span className="level-zs mono">
                            ZG:{item.zg.toFixed(2)} ZD:{item.zd.toFixed(2)}
                          </span>
                        )}
                        {item.patterns && item.patterns.length > 0 && (
                          <div className="level-patterns">
                            {item.patterns.map((pt, i) => (
                              <span key={i} className={`pattern-tag ${pt.includes('危') || pt.includes('背驰') ? 'warn' : ''}`}>
                                {pt}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
