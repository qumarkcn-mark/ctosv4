export function formatWatchPrice(value, options = {}) {
  const num = Number(value || 0)
  if (!num) return '--'
  if (options.fixed) return num.toFixed(2)
  const fixed = num >= 100 ? num.toFixed(2) : num.toFixed(3)
  return fixed.replace(/(\.\d*?[1-9])0+$/, '$1').replace(/\.0+$/, '')
}

export function findActiveTrigger(item, currentPrice) {
  const price = Number(currentPrice || 0)
  if (!price) return null
  const triggers = item?.monitor_conditions?.triggers || []
  const active = []
  for (const trigger of triggers) {
    const level = Number(trigger.level || 0)
    if (!level) continue
    if (trigger.type === 'price_below' && price <= level) active.push({ trigger, level })
    if (trigger.type === 'price_above' && price >= level) active.push({ trigger, level })
  }
  if (!active.length) return null
  const invalid = active
    .filter((item) => isInvalidTrigger(item.trigger))
    .sort((a, b) => Math.abs(price - a.level) - Math.abs(price - b.level))
  if (invalid.length) return invalid[0].trigger
  const confirmed = active
    .filter((item) => item.trigger.type === 'price_above')
    .sort((a, b) => Math.abs(price - a.level) - Math.abs(price - b.level))
  if (confirmed.length) return confirmed[0].trigger
  return active.sort((a, b) => Math.abs(price - a.level) - Math.abs(price - b.level))[0].trigger
}

function triggerMessage(trigger) {
  return String(trigger?.message_on_trigger || '')
}

function triggerDisplayLine(trigger, fallback) {
  return triggerMessage(trigger) || fallback
}

function isInvalidTrigger(trigger) {
  if (!trigger || trigger.type !== 'price_below') return false
  const action = String(trigger.action_on_trigger || '')
  const message = triggerMessage(trigger)
  return /止损/.test(action) || /失效|止损|破位/.test(message)
}

function collectWatchLevels(item) {
  const summary = item?.reasoning_summary || {}
  const triggers = item?.monitor_conditions?.triggers || []
  const levels = []

  for (const trigger of triggers) {
    const level = Number(trigger.level || 0)
    if (!level) continue
    levels.push({
      level,
      type: trigger.type,
      trigger,
      direction: trigger.type === 'price_above' ? 'above' : 'below',
    })
  }

  const down = Number(summary.key_level_down || 0)
  if (down) levels.push({ level: down, type: 'price_below', direction: 'below' })
  const up = Number(summary.key_level_up || 0)
  if (up) levels.push({ level: up, type: 'price_above', direction: 'above' })

  return levels
    .filter((item, index, self) => self.findIndex((other) => other.level === item.level && other.type === item.type) === index)
    .sort((a, b) => a.level - b.level)
}

function nearestWatchLevel(item, price) {
  const levels = collectWatchLevels(item)
  if (!price || !levels.length) return null
  let nearest = null
  for (const level of levels) {
    const distancePct = Math.abs(price - level.level) / price
    if (!nearest || distancePct < nearest.distancePct) {
      nearest = { ...level, distancePct }
    }
  }
  return nearest
}

function formatLevelPair(summary) {
  const down = Number(summary.key_level_down || 0)
  const up = Number(summary.key_level_up || 0)
  if (down && up) return `${formatWatchPrice(down)} / ${formatWatchPrice(up)}`
  if (down) return formatWatchPrice(down)
  if (up) return formatWatchPrice(up)
  return ''
}

export function computeTacticalState(item, currentPrice, options = {}) {
  const price = Number(currentPrice || item?.price || 0)
  const summary = item?.reasoning_summary || {}
  const nearThreshold = Number(options.nearThreshold ?? 0.02)
  const activeTrigger = findActiveTrigger(item, price)
  const nearest = nearestWatchLevel(item, price)
  const distancePct = nearest?.distancePct ?? null
  const baseAction = summary.action || '观望'

  if (!price) {
    return {
      state: 'idle',
      priority: 0,
      activeTrigger: null,
      nearestLevel: nearest,
      distancePct,
      displayLine: summary.one_liner || '等待价格数据',
      actionLabel: baseAction,
    }
  }

  if (isInvalidTrigger(activeTrigger)) {
    return {
      state: 'invalid',
      priority: 3,
      activeTrigger,
      nearestLevel: nearest,
      distancePct: 0,
      displayLine: triggerDisplayLine(activeTrigger, `跌破 ${formatWatchPrice(activeTrigger.level)}，路径失效`),
      actionLabel: activeTrigger.action_on_trigger || '止损',
    }
  }

  if (activeTrigger?.type === 'price_above') {
    return {
      state: 'confirmed',
      priority: 1,
      activeTrigger,
      nearestLevel: nearest,
      distancePct: 0,
      displayLine: triggerDisplayLine(activeTrigger, `站上 ${formatWatchPrice(activeTrigger.level)}，确认增强`),
      actionLabel: activeTrigger.action_on_trigger || baseAction,
    }
  }

  if (activeTrigger?.type === 'price_below') {
    return {
      state: 'near',
      priority: 2,
      activeTrigger,
      nearestLevel: nearest,
      distancePct: 0,
      displayLine: triggerDisplayLine(activeTrigger, `触及 ${formatWatchPrice(activeTrigger.level)}，观察承接`),
      actionLabel: activeTrigger.action_on_trigger || '关注',
    }
  }

  if (nearest && distancePct <= nearThreshold) {
    const verb = nearest.direction === 'above' ? '接近压力' : '接近支撑'
    const fallbackLine = `${verb} ${formatWatchPrice(nearest.level)}，观察反应`
    return {
      state: 'near',
      priority: 2,
      activeTrigger: nearest.trigger || null,
      nearestLevel: nearest,
      distancePct,
      displayLine: nearest.trigger ? triggerDisplayLine(nearest.trigger, fallbackLine) : fallbackLine,
      actionLabel: nearest.trigger?.action_on_trigger || '关注',
    }
  }

  const levels = formatLevelPair(summary)
  return {
    state: 'idle',
    priority: 0,
    activeTrigger: null,
    nearestLevel: nearest,
    distancePct,
    displayLine: summary.one_liner || (levels ? `等待接近 ${levels}` : '等待关键位'),
    actionLabel: baseAction,
  }
}

export function computeCardState(item, currentPrice) {
  const tactical = computeTacticalState(item, currentPrice)
  const map = {
    idle: 'normal',
    near: 'alert',
    invalid: 'danger',
    confirmed: 'confirm',
  }
  return map[tactical.state] || 'normal'
}
