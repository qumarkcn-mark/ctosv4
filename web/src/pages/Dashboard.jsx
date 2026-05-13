import { useState, useEffect } from 'react'
import TradeForm from '../components/TradeForm.jsx'
import TradeLedgerInbox from '../components/TradeLedgerInbox.jsx'
import PositionList from '../components/PositionList.jsx'
import { API_BASE } from '../config.js'
import { apiFetch } from '../api/client.js'
import './Dashboard.css'

export default function Dashboard({ onViewInAI, onOpenAI }) {
  const [positions, setPositions] = useState([])
  const [overview, setOverview] = useState(null)
  const [trades, setTrades] = useState([])
  const [t1Locked, setT1Locked] = useState([])   // 今日买入 T+1 锁定列表
  const [showTradeForm, setShowTradeForm] = useState(false)
  const [showLedgerInbox, setShowLedgerInbox] = useState(false)
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState(null)
  const [editForm, setEditForm] = useState({})

  // 最近交易 — 折叠 + 筛选
  const [tradesOpen, setTradesOpen] = useState(true)
  const [filterDir, setFilterDir] = useState('ALL')   // ALL | BUY | SELL
  const [filterKeyword, setFilterKeyword] = useState('')

  // 加载数据
  const fetchData = async () => {
    try {
      const [posRes, tradeRes] = await Promise.all([
        apiFetch(`${API_BASE}/positions/overview`),
        apiFetch(`${API_BASE}/trades?limit=10`),
      ])
      const posData = await posRes.json()
      const tradeData = await tradeRes.json()

      setOverview(posData)
      setPositions(posData.positions || [])
      setT1Locked(posData.t1_locked || [])
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

  const handleImportConfirmed = () => {
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

      await apiFetch(`${API_BASE}/trades/${tradeId}`, {
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
      await apiFetch(`${API_BASE}/trades/${tradeId}`, { method: 'DELETE' })
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
          <h1>交易账本</h1>
          <div className="overview-actions">
            {onOpenAI && (
              <button
                className="btn"
                onClick={onOpenAI}
              >
                AI 教练
              </button>
            )}
            <button
              className="btn btn-primary"
              onClick={() => {
                setShowLedgerInbox(!showLedgerInbox)
                if (!showLedgerInbox) setShowTradeForm(false)
              }}
            >
              {showLedgerInbox ? '关闭导入' : '截图导入'}
            </button>
            <button
              className="btn"
              onClick={() => {
                setShowTradeForm(!showTradeForm)
                if (!showTradeForm) setShowLedgerInbox(false)
              }}
            >
              {showTradeForm ? '关闭' : '录入交易'}
            </button>
          </div>
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

        {/* T+1 锁仓横幅 */}
        {t1Locked.length > 0 && (
          <div className="t1-locked-banner">
            <span className="t1-locked-icon">🔒</span>
            <div className="t1-locked-body">
              <span className="t1-locked-title">T+1 今日锁仓</span>
              <span className="t1-locked-desc">
                以下持仓今日买入，按A股T+1规则明日方可卖出：
                {t1Locked.map((s, i) => (
                  <strong key={s.symbol}>{i > 0 ? '、' : ' '}{s.name || s.symbol}</strong>
                ))}
              </span>
            </div>
          </div>
        )}

        {/* 风险预警 */}
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

      {showLedgerInbox && (
        <section className="trade-form-section animate-fade-in">
          <TradeLedgerInbox onConfirmed={handleImportConfirmed} />
        </section>
      )}

      {/* 持仓列表 */}
      <section className="positions-section animate-fade-in" style={{ animationDelay: '0.1s' }}>
        <h2 className="section-title">当前持仓</h2>
        <PositionList positions={positions} onViewInAI={onViewInAI} />
      </section>

      {/* 最近交易 */}
      <section className="trades-section animate-fade-in" style={{ animationDelay: '0.2s' }}>

        {/* ── 标题行：折叠 + 筛选 ── */}
        <div className="trades-header">
          <button
            className="trades-collapse-btn"
            onClick={() => setTradesOpen(o => !o)}
            title={tradesOpen ? '收起' : '展开'}
          >
            <span className={`collapse-arrow ${tradesOpen ? 'open' : ''}`}>›</span>
            <h2 className="section-title" style={{ margin: 0 }}>
              最近交易
              {trades.length > 0 && (
                <span className="trades-count-badge">{trades.length}</span>
              )}
            </h2>
          </button>

          {tradesOpen && trades.length > 0 && (
            <div className="trades-filter-bar">
              {/* 方向筛选 */}
              <div className="filter-dir-group">
                {['ALL', 'BUY', 'SELL'].map(d => (
                  <button
                    key={d}
                    type="button"
                    className={`filter-dir-btn ${filterDir === d ? 'active ' + d.toLowerCase() : ''}`}
                    onClick={() => setFilterDir(d)}
                  >
                    {d === 'ALL' ? '全部' : d === 'BUY' ? '买入' : '卖出'}
                  </button>
                ))}
              </div>
              {/* 关键字搜索 */}
              <div className="filter-keyword-wrap">
                <span className="filter-kw-icon">🔍</span>
                <input
                  className="filter-keyword-input"
                  type="text"
                  value={filterKeyword}
                  onChange={e => setFilterKeyword(e.target.value)}
                  placeholder="股票名/代码"
                />
                {filterKeyword && (
                  <button
                    type="button"
                    className="filter-kw-clear"
                    onClick={() => setFilterKeyword('')}
                  >✕</button>
                )}
              </div>
            </div>
          )}
        </div>

        {/* ── 列表内容 ── */}
        {tradesOpen && (() => {
          const kw = filterKeyword.trim().toLowerCase()
          const filtered = trades.filter(t => {
            if (filterDir !== 'ALL' && t.direction !== filterDir) return false
            if (kw && !((t.name || '').toLowerCase().includes(kw)) && !(t.symbol || '').toLowerCase().includes(kw)) return false
            return true
          })

          if (trades.length === 0) return (
            <div className="empty-state">
              <p className="text-secondary">暂无交易记录</p>
              <button className="btn" onClick={() => setShowTradeForm(true)}>
                录入第一笔交易
              </button>
            </div>
          )
          if (filtered.length === 0) return (
            <div className="trades-filter-empty">
              没有符合条件的交易记录
              <button type="button" className="filter-reset-btn" onClick={() => { setFilterDir('ALL'); setFilterKeyword('') }}>
                清除筛选
              </button>
            </div>
          )
          return (
            <div className="trade-list">
              {filtered.map((t) => (
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
            ))
          }
          </div>
          )
        })()}
      </section>
    </div>
  )
}
