import './WatchCard.css'
import { computeTacticalState, formatWatchPrice } from '../utils/watchboardState.js'

const formatPrice = (value) => formatWatchPrice(value, { fixed: true })
const formatLevel = formatWatchPrice

function formatPct(value) {
  const num = Number(value || 0)
  if (!Number.isFinite(num)) return '--'
  return `${num > 0 ? '+' : ''}${num.toFixed(2)}%`
}

function actionClass(action) {
  const normalized = String(action || '').replace(/^考虑/, '')
  if (normalized.includes('止损')) return 'stop'
  if (normalized.includes('减仓') || normalized.includes('锁利')) return 'reduce'
  if (normalized.includes('加仓') || normalized.includes('建仓')) return 'add'
  if (normalized.includes('持有')) return 'hold'
  if (normalized.includes('关注')) return 'watch'
  if (normalized.includes('观望')) return 'wait'
  const map = {
    持有: 'hold',
    加仓: 'add',
    减仓: 'reduce',
    止损: 'stop',
    观望: 'wait',
    关注: 'watch',
  }
  return map[normalized] || 'wait'
}

export default function WatchCard({ item, currentPrice, onClick }) {
  const price = Number(currentPrice || item.price || 0)
  const tactical = computeTacticalState(item, price)
  const state = tactical.state
  const summary = item.reasoning_summary || {}
  const message = tactical.displayLine || summary.one_liner || '暂无统一推演'
  const action = tactical.actionLabel || summary.action || '观望'
  const position = item.position
  const hasRangeLevels = Boolean(Number(summary.key_level_down || 0) && Number(summary.key_level_up || 0))
  const hasAnyKeyLevel = Boolean(Number(summary.key_level_down || 0) || Number(summary.key_level_up || 0))
  const quoteTime = item.price_data?.quote_time || ''
  const pnlPct = Number(position?.pnl_pct ?? 0)
  const pnlLabel = pnlPct >= 0 ? '盈' : '亏'

  const minL = Number(summary.key_level_down || 0)
  const maxL = Number(summary.key_level_up || 0)
  let dotPercentage = 50
  if (minL && maxL && maxL > minL) {
    dotPercentage = Math.max(0, Math.min(100, ((price - minL) / (maxL - minL)) * 100))
  }

  return (
    <button className={`watch-card state-${state}`} onClick={onClick} type="button">
      <div className="watch-card-top">
        <div>
          <strong>{item.name || item.symbol}</strong>
          <span>{item.symbol}{quoteTime ? ` · ${quoteTime}` : ''}</span>
        </div>
        <div className="watch-card-price">
          <strong>{formatPrice(price)}</strong>
          <span className={Number(item.change_pct || 0) >= 0 ? 'is-up' : 'is-down'}>
            {formatPct(item.change_pct)}
          </span>
        </div>
      </div>

      {position && (
        <div className="watch-card-position">
          <span className="position-main">{position.shares}股 @{formatPrice(position.cost)}</span>
          {position.pnl_pct !== null && position.pnl_pct !== undefined && (
            <span className={`position-pnl ${Number(position.pnl_pct) >= 0 ? 'is-up' : 'is-down'}`}>
              {pnlLabel} {formatPct(position.pnl_pct)}
            </span>
          )}
        </div>
      )}

      <p className="watch-card-summary">{message}</p>

      {hasRangeLevels && (
        <div className="watch-card-range-bar">
          <span>▼ {formatLevel(minL)}</span>
          <div className="range-bar-track">
            <div className="range-bar-fill" style={{ left: '0%', width: `${dotPercentage}%` }} />
            <div className="range-bar-dot" style={{ left: `${dotPercentage}%` }} />
          </div>
          <span>▲ {formatLevel(maxL)}</span>
        </div>
      )}

      <div className="watch-card-bottom">
        {!hasRangeLevels && (
          <div className="watch-card-levels">
            {minL ? <span>▼ {formatLevel(minL)}</span> : null}
            {maxL ? <span>▲ {formatLevel(maxL)}</span> : null}
            {!hasAnyKeyLevel ? <span className="watch-card-no-level">等待关键位</span> : null}
          </div>
        )}
        <span className={`watch-action-badge action-${actionClass(action)}`}>{action}</span>
      </div>
    </button>
  )
}
