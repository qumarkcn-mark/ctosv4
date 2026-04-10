import { useState, useEffect } from 'react'
import './BehaviorReport.css'

const API_BASE = 'http://localhost:8000/api'

const LEVEL_ICONS = { critical: '🔴', warning: '🟡', success: '🟢', info: '🔵' }

export default function BehaviorReport() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API_BASE}/behavior/report?user_id=1`)
      .then(r => r.json())
      .then(json => { setData(json.data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  if (loading) return <div className="behavior-loading">正在扫描你的交易基因...</div>
  if (!data) return <div className="behavior-loading">暂无数据</div>

  const { discipline_score: score, metrics: m, diagnosis } = data

  // 六维数据归一化
  const dims = [
    Math.min(m.win_rate || 0, 100),
    Math.min((m.profit_loss_ratio || 0) * 33, 100),
    Math.min(m.stop_loss_execution_rate || 0, 100),
    Math.min(100 - (m.counter_trend_rate || 0), 100),
    Math.min(100 - (m.impulse_trade_rate || 0), 100),
    Math.min((m.avg_hold_days || 0) * 5, 100),
  ]
  const labels = ['胜率', '盈亏比', '止损纪律', '趋势合规', '情绪控制', '持仓耐心']

  // SVG 六边形坐标
  const cx = 120, cy = 120, r = 90
  const toPoints = (values) => values.map((v, i) => {
    const angle = (Math.PI * 2 * i) / 6 - Math.PI / 2
    const ratio = v / 100
    return `${cx + r * ratio * Math.cos(angle)},${cy + r * ratio * Math.sin(angle)}`
  }).join(' ')

  const bgPoints = toPoints([100,100,100,100,100,100])
  const bg60 = toPoints([60,60,60,60,60,60])
  const bg30 = toPoints([30,30,30,30,30,30])
  const dataPoints = toPoints(dims)

  const labelPositions = labels.map((_, i) => {
    const angle = (Math.PI * 2 * i) / 6 - Math.PI / 2
    return { x: cx + (r + 20) * Math.cos(angle), y: cy + (r + 20) * Math.sin(angle) }
  })

  const scoreColor = score >= 70 ? '#22C55E' : score >= 40 ? '#EAB308' : '#EF4444'

  return (
    <div className="behavior-report">
      <h2>📈 投资行为体检</h2>

      <div className="report-grid">
        {/* 左: 评分 + 雷达 */}
        <div className="report-left">
          <div className="score-ring" style={{ '--score-color': scoreColor }}>
            <div className="score-inner">
              <span className="score-val">{score}</span>
              <span className="score-sub">纪律评分</span>
            </div>
          </div>

          <div className="radar-container">
            <svg viewBox="0 0 240 240" className="radar-svg">
              <polygon points={bgPoints} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="1"/>
              <polygon points={bg60} fill="none" stroke="rgba(255,255,255,0.05)" strokeWidth="0.5"/>
              <polygon points={bg30} fill="none" stroke="rgba(255,255,255,0.03)" strokeWidth="0.5"/>
              <polygon points={dataPoints} fill="rgba(234,179,8,0.2)" stroke="rgba(234,179,8,0.8)" strokeWidth="1.5"/>
              {labelPositions.map((pos, i) => (
                <text key={i} x={pos.x} y={pos.y} textAnchor="middle" dominantBaseline="middle"
                  fill="rgba(255,255,255,0.5)" fontSize="10">{labels[i]}</text>
              ))}
            </svg>
          </div>

          {/* 指标卡 */}
          <div className="metric-grid">
            <div className="metric-item"><span className="metric-val">{m.total_pairs}</span><span className="metric-lbl">完成交易</span></div>
            <div className="metric-item"><span className="metric-val">{m.win_rate}%</span><span className="metric-lbl">胜率</span></div>
            <div className="metric-item"><span className="metric-val">{m.profit_loss_ratio}</span><span className="metric-lbl">盈亏比</span></div>
            <div className="metric-item"><span className="metric-val">{m.avg_hold_days}d</span><span className="metric-lbl">平均持仓</span></div>
          </div>
        </div>

        {/* 右: 诊断卡片 */}
        <div className="report-right">
          <h3>教练诊断</h3>
          {diagnosis.length === 0 && <p className="text-secondary">录入交易记录后生成诊断</p>}
          {diagnosis.map((d, i) => (
            <div key={i} className={`diag-card ${d.level}`}>
              <div className="diag-head">
                <span>{LEVEL_ICONS[d.level] || '⚪'}</span>
                <strong>{d.title}</strong>
              </div>
              <p>{d.detail}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
