import { useState, useEffect } from 'react'
import KlineChart from '../components/KlineChart.jsx'
import './ChanMatrix.css'

const API_BASE = 'http://localhost:8000/api'

const STATE_LABELS = {
  THIRD_BUY_CONFIRMED: '三买确立',
  WAITING_FOR_PULLBACK: '等待回踩',
  IN_CENTER_OSC: '中枢震荡',
  DOWNWARD_LEAVING: '向下离开',
  UPWARD_LEAVING: '向上离开',
  UNKNOWN: '数据不足',
}

const STATE_COLORS = {
  THIRD_BUY_CONFIRMED: 'gold',
  WAITING_FOR_PULLBACK: 'blue',
  IN_CENTER_OSC: 'blue',
  DOWNWARD_LEAVING: 'red',
  UPWARD_LEAVING: 'green',
  UNKNOWN: 'gray',
}

export default function ChanMatrix() {
  const [symbol, setSymbol] = useState('sz000001')
  const [input, setInput] = useState('sz000001')
  const [data, setData] = useState(null)
  const [mode, setMode] = useState('A')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetchMatrix = async (sym) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_BASE}/chan/matrix/${sym}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      setData(json.data)
    } catch (e) {
      setError(e.message)
      setData(null)
    }
    setLoading(false)
  }

  useEffect(() => { fetchMatrix(symbol) }, [symbol])

  const handleSubmit = (e) => {
    e.preventDefault()
    if (input.trim()) setSymbol(input.trim().toLowerCase())
  }

  const matrix = data ? (mode === 'A' ? data.matrix_a : data.matrix_b) : []

  const computeAdvice = (m) => {
    if (!m || m.length < 2) return { text: '等待数据...', level: 'neutral' }
    const [l1, l2] = m
    if (l1.state === 'DOWNWARD_LEAVING' && l2.state !== 'THIRD_BUY_CONFIRMED')
      return { text: '⚠️ 主级别向下破位中，极度弱势，切勿盲目接飞刀！', level: 'danger' }
    if (l1.state === 'WAITING_FOR_PULLBACK' && l2.state === 'THIRD_BUY_CONFIRMED')
      return { text: '🔥 【核弹级信号】主级别完成离开段寻找支撑，次级别三买共振，准备起飞！', level: 'fire' }
    if (l1.state === 'THIRD_BUY_CONFIRMED')
      return { text: '🚀 大级别三买已确立，主升浪启动中。', level: 'success' }
    if (l1.state === 'IN_CENTER_OSC')
      return { text: '⚖️ 维持中枢内部震荡，注意高抛低吸节奏。', level: 'neutral' }
    return { text: '保持观望，等待结构明朗。', level: 'neutral' }
  }

  const advice = computeAdvice(matrix)

  const LEVEL_NAMES = {
    day: '日线', m60: '60分钟', m30: '30分钟', m15: '15分钟', m5: '5分钟',
  }

  return (
    <div className="chan-matrix">
      <div className="chan-header">
        <h2>🔮 缠论推演矩阵</h2>
        <form className="symbol-search" onSubmit={handleSubmit}>
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="输入股票代码 如 sh600519"
            className="search-input"
          />
          <button type="submit" className="search-btn">推演</button>
        </form>
      </div>

      {/* 模式切换 */}
      <div className="mode-toggle">
        <button className={`toggle-btn ${mode === 'A' ? 'active' : ''}`} onClick={() => setMode('A')}>
          日/30/5 短线
        </button>
        <button className={`toggle-btn ${mode === 'B' ? 'active' : ''}`} onClick={() => setMode('B')}>
          日/60/15 波段
        </button>
      </div>

      {/* 交易建议 */}
      {!loading && data && (
        <div className={`advice-banner ${advice.level}`}>
          {advice.text}
        </div>
      )}

      {loading && <div className="chan-loading">引擎推算中...</div>}
      {error && <div className="chan-error">请求失败: {error}</div>}

      {/* K 线图表 — 全功能缠论沙盘（笔 / 中枢 / MACD） */}
      {symbol && (
        <KlineChart symbol={symbol} />
      )}

      {/* 级别卡片 */}
      {!loading && matrix.length > 0 && (
        <div className="level-cards">
          {matrix.map((item) => (
            <div key={item.level} className="level-card">
              <div className="level-card-header">
                <span className="level-name">{LEVEL_NAMES[item.level] || item.level}</span>
                <span className={`state-badge ${STATE_COLORS[item.state] || 'gray'}`}>
                  {STATE_LABELS[item.state] || item.state}
                </span>
              </div>
              {item.zd > 0 ? (
                <div className="zhongshu-watermark">
                  <div className="zg-label">ZG <span className="mono">{item.zg.toFixed(2)}</span></div>
                  <div className="center-bar">
                    <div className="center-fill"></div>
                    <span className="center-text">中枢区间</span>
                  </div>
                  <div className="zd-label">ZD <span className="mono">{item.zd.toFixed(2)}</span></div>
                </div>
              ) : (
                <div className="no-zhongshu">单边脱离中枢运行中</div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
