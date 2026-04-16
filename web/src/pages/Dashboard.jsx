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
  const [editingId, setEditingId] = useState(null)
  const [editForm, setEditForm] = useState({})

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

  const handleTradeSubmitted = () => {
    setShowTradeForm(false)
    fetchData()
  }

  // 开始编辑
  const handleStartEdit = (trade) => {
    setEditingId(trade.id)
    setEditForm({
      price: trade.price,
      quantity: trade.quantity,
      stop_loss_price: trade.stop_loss_price || '',
      reason_text: trade.reason_text || '',
      traded_at: trade.traded_at?.split('T')[0] || '',
    })
  }

  // 保存编辑
  const handleSaveEdit = async (tradeId) => {
    try {
      const body = {}
      if (editForm.price) body.price = parseFloat(editForm.price)
      if (editForm.quantity) body.quantity = parseInt(editForm.quantity)
      if (editForm.stop_loss_price !== '') body.stop_loss_price = parseFloat(editForm.stop_loss_price)
      if (editForm.reason_text !== undefined) body.reason_text = editForm.reason_text
      if (editForm.traded_at) body.traded_at = editForm.traded_at + 'T09:30:00'

      await fetch(`${API}/api/trades/${tradeId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      setEditingId(null)
      fetchData()
    } catch (err) {
      console.error('编辑失败:', err)
    }
  }

  // 删除交易
  const handleDelete = async (tradeId) => {
    if (!confirm('确定要删除这笔交易记录吗？删除后持仓将自动重算。')) return
    try {
      await fetch(`${API}/api/trades/${tradeId}`, { method: 'DELETE' })
      fetchData()
    } catch (err) {
      console.error('删除失败:', err)
    }
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
              <div className={`stat-card hero-stat-card ${overview.health_score < 60 ? 'animate-pulse' : ''}`}>
                <span className="stat-label">健康度</span>
                <div className="health-score-container">
                  <span className={`stat-value mono ${overview.health_score >= 80 ? 'text-up' : 'text-down'}`}>
                    {overview.health_score} 
                  </span>
                  <span className="health-suffix">分</span>
                </div>
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
              <div key={t.id} className={`trade-item ${editingId === t.id ? 'editing' : ''}`}>
                {editingId === t.id ? (
                  /* ── 编辑模式 ── */
                  <div className="trade-edit-form">
                    <div className="trade-edit-row">
                      <span className={`trade-direction ${t.direction === 'BUY' ? 'buy' : 'sell'}`}>
                        {t.direction === 'BUY' ? '买' : '卖'}
                      </span>
                      <span className="trade-name">{t.name || t.symbol}</span>
                    </div>
                    <div className="trade-edit-fields">
                      <label>
                        <span>价格</span>
                        <input
                          type="number"
                          step="0.01"
                          value={editForm.price}
                          onChange={e => setEditForm({...editForm, price: e.target.value})}
                        />
                      </label>
                      <label>
                        <span>数量</span>
                        <input
                          type="number"
                          step="100"
                          value={editForm.quantity}
                          onChange={e => setEditForm({...editForm, quantity: e.target.value})}
                        />
                      </label>
                      <label>
                        <span>止损价</span>
                        <input
                          type="number"
                          step="0.01"
                          value={editForm.stop_loss_price}
                          onChange={e => setEditForm({...editForm, stop_loss_price: e.target.value})}
                        />
                      </label>
                      <label>
                        <span>交易日期</span>
                        <input
                          type="date"
                          value={editForm.traded_at}
                          onChange={e => setEditForm({...editForm, traded_at: e.target.value})}
                        />
                      </label>
                    </div>
                    <div className="trade-edit-reason">
                      <label>
                        <span>交易原因</span>
                        <input
                          type="text"
                          value={editForm.reason_text}
                          onChange={e => setEditForm({...editForm, reason_text: e.target.value})}
                          placeholder="交易原因"
                        />
                      </label>
                    </div>
                    <div className="trade-edit-actions">
                      <button className="btn btn-sm btn-save" onClick={() => handleSaveEdit(t.id)}>
                        ✓ 保存
                      </button>
                      <button className="btn btn-sm btn-cancel" onClick={() => setEditingId(null)}>
                        ✕ 取消
                      </button>
                    </div>
                  </div>
                ) : (
                  /* ── 展示模式 ── */
                  <>
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
                      <div className="trade-actions">
                        <button className="btn-icon" title="编辑" onClick={() => handleStartEdit(t)}>✏️</button>
                        <button className="btn-icon btn-icon-danger" title="删除" onClick={() => handleDelete(t.id)}>🗑️</button>
                      </div>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
