import { useState, useEffect } from 'react'
import { API_BASE } from '../config.js'
import './TRadar.css'

const STATE_CONFIG = {
  THIRD_BUY_CONFIRMED: { label: '三买确立', color: '#22c55e', emoji: '🟢' },
  WAITING_FOR_PULLBACK: { label: '等待回踩', color: '#3b82f6', emoji: '🔵' },
  IN_CENTER_OSC: { label: '中枢震荡', color: '#3b82f6', emoji: '🔵' },
  DOWNWARD_LEAVING: { label: '向下离开', color: '#ef4444', emoji: '🔴' },
  UPWARD_LEAVING: { label: '向上离开', color: '#22c55e', emoji: '🟢' },
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
  if (l1.state === 'WAITING_FOR_PULLBACK' && l2.state === 'THIRD_BUY_CONFIRMED')
    return { text: '🔥 主级别离开段 + 次级别三买共振', level: 'fire' }
  if (l1.state === 'THIRD_BUY_CONFIRMED')
    return { text: '🚀 大级别三买已确立', level: 'success' }
  if (l1.state === 'IN_CENTER_OSC')
    return { text: '⚖️ 中枢震荡中', level: 'neutral' }
  return { text: '观望等待结构明朗', level: 'neutral' }
}

export default function TRadar({ symbol }) {
  const [data, setData] = useState(null)
  const [mode, setMode] = useState('A')
  const [collapsed, setCollapsed] = useState(false)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!symbol) return
    setLoading(true)
    fetch(`${API_BASE}/chan/matrix/${symbol}`)
      .then((r) => r.json())
      .then((json) => {
        setData(json.data)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [symbol])

  const matrix = data ? (mode === 'A' ? data.matrix_a : data.matrix_b) : []
  const advice = computeAdvice(matrix)

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

          {/* 建议 banner */}
          {!loading && matrix.length > 0 && (
            <div className={`tradar-advice ${advice.level}`}>
              {advice.text}
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
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
