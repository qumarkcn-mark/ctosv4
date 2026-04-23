import './PositionList.css'

export default function PositionList({ positions, onViewInChan }) {
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
        const pnl = p.current_price
          ? ((p.current_price - p.avg_cost) / p.avg_cost * 100)
          : null
          
        const weightPercent = p.weight !== undefined ? (p.weight * 100).toFixed(1) : 0
        const hasStopLoss = p.stop_loss_price != null && p.stop_loss_price > 0
        const distanceToStop = hasStopLoss && p.current_price 
          ? ((p.current_price - p.stop_loss_price) / p.stop_loss_price * 100)
          : null
          
        const isBroken = distanceToStop !== null && distanceToStop <= 0
        const isNear = distanceToStop !== null && distanceToStop > 0 && distanceToStop <= 3

        return (
          <div key={p.symbol} className="position-card card">
            <div className="position-header">
              <span className="position-name">{p.name || p.symbol}</span>
              <span className="position-code mono text-secondary">{p.symbol}</span>
              {onViewInChan && (
                <button
                  className="pos-view-btn"
                  title="缢论看盘"
                  onClick={() => onViewInChan(p.symbol, p.name)}
                >
                  🔮 看盘
                </button>
              )}
            </div>

            <div className="position-stats">
              <div className="position-stat">
                <span className="stat-label">持仓</span>
                <span className="stat-value mono">{p.quantity} 股</span>
              </div>
              <div className="position-stat">
                <span className="stat-label">成本</span>
                <span className="stat-value mono">¥{p.avg_cost?.toFixed(2)}</span>
              </div>
              <div className="position-stat">
                <span className="stat-label">现价</span>
                <span className="stat-value mono">
                  {p.current_price ? `¥${p.current_price.toFixed(2)}` : '—'}
                </span>
              </div>
              <div className="position-stat">
                <span className="stat-label">盈亏</span>
                <span className={`stat-value mono ${pnl > 0 ? 'text-up' : pnl < 0 ? 'text-down' : ''}`}>
                  {pnl !== null ? `${pnl > 0 ? '+' : ''}${pnl.toFixed(2)}%` : '—'}
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
            {p.weight !== undefined && (
              <div className="position-weight">
                <div className="weight-bar">
                  <div
                    className="weight-fill"
                    style={{ width: `${Math.min(weightPercent, 100)}%` }}
                  />
                </div>
                <span className="weight-label mono">{weightPercent}%</span>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
