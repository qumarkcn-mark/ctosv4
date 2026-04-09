import './PositionList.css'

export default function PositionList({ positions }) {
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

        return (
          <div key={p.symbol} className="position-card card">
            <div className="position-header">
              <span className="position-name">{p.name || p.symbol}</span>
              <span className="position-code mono text-secondary">{p.symbol}</span>
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

            {/* 仓位占比条 */}
            {p.weight_pct !== undefined && (
              <div className="position-weight">
                <div className="weight-bar">
                  <div
                    className="weight-fill"
                    style={{ width: `${Math.min(p.weight_pct, 100)}%` }}
                  />
                </div>
                <span className="weight-label mono">{p.weight_pct}%</span>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
