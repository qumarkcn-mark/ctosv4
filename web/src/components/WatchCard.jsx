import './WatchCard.css'

export function findActiveTrigger(item, currentPrice) {
  const price = Number(currentPrice || 0)
  if (!price) return null
  const triggers = item?.monitor_conditions?.triggers || []
  for (const trigger of triggers) {
    const level = Number(trigger.level || 0)
    if (!level) continue
    if (trigger.type === 'price_below' && price <= level) return trigger
    if (trigger.type === 'price_above' && price >= level) return trigger
  }
  return null
}

export function computeCardState(item, currentPrice) {
  const activeTrigger = findActiveTrigger(item, currentPrice)
  if (!activeTrigger) return 'normal'
  if (activeTrigger.type === 'price_below' && activeTrigger.action_on_trigger === '止损') return 'danger'
  if (activeTrigger.type === 'price_above') return 'confirm'
  return 'alert'
}

function formatPrice(value) {
  const num = Number(value || 0)
  if (!num) return '--'
  return num >= 100 ? num.toFixed(2) : num.toFixed(3).replace(/0$/, '')
}

function formatPct(value) {
  const num = Number(value || 0)
  if (!Number.isFinite(num)) return '--'
  return `${num > 0 ? '+' : ''}${num.toFixed(2)}%`
}

function upcomingTrigger(item, currentPrice) {
  const price = Number(currentPrice || 0)
  if (!price) return null
  const triggers = item?.monitor_conditions?.triggers || []
  let nearest = null
  for (const trigger of triggers) {
    const level = Number(trigger.level || 0)
    if (!level) continue
    const isAhead =
      (trigger.type === 'price_below' && price > level) ||
      (trigger.type === 'price_above' && price < level)
    if (!isAhead) continue
    const distance = Math.abs(price - level) / price
    if (!nearest || distance < nearest.distance) nearest = { trigger, distance }
  }
  return nearest?.trigger || null
}

function compactTriggerMessage(trigger) {
  let text = String(trigger?.message_on_trigger || '').trim()
  if (!text) return '观察触发'
  const level = Number(trigger?.level || 0)
  if (level) {
    const levelText = formatPrice(level).replace('.', '\\.')
    text = text.replace(new RegExp(levelText, 'g'), '')
  }
  return (
    text
      .replace(/^[，,、\s]+/, '')
      .replace(/^(跌破|突破|站稳|回踩|上破|下破)[，,、]\s*/, '$1')
      .replace(/^反弹至[，,、]\s*/, '反弹')
      .replace(/^回踩至[，,、]\s*/, '回踩')
      .replace(/^回到[，,、]\s*/, '回到')
      .trim() || '观察触发'
  )
}

function cardMessage(item, currentPrice, activeTrigger, summary) {
  if (activeTrigger?.message_on_trigger) return activeTrigger.message_on_trigger
  const nextTrigger = upcomingTrigger(item, currentPrice)
  if (nextTrigger) {
    return `盯${formatPrice(nextTrigger.level)}：${compactTriggerMessage(nextTrigger)}`
  }
  return summary.one_liner || '暂无统一推演'
}

function actionClass(action) {
  const map = {
    持有: 'hold',
    加仓: 'add',
    减仓: 'reduce',
    止损: 'stop',
    观望: 'wait',
    关注: 'watch',
  }
  return map[action] || 'wait'
}

export default function WatchCard({ item, currentPrice, onClick }) {
  const price = Number(currentPrice || item.price || 0)
  const state = computeCardState(item, price)
  const trigger = findActiveTrigger(item, price)
  const summary = item.reasoning_summary || {}
  const message = cardMessage(item, price, trigger, summary)
  const action = trigger?.action_on_trigger || summary.action || '观望'
  const position = item.position
  const hasKeyLevels = Boolean(Number(summary.key_level_down || 0) || Number(summary.key_level_up || 0))
  const quoteTime = item.price_data?.quote_time || ''

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
          <span>{position.shares} 股</span>
          <span>成本 {formatPrice(position.cost)}</span>
          {position.pnl_pct !== null && position.pnl_pct !== undefined && (
            <span className={Number(position.pnl_pct) >= 0 ? 'is-up' : 'is-down'}>
              浮盈 {formatPct(position.pnl_pct)}
            </span>
          )}
        </div>
      )}

      <p className="watch-card-summary">{message}</p>

      <div className="watch-card-bottom">
        {hasKeyLevels ? (
          <div className="watch-card-levels">
            <span>▼ {formatPrice(summary.key_level_down)}</span>
            <span>▲ {formatPrice(summary.key_level_up)}</span>
          </div>
        ) : (
          <span className="watch-card-no-level">等待关键位</span>
        )}
        <span className={`watch-action-badge action-${actionClass(action)}`}>{action}</span>
      </div>
    </button>
  )
}
