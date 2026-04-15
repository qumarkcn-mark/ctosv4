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
  TREND_EXTENDING: { label: '趋势延伸', color: '#f59e0b', emoji: '🟡' },
  UNKNOWN: { label: '数据不足', color: '#666', emoji: '⚪' },
}

const LEVEL_NAMES = {
  day: '日线', m60: '60分钟', m30: '30分钟', m15: '15分钟', m5: '5分钟',
}

function computeAdvice(matrix) {
  if (!matrix || matrix.length < 2) return { text: '等待数据...', level: 'neutral' }
  const [l1, l2] = matrix
  if (l1.state === 'DOWNWARD_LEAVING' && l2.state !== 'THIRD_BUY_CONFIRMED')
    return { text: '⚠️ 主级别向下破位，极度弱势', level: 'danger' }
  if (l1.state === 'THIRD_SELL_CONFIRMED')
    return { text: '🛑 大级别三卖已确立，逢高离场', level: 'danger' }
  if (l1.state === 'WAITING_FOR_PULLBACK' && l2.state === 'THIRD_BUY_CONFIRMED')
    return { text: '🔥 主级别离开段 + 次级别三买共振', level: 'fire' }
  if (l1.state === 'THIRD_BUY_CONFIRMED')
    return { text: '🚀 大级别三买已确立', level: 'success' }
  if (l1.state === 'UPWARD_LEAVING')
    return { text: '🚀 大级别向上离开中枢', level: 'success' }
  if (l1.state === 'TREND_EXTENDING')
    return { text: '📈 趋势延伸中，等待中枢形成', level: 'neutral' }
  if (l1.state === 'IN_CENTER_OSC')
    return { text: '⚖️ 中枢震荡中', level: 'neutral' }
  return { text: '观望等待结构明朗', level: 'neutral' }
}

export default function TRadar({ symbol }) {
  const [data, setData] = useState(null)
  const [mode, setMode] = useState('A')
  const [collapsed, setCollapsed] = useState(false)
  const [loading, setLoading] = useState(false)

  // AI 推演相关状态
  const [deducing, setDeducing] = useState(false)
  const [aiReport, setAiReport] = useState(null)
  
  // 历史复盘相关状态
  const [showHistory, setShowHistory] = useState(false)
  const [historyList, setHistoryList] = useState([])
  const [activeHistoryId, setActiveHistoryId] = useState(null)
  const [historyTimestamp, setHistoryTimestamp] = useState(null)

  const fetchCurrentMatrix = () => {
    if (!symbol) return
    setLoading(true)
    fetch(`${API_BASE}/chan/matrix/${symbol}`)
      .then((r) => r.json())
      .then((json) => {
        setData(json.data)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }

  useEffect(() => {
    setAiReport(null) // 切换股票时清空上一次的战报
    setActiveHistoryId(null)
    setHistoryTimestamp(null)
    setShowHistory(false)
    fetchCurrentMatrix()
  }, [symbol])


  const matrix = data ? (mode === 'A' ? data.matrix_a : data.matrix_b) : []
  const advice = computeAdvice(matrix)

  // 触发 AI 推演
  const handleAIDeduce = async () => {
    if (!symbol) return
    setDeducing(true)
    try {
      const res = await fetch(`${API_BASE}/agent/radar_deduce`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol })
      })
      const result = await res.json()
      if (result.status === 'success') {
        const rawBody = result.data.scenarios || result.data
        // Fallback for string vs object response
        const reportObj = typeof rawBody === 'string' ? JSON.parse(rawBody) : rawBody
        setAiReport(reportObj)
      }
    } catch (e) {
      console.error("AI Deduction failed", e)
    } finally {
      setDeducing(false)
    }
  }

  const handleToggleHistory = async () => {
    if (!showHistory) {
      try {
        const res = await fetch(`${API_BASE}/agent/radar_history/${symbol}`)
        const json = await res.json()
        if (json.status === 'success') {
          setHistoryList(json.data)
        }
      } catch (e) {}
    }
    setShowHistory(!showHistory)
  }

  const loadHistorySnapshot = (h) => {
    setActiveHistoryId(h.id)
    setHistoryTimestamp(h.created_at)
    setData(h.matrix_data)
    setAiReport({ summary: h.summary, deduction_process: h.deduction_process })
    setShowHistory(false) // 选中后收起列表
  }

  const handleBackToCurrent = () => {
    setActiveHistoryId(null)
    setHistoryTimestamp(null)
    setAiReport(null)
    fetchCurrentMatrix()
  }

  return (
    <div className={`tradar ${collapsed ? 'collapsed' : ''}`}>
      <div className="tradar-header" onClick={() => setCollapsed(!collapsed)}>
        <span className="tradar-icon">🔮</span>
        <span className="tradar-title">推演雷达</span>
        <div className="tradar-mode-switch">
          <button
            className={`mode-btn ${mode === 'A' ? 'active' : ''}`}
            onClick={(e) => { e.stopPropagation(); setMode('A') }}
          >
            短线
          </button>
          <button
            className={`mode-btn ${mode === 'B' ? 'active' : ''}`}
            onClick={(e) => { e.stopPropagation(); setMode('B') }}
          >
            波段
          </button>
        </div>
        <span className="tradar-collapse-icon">{collapsed ? '▶' : '▼'}</span>
      </div>

      {!collapsed && (
        <div className="tradar-body">
          {loading && <div className="tradar-loading">推算中...</div>}

          {/* AI 深度推演面板 */}
          {!loading && matrix.length > 0 && (
            <div className="tradar-ai-section">
              {!aiReport ? (
                <>
                  <div className={`tradar-advice ${advice.level}`}>
                    {advice.text}
                  </div>
                  <div className="tradar-ai-actions">
                    <button 
                      className="tradar-ai-btn" 
                      onClick={handleAIDeduce} 
                      disabled={deducing}
                    >
                      {deducing ? '🧠 推演中...' : '⚡ AI推演'}
                    </button>
                    <button
                      className={`tradar-history-btn ${showHistory ? 'active' : ''}`}
                      onClick={handleToggleHistory}
                    >
                      🕰️ {showHistory ? '收起历史' : '复盘'}
                    </button>
                  </div>
                </>
              ) : (
                <div className="tradar-ai-report">
                  {activeHistoryId && (
                    <div className="report-history-banner">
                      <span>⚠️ 历史快照视图 ({historyTimestamp})</span>
                      <button onClick={handleBackToCurrent}>返回实时</button>
                    </div>
                  )}
                  <div className="report-summary">{aiReport.summary}</div>
                  <ul className="report-list">
                    {(aiReport.deduction_process || []).map((step, idx) => (
                      <li key={idx}>▸ {step}</li>
                    ))}
                  </ul>
                  {!activeHistoryId && (
                    <button className="tradar-ai-refresh" onClick={() => setAiReport(null)}>
                      收起战报
                    </button>
                  )}
                </div>
              )}
              
              {/* 历史记录列表槽位 */}
              {showHistory && (
                <div className="tradar-history-list">
                  {historyList.length === 0 ? (
                    <div className="history-empty">暂无该股历史推演</div>
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

          {/* 级别状态行 */}
          {!loading && matrix.map((item) => {
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
                {/* 渲染形态标签 (Patterns) */}
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
  )
}
