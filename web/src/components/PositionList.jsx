import './PositionList.css'

function formatMoney(value) {
  if (value === null || value === undefined || value === '') return '—'
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  return `¥${num.toFixed(2)}`
}

function formatQuantity(value) {
  if (value === null || value === undefined || value === '') return '—'
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  return `${num.toLocaleString()} 股`
}

function formatPercent(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  return `${num > 0 ? '+' : ''}${num.toFixed(2)}%`
}

function normalizeWeight(value) {
  if (value === null || value === undefined || value === '') return 0
  const num = Number(value)
  if (!Number.isFinite(num)) return 0
  return num <= 1 ? num * 100 : num
}

export default function PositionList({ positions, onViewInAI }) {
  if (!positions || positions.length === 0) {
    return (
      <div className="position-empty">
        <p className="text-secondary">暂无持仓</p>
      </div>
    )
  }

  return (
    <div className="position-grid">
      {positions.map((p) => {
        const pnl = p.current_price && p.avg_cost
          ? ((p.current_price - p.avg_cost) / p.avg_cost * 100)
          : null

        const weightPercent = normalizeWeight(p.weight)
        const hasStopLoss = p.stop_loss_price != null && p.stop_loss_price > 0
        const distanceToStop = hasStopLoss && p.current_price 
          ? ((p.current_price - p.stop_loss_price) / p.stop_loss_price * 100)
          : null
          
        const isBroken = distanceToStop !== null && distanceToStop <= 0
        const isNear = distanceToStop !== null && distanceToStop > 0 && distanceToStop <= 3
        return (
          <article key={p.symbol} className="position-card card">
            <div className="position-header">
              <div className="position-title-group">
                <span className="position-name">{p.name || p.symbol}</span>
                {onViewInAI && (
                  <button
                    className="pos-view-btn"
                    title="AI 结构问答"
                    onClick={() => onViewInAI(p.symbol, p.name)}
                  >
                    看盘
                  </button>
                )}
              </div>
              <span className="position-code mono text-secondary">{p.symbol}</span>
            </div>

            <div className="position-stats" aria-label={`${p.name || p.symbol} 持仓数据`}>
              <div className="position-stat">
                <span className="stat-label">持仓</span>
                <span className="stat-value mono">{formatQuantity(p.quantity)}</span>
              </div>
              <div className="position-stat">
                <span className="stat-label">成本</span>
                <span className="stat-value mono">{formatMoney(p.avg_cost)}</span>
              </div>
              <div className="position-stat">
                <span className="stat-label">现价</span>
                <span className="stat-value mono">{formatMoney(p.current_price)}</span>
              </div>
              <div className="position-stat">
                <span className="stat-label">盈亏</span>
                <span className={`stat-value mono ${pnl > 0 ? 'text-up' : pnl < 0 ? 'text-down' : ''}`}>
                  {pnl !== null ? formatPercent(pnl) : '—'}
                </span>
              </div>
            </div>

            {/* 止损看门狗 (ATR) */}
            {hasStopLoss && (
              <div className={`stop-loss-watchdog ${isBroken ? 'danger' : isNear ? 'warning' : 'safe'}`}>
                <div className="watchdog-header">
                  <span className="label">🎯 ATR 止损线</span>
                  <span className="value mono">¥{p.stop_loss_price.toFixed(2)}</span>
                </div>
                {distanceToStop !== null && (
                  <div className="watchdog-status">
                    {isBroken ? '⚠️ 已击穿止损' : isNear ? `⚠ 逼近止损 (${distanceToStop.toFixed(2)}%)` : `距止损: ${distanceToStop.toFixed(2)}%`}
                  </div>
                )}
              </div>
            )}

            {/* 仓位占比条 */}
            {p.weight !== null && p.weight !== undefined && (
              <div className="position-weight">
                <div className="weight-bar">
                  <div
                    className="weight-fill"
                    style={{ width: `${Math.min(weightPercent, 100)}%` }}
                  />
                </div>
                <span className="weight-label mono">{weightPercent.toFixed(1)}%</span>
              </div>
            )}
          </article>
        )
      })}
    </div>
  )
}
