import { useState, useEffect } from 'react'
import TradeForm from '../components/TradeForm.jsx'
import PositionList from '../components/PositionList.jsx'
import './Dashboard.css'

const API = ''  // Vite proxy 会转发到后端

export default function Dashboard() {
  const [positions, setPositions] = useState([])
  const [overview, setOverview] = useState(null)
  const [trades, setTrades] = useState([])
  const [showTradeForm, setShowTradeForm] = useState(false)
  const [loading, setLoading] = useState(true)

  // 加载数据
  const fetchData = async () => {
    try {
      const [posRes, tradeRes] = await Promise.all([
        fetch(`${API}/api/positions/overview`),
        fetch(`${API}/api/trades?limit=10`),
      ])
      const posData = await posRes.json()
      const tradeData = await tradeRes.json()

      setOverview(posData)
      setPositions(posData.positions || [])
      setTrades(tradeData.trades || [])
    } catch (err) {
      console.error('加载数据失败:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  // 交易提交后刷新
  const handleTradeSubmitted = () => {
    setShowTradeForm(false)
    fetchData()
  }

  if (loading) {
    return (
      <div className="dashboard-loading">
        <span className="animate-pulse">加载中...</span>
      </div>
    )
  }

  return (
    <div className="dashboard">
      {/* 顶部概览 */}
      <section className="overview-section animate-fade-in">
        <div className="overview-header">
          <h1>交易看板</h1>
          <button
            className="btn btn-primary"
            onClick={() => setShowTradeForm(!showTradeForm)}
          >
            {showTradeForm ? '✕ 关闭' : '＋ 录入交易'}
          </button>
        </div>

        {overview && (
          <div className="overview-cards">
            <div className="stat-card">
              <span className="stat-label">总市值</span>
              <span className="stat-value mono">
                ¥{overview.total_value?.toLocaleString() || '0'}
              </span>
            </div>
            <div className="stat-card">
              <span className="stat-label">持仓数量</span>
              <span className="stat-value">{overview.position_count || 0} 只</span>
            </div>
            <div className="stat-card">
              <span className="stat-label">今日交易</span>
              <span className="stat-value">{trades.length} 笔</span>
            </div>
            {overview.health_score !== undefined && (
              <div className="stat-card">
                <span className="stat-label">健康度</span>
                <span className={`stat-value mono ${overview.health_score >= 80 ? 'text-up' : 'text-down'}`}>
                  {overview.health_score} 分
                </span>
              </div>
            )}
          </div>
        )}

        {/* 预警 */}
        {overview?.warnings?.length > 0 && (
          <div className="warnings">
            {overview.warnings.map((w, i) => (
              <div key={i} className={`warning-badge ${w.severity}`}>
                ⚠ {w.message}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 交易录入表单 (折叠) */}
      {showTradeForm && (
        <section className="trade-form-section animate-fade-in">
          <TradeForm onSubmitted={handleTradeSubmitted} />
        </section>
      )}

      {/* 持仓列表 */}
      <section className="positions-section animate-fade-in" style={{ animationDelay: '0.1s' }}>
        <h2 className="section-title">当前持仓</h2>
        <PositionList positions={positions} />
      </section>

      {/* 最近交易 */}
      <section className="trades-section animate-fade-in" style={{ animationDelay: '0.2s' }}>
        <h2 className="section-title">最近交易</h2>
        {trades.length === 0 ? (
          <div className="empty-state">
            <p className="text-secondary">暂无交易记录</p>
            <button className="btn" onClick={() => setShowTradeForm(true)}>
              录入第一笔交易
            </button>
          </div>
        ) : (
          <div className="trade-list">
            {trades.map((t) => (
              <div key={t.id} className="trade-item">
                <div className="trade-info">
                  <span className={`trade-direction ${t.direction === 'BUY' ? 'buy' : 'sell'}`}>
                    {t.direction === 'BUY' ? '买' : '卖'}
                  </span>
                  <span className="trade-name">{t.name || t.symbol}</span>
                  <span className="trade-detail mono">
                    {t.quantity}股 × ¥{t.price}
                  </span>
                </div>
                <div className="trade-meta">
                  <span className="mono">¥{t.amount?.toLocaleString()}</span>
                  <span className="text-secondary">
                    {t.traded_at?.split('T')[0]}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
