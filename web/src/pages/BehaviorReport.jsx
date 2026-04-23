import React, { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import './BehaviorReport.css'

const API_BASE = 'http://localhost:8000/api'

const LEVEL_ICONS = { critical: '🔴', warning: '🟡', success: '🟢', info: '🔵' }

export default function BehaviorReport() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  // Scanner State
  const [isScanning, setIsScanning] = useState(false)
  const [scanProgress, setScanProgress] = useState(0)
  const [scanStatusText, setScanStatusText] = useState('')
  const [scanResults, setScanResults] = useState([])
  const [portfolioStrategy, setPortfolioStrategy] = useState('')
  const [isGeneratingStrategy, setIsGeneratingStrategy] = useState(false)

  useEffect(() => {
    fetch(`${API_BASE}/behavior/report?user_id=1`)
      .then(r => r.json())
      .then(json => { setData(json.data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  if (loading) return <div className="behavior-loading">正在扫描你的交易基因...</div>
  if (!data) return <div className="behavior-loading">暂无数据</div>

  const { discipline_score: score, metrics: m, diagnosis } = data

  const handleStartScan = async () => {
    setIsScanning(true)
    setScanResults([])
    setScanStatusText('连线系统，获取现役持仓列表...')
    setScanProgress(5)

    try {
      const posRes = await fetch(`${API_BASE}/positions?user_id=1`)
      const posData = await posRes.json()
      const positions = posData.positions || []
      
      if (positions.length === 0) {
        setScanStatusText('当前空仓，无须扫描')
        setScanProgress(100)
        setIsScanning(false)
        return
      }

      const results = []
      
      for (let i = 0; i < positions.length; i++) {
        const p = positions[i]
        setScanStatusText(`正在深度推演 ${p.name || p.symbol} (${i+1}/${positions.length})...`)
        setScanProgress(10 + Math.floor((i / positions.length) * 90))
        
        try {
          const deduceRes = await fetch(`${API_BASE}/agent/radar_deduce`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: p.symbol, user_id: 1 })
          })
          const deduceData = await deduceRes.json()
          
          if (deduceData.status === 'success') {
            results.push({
              symbol: p.symbol,
              name: p.name || p.symbol,
              quantity: p.quantity,
              cost: p.avg_cost,
              pnl_pct: p.pnl_pct,
              report: deduceData.data
            })
          }
        } catch (e) {
          console.error('Scan failed for', p.symbol, e)
        }
        
        // incremental update
        setScanResults([...results])
      }
      
      setScanStatusText(`扫描完成 (${positions.length} 只股票)，正在生成全局战报...`)
      setScanProgress(100)
      
      // 生成全局战略
      setIsGeneratingStrategy(true)
      try {
        const stratRes = await fetch(`${API_BASE}/agent/portfolio_strategy`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scan_results: results })
        })
        const stratData = await stratRes.json()
        if (stratData.status === 'success') {
          setPortfolioStrategy(stratData.data)
        }
      } catch (e) {
        console.error('Failed to generate portfolio strategy', e)
      } finally {
        setIsGeneratingStrategy(false)
        setScanStatusText('全局扫描与战略推演完毕')
      }
      
    } catch (e) {
      console.error(e)
      setScanStatusText('扫描网络异常')
    } finally {
      setIsScanning(false)
    }
  }

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

      {/* 🛡️ 现役持仓防线扫描 */}
      <div className="position-scanner-section">
        <div className="scanner-header">
          <div className="scanner-title-group">
            <h3>🛡️ 全天候持仓防线扫描 (Active Defense Scan)</h3>
            <p className="scanner-desc">调用 V5 操盘手引擎，对所有持仓股进行军事化逐一排查，杜绝违规死扛。</p>
          </div>
          <button 
            className={`scan-btn ${isScanning ? 'scanning' : ''}`} 
            onClick={handleStartScan}
            disabled={isScanning}
          >
            {isScanning ? '正在扫描...' : '🚀 启动深度体检'}
          </button>
        </div>

        {/* 进度条 */}
        {(isScanning || scanProgress > 0) && (
          <div className="scan-progress-container">
            <div className="scan-progress-bar">
              <div className="scan-progress-fill" style={{ width: `${scanProgress}%` }}></div>
            </div>
            <div className="scan-progress-text">{scanStatusText}</div>
          </div>
        )}

        {/* 📈 全局战报大屏 */}
        {portfolioStrategy && (
          <div className="portfolio-strategy-panel">
            <div className="ps-header">
              <span className="ps-icon">🎖️</span>
              <h4>总参谋部：全局仓位调度战略</h4>
            </div>
            <div className="ps-content">
              <ReactMarkdown>{portfolioStrategy}</ReactMarkdown>
            </div>
          </div>
        )}
        
        {isGeneratingStrategy && !portfolioStrategy && (
           <div className="portfolio-strategy-panel generating">
              <div className="ps-header">
                <span className="ps-icon">🤖</span>
                <h4>正在由 V5 引擎生成大元帅全局战报，请稍候...</h4>
              </div>
           </div>
        )}

        {/* 扫描结果网格 */}
        {scanResults.length > 0 && (
          <div className="scan-results-grid">
            {scanResults.map((res, idx) => {
               // 提取最核心的首要预案 (Main Plan)
               const mainPlan = res.report.pre_plans && res.report.pre_plans.length > 0 ? res.report.pre_plans[0] : null;
               const isDanger = mainPlan?.color === '🔴';
               const isSafe = mainPlan?.color === '🟢';
               const cardClass = isDanger ? 'danger' : isSafe ? 'safe' : 'warning';
               
               return (
                 <div key={idx} className={`scan-card ${cardClass}`}>
                   <div className="sc-header">
                     <span className="sc-symbol">{res.name}</span>
                     <div className="sc-stats">
                       <span className="sc-qty">{res.quantity}股</span>
                       <span className="sc-cost">| 成本 {res.cost?.toFixed(2)}</span>
                     </div>
                   </div>
                   
                   <div className={`sc-pnl ${res.pnl_pct >= 0 ? 'up' : 'down'}`}>
                     PnL: {res.pnl_pct > 0 ? '+' : ''}{res.pnl_pct}%
                   </div>
                   
                   <div className="sc-body">
                     <div className="sc-diag">{res.report.diagnosis || '暂无定调'}</div>
                   </div>
                   
                   {/* AI 机械指令 */}
                   {mainPlan ? (
                     <div className="sc-action-box">
                        <div className="sc-cmd-label">AI COMMAND</div>
                        <div className="sc-cmd-text">[{mainPlan.machine_action || 'HOLD'}]</div>
                        {res.report.core_defense && (
                          <div className="sc-defense">🛡️ {res.report.core_defense}</div>
                        )}
                     </div>
                   ) : (
                     <div className="sc-action-box">
                        <div className="sc-cmd-label">AI COMMAND</div>
                        <div className="sc-cmd-text">[MONITOR]</div>
                        {res.report.core_defense && (
                          <div className="sc-defense">🛡️ {res.report.core_defense}</div>
                        )}
                     </div>
                   )}
                 </div>
               )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
